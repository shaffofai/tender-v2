# -*- coding: utf-8 -*-
"""Buyurtmachi fayllarini sherik API sidan o'zimiz so'rash (2026-10-22).

    GET <BUYURTMACHI_API_URL>/<tender_id>          (Basic Auth — TENDER_API_*)
    → {"success": true, "data": [{"file_id", "type", "link", "tender_id"}, ...]}

MUAMMO. Sherik ba'zi tenderlar uchun buyurtmachi fayllarini (`consulting`)
bizga umuman yubormaydi, ishtirokchi fayllari esa keladi va status 4
(«buyurtmachi fayllari topilmadi») oladi — 2026-10-22 da 17 tender, 176 fayl.

YECHIM. Shablon yo'q bo'lsa fayllarni o'zimiz so'raymiz:
  • `shablonlarni_ol` — pipeline status 4 dan OLDIN chaqiradi. Kelgan fayllar
    sherik POST qilganlar bilan AYNAN bir yo'ldan o'tadi
    (`intake.elementni_tekshir` → `store.elementni_yoz`): host oq ro'yxati,
    `type`, havola uzunligi, `file_id` noyobligi;
  • `supur` — worker bo'sh siklida: so'nggi `BUYURTMACHI_API_KUN` kundagi
    shablonsiz status 4 fayllarning tenderlarini qayta so'raydi. Shablon
    tushsa `shablon_kelganini_tekshir` fayllarni tiriltiradi (N1) va haqiqiy
    verdikt chiqadi.

Bitta tender `BUYURTMACHI_API_INTERVAL` soniyada ko'pi bilan bir marta
so'raladi (jarayon xotirasida). API xatosi faylni YIQITMAYDI: status 4 yo'li
o'zgarishsiz qoladi, keyingi `supur` qayta so'raydi (OLTIN QOIDA — texnik xato
hech qachon status 2 bermaydi). Kalit rad etilsa (401/403) — `error` satri,
audit bot Telegram'ga yuboradi.
"""

import re
import time
from urllib.parse import urlparse

import httpx
import psycopg

from app import config
from app.api import intake, store
from app.db.schema import JOBS_TABLE, TEMPLATES_TABLE, _kalit_join
from app.log import log
from app.worker.queue import KUTISH_BELGI, STATUS_TEXNIK, shablon_kelganini_tekshir

#: Fon qidiruvi bazaga shuncha soniyada bir marta qaraydi (bo'sh sikl — 5 s).
SUPUR_ORALIQ = 60
#: Bitta qidiruvda ko'pi bilan shuncha tender so'raladi.
SUPUR_TENDER = 10


class _Holat:
    def __init__(self):
        self.soroldi = {}          # tender_id → time.monotonic() (oxirgi so'rov)
        self.supurildi = None      # oxirgi `supur` (time.monotonic())
        self.ogohlandi = False     # sozlama xatosi bir marta aytiladi


_H = _Holat()


class _Mijoz:
    """Bitta `httpx.Client` — ulanish qayta ishlatiladi. Testda almashtiriladi."""
    _c = None

    @classmethod
    def ol(cls):
        if cls._c is None:
            s = config.buyurtmachi_api()
            cls._c = httpx.Client(timeout=s.timeout, auth=(s.login, s.parol),
                                  headers={"User-Agent": "tender-ai-worker/1.0",
                                           "Accept": "application/json"})
        return cls._c


def _tozala():
    """Testlar uchun: holatni boshlang'ich ko'rinishga qaytaradi."""
    _H.soroldi.clear()
    _H.supurildi = None
    _H.ogohlandi = False
    _Mijoz._c = None


def _yoqilganmi():
    """URL berilgan va xavfsiz bo'lsa True. Sozlama xatosi bir marta aytiladi."""
    s = config.buyurtmachi_api()
    if not s.url:
        return False                       # o'chiq (BUYURTMACHI_API_URL=0 yoki manba yo'q)
    xato = None
    host = (urlparse(s.url).hostname or "").lower()
    # Sender bilan bir xil qoida: Basic Auth parolni SHIFRLAMAYDI — faqat
    # https; yagona istisno loopback (mahalliy sinov).
    if not s.url.lower().startswith("https://") and host not in ("127.0.0.1", "localhost", "::1"):
        xato = "faqat https:// bo'lishi mumkin (parol ochiq ketmasin)"
    elif not s.login or not s.parol:
        xato = "TENDER_API_LOGIN / TENDER_API_PAROL yo'q"
    if xato is None:
        return True
    if not _H.ogohlandi:
        _H.ogohlandi = True
        log(f"[buyurtmachi API] o'chiq: {xato}", "warning")
    return False


def _xato_matni(exc):
    m = f"{type(exc).__name__}: {exc}"
    return re.sub(r"://[^@\s]*@", "://***@", m)[:300]


def sora(tender_id):
    """Bitta so'rov. Qaytadi: ("ok", [element, ...]) | ("yoq", []) | ("xato", matn)."""
    url = f"{config.buyurtmachi_api().url}/{int(tender_id)}"
    try:
        r = _Mijoz.ol().get(url)
    except httpx.HTTPError as exc:
        return "xato", _xato_matni(exc)
    if r.status_code == 404:
        return "yoq", []
    if r.status_code != 200:
        return "xato", f"HTTP {r.status_code}: {(r.text or '')[:200]}"
    try:
        tana = r.json()
    except ValueError:
        return "xato", f"javob JSON emas: {(r.text or '')[:200]}"
    data = tana.get("data") if isinstance(tana, dict) else None
    if not isinstance(tana, dict) or tana.get("success") is not True \
            or not isinstance(data if data is not None else [], list):
        return "xato", f"kutilmagan javob: {str(tana)[:200]}"
    return ("ok" if data else "yoq"), (data or [])


def shablonlarni_ol(conn, tender_id):
    """Tender uchun buyurtmachi fayllarini API dan olib `templates` ga yozadi.

    Qaytadi: YANGI yozilgan shablonlar soni (0 — o'chiq, yaqinda so'ralgan,
    sherikda yo'q yoki xato). Yangi shablon bo'lsa shu tenderning shablonsiz
    status 4 fayllari darhol tiriltiriladi (`shablon_kelganini_tekshir`).
    Faylning o'ziga (joriy claim) tegmaydi. Baza uzilishidan boshqa hech
    qanday xato tashqariga chiqmaydi — chaqiruvchi odatdagi yo'lda davom etadi.
    """
    if not _yoqilganmi():
        return 0
    tid = int(tender_id)
    hozir = time.monotonic()
    oxirgi = _H.soroldi.get(tid)
    if oxirgi is not None and hozir - oxirgi < config.buyurtmachi_api().interval:
        return 0
    _H.soroldi[tid] = hozir
    try:
        conn.commit()                      # HTTP kutilganda ochiq tranzaksiya qolmasin
        holat, data = sora(tid)
        if holat == "xato":
            if data.startswith(("HTTP 401", "HTTP 403")):
                # Birinchi satr O'ZGARMAS — bot bir xil xatoni 10 daqiqada bir aytadi.
                log(f"[buyurtmachi API] kalit rad etildi — TENDER_API_LOGIN / "
                    f"TENDER_API_PAROL ni tekshiring\n  tender_id={tid}: {data}", "error")
            else:
                log(f"[buyurtmachi API] tender_id={tid}: {data}", "warning")
            return 0
        if holat == "yoq":
            log(f"[buyurtmachi API] tender_id={tid}: sherikda ham buyurtmachi fayli yo'q")
            return 0

        yangi, rad = [], []
        for el in data:
            if not isinstance(el, dict):
                rad.append("element obyekt emas")
                continue
            toza, xato = intake.elementni_tekshir(
                {**el, "tender_id": el.get("tender_id", tid), "user_type": "consulting"})
            if xato is None and toza["tender_id"] != tid:
                xato = f"boshqa tender ({toza['tender_id']})"
            if xato:
                rad.append(f"file_id={el.get('file_id')}: {xato}")
                continue
            natija, _id, xato = store.elementni_yoz(conn, toza)
            if natija == "qabul_qilindi":
                yangi.append(toza["type"])
            elif natija == "xato":
                rad.append(f"file_id={toza['file_id']}: {xato}")
        conn.commit()
        if rad:
            log(f"[buyurtmachi API] tender_id={tid}: {len(rad)} ta element yozilmadi — "
                f"{'; '.join(rad)[:400]}", "warning")
        if yangi:
            log(f"[buyurtmachi API] tender_id={tid}: {len(yangi)} ta shablon olindi "
                f"({', '.join(sorted(yangi))})", hodisa="shablon_olindi",
                qoshimcha={"tender_id": tid, "turlar": sorted(yangi)})
            shablon_kelganini_tekshir(conn)
        return len(yangi)
    except (psycopg.OperationalError, psycopg.InterfaceError):
        raise
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:                  # pragma: no cover
            pass
        log(f"[buyurtmachi API] tender_id={tid}: {_xato_matni(exc)}", "warning")
        return 0


def supur(conn):
    """Bo'sh sikl: shablonsiz status 4 fayllarning tenderlarini qayta so'raydi.

    `SUPUR_ORALIQ` da bir marta, `SUPUR_TENDER` tagacha tender — eng yangi
    fayllisi birinchi; yaqinda so'ralganlar SQL da chetlanadi (aks holda ular
    LIMIT ni egallab, qolganlari hech qachon so'ralmasdi).
    Qaytadi: yangi yozilgan shablonlar soni. Baza uzilishidan boshqa xato
    tashqariga chiqmaydi — worker sikli yiqilmasin.
    """
    if not _yoqilganmi():
        return 0
    hozir = time.monotonic()
    if _H.supurildi is not None and hozir - _H.supurildi < SUPUR_ORALIQ:
        return 0
    _H.supurildi = hozir
    try:
        return _supur(conn, hozir)
    except (psycopg.OperationalError, psycopg.InterfaceError):
        raise
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:                  # pragma: no cover
            pass
        log(f"[buyurtmachi API] fon qidiruvi: {_xato_matni(exc)}", "warning")
        return 0


def _supur(conn, hozir):
    s = config.buyurtmachi_api()
    yaqinda = [t for t, v in _H.soroldi.items() if hozir - v < s.interval]
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT j.tender_id
            FROM {JOBS_TABLE} j JOIN jobs_state s ON {_kalit_join('j', 's')}
            WHERE j.status = %s
              AND s.dead_at IS NOT NULL
              AND s.dead_reason LIKE %s
              AND j.created_at > now() - make_interval(days => %s::int)
              AND NOT (j.tender_id = ANY(%s::bigint[]))
              AND NOT EXISTS (SELECT 1 FROM {TEMPLATES_TABLE} t
                              WHERE t.tender_id = j.tender_id AND t.type = j.type
                                AND t.deleted_at IS NULL)
            GROUP BY j.tender_id
            ORDER BY max(j.created_at) DESC
            LIMIT %s
            """, (STATUS_TEXNIK, KUTISH_BELGI + "%", s.kun, yaqinda, SUPUR_TENDER))
        tenderlar = [r[0] for r in cur.fetchall()]
    conn.commit()
    return sum(shablonlarni_ol(conn, t) for t in tenderlar)
