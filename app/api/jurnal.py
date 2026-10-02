# -*- coding: utf-8 -*-
"""So'rov jurnalining HTTP qismi: yozish (oraliq qatlam) va o'qish (`GET /jurnal`).

YOZISH — `JurnalOraliq`, sof ASGI oraliq qatlam. Ishlovchilarga TEGMAYDI:
`receive` va `send` ni o'rab, o'tayotgan xabarlarni kuzatadi va so'rov
tugagach `app.jurnal.qoy` ga bitta `sorov` qatori beradi (401 bo'lsa — yana
bitta `kirish` qatori). Hech qanday `await` va hech qanday sarlavha
qo'shmaydi, bazaga tegmaydi — javob baytma-bayt avvalgidek.

O'QISH — `royxatni_ol`, `yozuvni_ol`. O'z kalitlari (JURNAL_LOGIN /
JURNAL_PAROL) va har chaqiruvda O'Z qisqa umrli ulanishi bilan.
"""

import base64
import datetime
import hmac
import json
import time
import uuid

import psycopg
from fastapi.responses import JSONResponse

from app import config
from app import jurnal as _jurnal
from app.db import DATABASE_URL
from app.log import log

# ---------------------------------------------------------------------------
# Yozish — oraliq qatlam
# ---------------------------------------------------------------------------
#: Har yo'nalishda saqlanadigan tana, bayt. Sherikning odatiy so'rovi 1–3 fayl
#: (bir necha yuz bayt); chegara — himoya: API so'rov hajmini cheklamaydi.
SAQLASH_MAX = 64 * 1024

_PREFIKS = "/api-v2/tender-v2"

#: So'rov sarlavhalari — OQ RO'YXAT (hammasi emas). User-Agent alohida ustunda,
#: Authorization dan faqat sxemasi qoladi (`_sarlavhalar`).
_SARLAVHALAR = ("content-type", "content-length", "accept", "accept-language", "origin",
                "host", "x-forwarded-for", "x-real-ip", "referer")
_SXEMALAR = ("basic", "bearer", "digest", "negotiate")


def _qisqa_yol(yol):
    """Yo'lning prefikssiz shakli: har yo'l ikki shaklda keladi (`app.py` izohi)."""
    return yol[len(_PREFIKS):] if yol.startswith(_PREFIKS + "/") else yol


def _sarlavhalar(h):
    out = {k: h[k] for k in _SARLAVHALAR if k in h}
    if "referer" in out:
        out["referer"] = out["referer"].split("?", 1)[0]
    auth = h.get("authorization")
    if auth is not None:
        # Sxema tanish bo'lsagina ko'rsatiladi: `Authorization: <kalitning o'zi>`
        # deb yuborilgan qiymatning birinchi so'zi — sirning bir qismi.
        sxema, ajratgich, _ = auth.partition(" ")
        out["authorization"] = (f"{sxema} {_jurnal.YASHIRILDI}"
                                if ajratgich and sxema.lower() in _SXEMALAR
                                else _jurnal.YASHIRILDI)
    return out


def _davo_login(auth):
    """DA'VO qilingan login — `auth._auth_ok` qanday dekodlasa, shunday.

    Bu tekshirilmagan qiymat (tekshirilgani — `tasdiqlangan` ustuni): 404 ga
    yuborilgan `Basic base64('golden:x')` ham `golden` bo'lib yoziladi.
    Dekodlangan qiymatda `:` bo'lmasa HECH NARSA yozilmaydi — aks holda
    `Basic base64(<yalang'och kalit>)` yuborgan mijozning kaliti `login`
    ustunida butunligicha qolardi.
    """
    if not auth or not auth.lower().startswith("basic "):
        return None
    try:
        xom = base64.b64decode(auth[6:].strip(), validate=True).decode("utf-8", "replace")
    except Exception:
        return None
    login, ikki_nuqta, _ = xom.partition(":")
    return login if ikki_nuqta else None


def _rad_sababi(tana):
    """401 javobidagi `xato` matni — sherikning noto'g'ri paroli bilan bizning
    sozlanmagan serverimizni («server sozlanmagan») farqlash uchun."""
    try:
        xato = json.loads(tana).get("xato")
    except Exception:
        return None
    return xato if isinstance(xato, str) else None


class _Tana:
    """Bir yo'nalishdagi tana: jami bayt soni va boshidan `SAQLASH_MAX` gacha
    bo'laklar (nusxa olinmaydi — bo'lakka havola saqlanadi)."""

    def __init__(self):
        self.bayt = 0
        self.bolaklar = []

    def qosh(self, bolak):
        if self.bayt < SAQLASH_MAX:
            self.bolaklar.append(bolak)
        self.bayt += len(bolak)

    @property
    def qirqildi(self):
        return self.bayt > SAQLASH_MAX

    def saqlangan(self):
        return b"".join(self.bolaklar)[:SAQLASH_MAX]


class _Qayd:
    """Bitta so'rovning qaydi — oraliq qatlam to'ldiradi, oxirida qatorlarga aylanadi.

    `keldi`, `ketdi` va `yoz` istisno KO'TARMAYDI: jurnaldagi xato so'rovni
    buzmasin.
    """

    def __init__(self, scope):
        self.boshlandi = time.perf_counter()
        self.tugadi = None                       # oxirgi javob bayti ketgan payt
        self.vaqt = _jurnal.hozir()              # so'rov KELGAN payt — `yaratildi`
        self.usul = scope["method"]
        self.yol = scope["path"]
        self.sorov_qatori = scope.get("query_string", b"").decode("latin-1")
        # Takror sarlavhadan BIRINCHISI olinadi — ishlovchilar ham shunday
        # o'qiydi (`request.headers.get` — `_auth_ok`, `_ruxsat`). Oxirgisi
        # olinsa, ikkita `Authorization` yuborgan mijozning `login` i
        # tekshirilgan kalitniki emas, ikkinchi (soxta) sarlavhaniki bo'lib,
        # `tasdiqlangan = true` bilan yozilardi.
        self.sarlavha = {}
        for k, q in scope.get("headers", ()):
            self.sarlavha.setdefault(k.decode("latin-1").lower(), q.decode("latin-1"))
        mijoz = scope.get("client")
        self.ip = mijoz[0] if mijoz else None
        self.belgi = {"id": uuid.uuid4().hex, "tasdiqlangan": False}
        self.holat = None
        self.istisno = None
        self.sorov = _Tana()
        self.javob = _Tana()

    def keldi(self, xabar):
        try:
            if xabar["type"] == "http.request":
                self.sorov.qosh(xabar.get("body", b""))
        except Exception:
            pass

    def ketdi(self, xabar):
        """True — bu javobning OXIRGI xabari."""
        try:
            if xabar["type"] == "http.response.start":
                self.holat = xabar["status"]
            elif xabar["type"] == "http.response.body":
                self.javob.qosh(xabar.get("body", b""))
                return not xabar.get("more_body", False)
        except Exception:
            pass
        return False

    def qatorlar(self):
        qisqa = _qisqa_yol(self.yol)
        holat = self.holat
        if holat is None and self.istisno:
            # Ishlovchi yiqildi: 500 ni bizdan TASHQARIDAGI qatlam
            # (ServerErrorMiddleware) beradi — bu yerdan javob o'tmaydi.
            holat = 500
        if self.usul == "GET" and qisqa == "/health" and holat == 200:
            # Konteyner healthcheck'i har 30 s da — kuniga ~2 900 ma'nosiz qator.
            # Sog'lom EMAS javob (503) yoziladi.
            return []

        jurnalmi = qisqa == "/jurnal" or qisqa.startswith("/jurnal/")
        # Kalit TEKSHIRILGANMI. `/jurnal` da buni o'qish kodi o'zi belgilaydi
        # (`_ruxsat`); `/check` ishlovchisiga tegilmaydi, shuning uchun u
        # natijadan aniqlanadi: ishlovchi 401 dan boshqa javobni faqat
        # `_auth_ok` o'tgandan keyin beradi.
        tasdiqlangan = (holat is not None and holat != 401 and not self.istisno
                        and (self.belgi["tasdiqlangan"]
                             or (self.usul == "POST" and qisqa == "/check")))
        if self.istisno or (holat or 0) >= 500:
            daraja = "error"
        elif holat is None or holat >= 400:
            daraja = "warning"
        else:
            daraja = "info"

        qoshimcha = {"sorov_bayt": self.sorov.bayt, "javob_bayt": self.javob.bayt}
        if self.sorov.qirqildi:
            qoshimcha["sorov_qirqildi"] = True
        if self.javob.qirqildi and not jurnalmi:
            qoshimcha["javob_qirqildi"] = True
        if self.istisno:
            qoshimcha["istisno"] = self.istisno      # TURI; xabari emas

        umumiy = {"yaratildi": self.vaqt, "manba": "api", "sorov_id": self.belgi["id"],
                  "usul": self.usul, "yol": self.yol, "holat_kodi": holat, "ip": self.ip,
                  "login": _davo_login(self.sarlavha.get("authorization")),
                  "tasdiqlangan": tasdiqlangan,
                  "user_agent": self.sarlavha.get("user-agent")}
        tugadi = self.tugadi or time.perf_counter()
        qatorlar = [dict(
            umumiy, tur="sorov", hodisa="http", daraja=daraja,
            sorov_qatori=self.sorov_qatori or None,
            davomiylik_ms=int((tugadi - self.boshlandi) * 1000),
            sorov_sarlavhalari=_sarlavhalar(self.sarlavha),
            sorov_tanasi=self.sorov.saqlangan() or None,
            # `/jurnal` javobi — jurnalning o'zi: saqlansa har o'qish jurnalni
            # o'z nusxasi bilan shishirardi. Faqat hajmi (`javob_bayt`).
            javob_tanasi=None if jurnalmi else (self.javob.saqlangan() or None),
            qoshimcha=qoshimcha)]
        if holat == 401:
            qatorlar.append(dict(
                umumiy, tur="kirish", hodisa="kirish_rad", daraja="warning",
                qoshimcha={"sabab": _rad_sababi(self.javob.saqlangan())}))
        return qatorlar

    def yoz(self):
        try:
            for qator in self.qatorlar():
                _jurnal.qoy(qator)
        except Exception:
            pass


class JurnalOraliq:
    """Har HTTP so'rovni jurnalga yozadi (`GET /health` → 200 dan tashqari).

    Jurnal yoqilmagan bo'lsa (`jurnal.boshla` chaqirilmagan, JURNAL_YOQILGAN=0)
    so'rov to'g'ridan-to'g'ri o'tadi.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not _jurnal.faol():
            await self.app(scope, receive, send)
            return
        try:
            qayd = _Qayd(scope)
        except Exception:
            # Qayd yasalmadi — so'rov jurnalsiz o'tadi, lekin O'TADI.
            await self.app(scope, receive, send)
            return

        async def qabul():
            xabar = await receive()
            qayd.keldi(xabar)
            return xabar

        async def jonat(xabar):
            oxirgi = qayd.ketdi(xabar)
            await send(xabar)
            if oxirgi:
                qayd.tugadi = time.perf_counter()

        belgi = _jurnal.JORIY_SOROV.set(qayd.belgi)
        try:
            await self.app(scope, qabul, jonat)
        except Exception as exc:
            qayd.istisno = type(exc).__name__
            raise
        finally:
            _jurnal.JORIY_SOROV.reset(belgi)
            qayd.yoz()


# ---------------------------------------------------------------------------
# O'qish — GET /jurnal, GET /jurnal/{jid}
# ---------------------------------------------------------------------------
#: Bundan qisqa JURNAL_PAROL «yo'q» deb sanaladi (503). Yo'l edge orqali
#: internetdan ochiq, urinishlar cheklanmaydi, solishtirish esa tez (hmac) —
#: qisqa parolni tanlab topish mumkin. Himoyalanayotgan narsa — sherikning har
#: so'rovi, IP lar va log satrlari. `openssl rand -hex 24` — 48 belgi.
PAROL_MIN = 24
LIMIT_STANDART, LIMIT_MAKS = 100, 500

_TURLAR = ("sorov", "kirish", "log", "amal")
_MANBALAR = ("api", "worker", "sender")
_DARAJALAR = ("info", "warning", "error")
_FILTRLAR = ("dan", "gacha", "tur", "manba", "daraja", "holat_kodi", "login", "sorov_id",
             "yol", "limit", "oldin_id", "keyin_id")

#: Ro'yxat TANALARSIZ (faqat uzunligi) va `xabar` 500 belgigacha: 500 qator ×
#: ikkita 64 KiB tana API ning 512 MB chegarasiga yetardi. To'liq yozuv —
#: `/jurnal/{jid}`.
_ROYXAT = ("id", "yaratildi", "tur", "hodisa", "daraja", "manba", "sorov_id", "usul", "yol",
           "sorov_qatori", "holat_kodi", "davomiylik_ms", "ip", "login", "tasdiqlangan",
           "xabar", "sorov_tanasi_uzunligi", "javob_tanasi_uzunligi", "qoshimcha")
_ROYXAT_SQL = ("id, yaratildi, tur, hodisa, daraja, manba, sorov_id, usul, yol, "
               "sorov_qatori, holat_kodi, davomiylik_ms, ip, login, tasdiqlangan, "
               "left(xabar, 500), length(sorov_tanasi), length(javob_tanasi), qoshimcha")
_TOLIQ = ("id",) + _jurnal.USTUNLAR


def _xato(kod, matn):
    return JSONResponse({"xato": matn}, status_code=kod)


def _kalit_xatosi(sarlavha, s):
    """Rad sababi yoki None. Solishtirish — UTF-8 BAYTLARDA: `hmac.compare_digest`
    ASCII bo'lmagan `str` da TypeError beradi (so'rov 500 bilan tugardi)."""
    if not sarlavha.lower().startswith("basic "):
        return "Basic Auth kerak"
    try:
        xom = base64.b64decode(sarlavha[6:].strip(), validate=True)
    except Exception:
        return "Authorization sarlavhasi buzuq"
    login, _, parol = xom.partition(b":")
    # Ikkalasi HAR DOIM solishtiriladi — login to'g'riligi vaqt bo'yicha sezilmasin.
    login_mos = hmac.compare_digest(login, s.login.encode("utf-8"))
    parol_mos = hmac.compare_digest(parol, s.parol.encode("utf-8"))
    return None if login_mos and parol_mos else "login yoki parol noto'g'ri"


def _ruxsat(request):
    """None — ruxsat bor; aks holda tayyor rad javobi (503 yoki 401).

    Rad etilgan urinish jurnalga `kirish` qatori bo'lib tushadi — uni oraliq
    qatlam yozadi (har 401 uchun).
    """
    s = config.jurnal()
    if not s.login or len(s.parol) < PAROL_MIN:
        return _xato(503, "jurnalni o'qish sozlanmagan: JURNAL_LOGIN / JURNAL_PAROL "
                          "(parol kamida %d belgi)" % PAROL_MIN)
    sabab = _kalit_xatosi(request.headers.get("authorization", ""), s)
    if sabab:
        return JSONResponse({"xato": sabab}, status_code=401,
                            headers={"WWW-Authenticate": 'Basic realm="tender-jurnal"'})
    sorov = _jurnal.JORIY_SOROV.get()
    if sorov is not None:
        sorov["tasdiqlangan"] = True
    return None


def _butun(prm, nom, standart=None):
    xom = prm.get(nom)
    if xom is None:
        return standart
    if not (xom.isascii() and xom.isdigit() and len(xom) <= 18):
        raise ValueError(f"{nom}: manfiy bo'lmagan butun son kerak")
    return int(xom)


def _vaqt(prm, nom):
    xom = prm.get(nom)
    if xom is None:
        return None
    try:
        v = datetime.datetime.fromisoformat(xom)
    except ValueError:
        raise ValueError(f"{nom}: ISO 8601 sana-vaqt kerak, masalan "
                         f"2026-09-30T10:00:00Z") from None
    return v if v.tzinfo else v.replace(tzinfo=datetime.timezone.utc)


def _filtrlar(prm):
    """So'rov parametrlari → (SQL, qiymatlar, tavsif). Xato — ValueError(matn).

    Qo'lda tahlil qilinadi (FastAPI parametrlari emas): aks holda noto'g'ri
    qiymat 422 `{"detail": [...]}` bilan qaytardi — bu API ning boshqa hamma
    xatosi esa `{"xato": ...}`.

    Ikki rejim:
      ko'rish (standart)  vaqt oralig'i (`dan`..`gacha`, standart — oxirgi 24
                          soat), YANGISI birinchi; keyingi sahifa — `oldin_id`;
      kuzatish            `keyin_id=<n>` — `id` o'sish tartibida, vaqt
                          oralig'isiz. `id` YOZILGAN tartibda beriladi, ya'ni
                          kursordan orqada hech narsa paydo bo'lmaydi
                          (yozish qulfi — `app.jurnal._yoz`). Sahifa UFQ
                          bilan chegaralanadi — o'sha so'rovda o'qilgan eng
                          katta `id`; filtr hech narsa topmasa ham kursor
                          ufqqa siljiydi (`royxatni_ol`).
    """
    nomalum = sorted(set(prm.keys()) - set(_FILTRLAR))
    if nomalum:
        raise ValueError("noma'lum parametr: %s (mavjudlari: %s)"
                         % (", ".join(nomalum), ", ".join(_FILTRLAR)))
    for nom in _FILTRLAR:
        if "\x00" in (prm.get(nom) or ""):
            raise ValueError(f"{nom}: noto'g'ri belgi")

    limit = _butun(prm, "limit", LIMIT_STANDART)
    if not 1 <= limit <= LIMIT_MAKS:
        raise ValueError(f"limit: 1..{LIMIT_MAKS}")
    oldin, keyin = _butun(prm, "oldin_id"), _butun(prm, "keyin_id")
    if oldin is not None and keyin is not None:
        raise ValueError("oldin_id va keyin_id birga berilmaydi")
    dan, gacha = _vaqt(prm, "dan"), _vaqt(prm, "gacha")
    if dan is None and keyin is None:
        try:
            dan = (gacha or _jurnal.hozir()) - datetime.timedelta(hours=24)
        except OverflowError:
            # `gacha=0001-01-01`: 24 soat oldingi sana `datetime` ga sig'maydi.
            raise ValueError("gacha: sana juda erta") from None

    shartlar, qiymatlar = [], []

    def shart(ifoda, qiymat):
        shartlar.append(ifoda)
        qiymatlar.append(qiymat)

    if dan is not None:
        shart("yaratildi >= %s", dan)
    if gacha is not None:
        shart("yaratildi < %s", gacha)
    for nom, ruxsat in (("tur", _TURLAR), ("manba", _MANBALAR), ("daraja", _DARAJALAR)):
        q = prm.get(nom)
        if q is not None:
            if q not in ruxsat:
                raise ValueError(f"{nom}: kutilgan qiymatlar — {', '.join(ruxsat)}")
            shart(f"{nom} = %s", q)
    holat = _butun(prm, "holat_kodi")
    if holat is not None:
        shart("holat_kodi = %s", holat)
    if prm.get("login") is not None:
        shart("login = %s", prm["login"])
    if prm.get("sorov_id") is not None:
        # Bitta so'rovning hamma qatorlari birga: `sorov`, `kirish` va so'rov
        # davomida yozilgan `log` satrlari bir xil `sorov_id` oladi (`JORIY_SOROV`).
        shart("sorov_id = %s", prm["sorov_id"])
    if prm.get("yol") is not None:
        # Yo'l BOSHI bo'yicha; LIKE ning maxsus belgilari oddiy belgi bo'lib qoladi.
        bosh = prm["yol"].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        shart("yol LIKE %s", bosh + "%")
    if oldin is not None:
        shart("id < %s", oldin)
    if keyin is not None:
        shart("id > %s", keyin)
        shartlar.append("id <= ufq.id")                  # ufq — pastda

    sql = f"SELECT {_ROYXAT_SQL} FROM sorov_jurnali"
    if shartlar:
        sql += " WHERE " + " AND ".join(shartlar)
    sql += f" ORDER BY id {'ASC' if keyin is not None else 'DESC'} LIMIT %s"
    if keyin is not None:
        # UFQ — eng katta `id`, sahifa bilan BITTA so'rovda (bitta snapshot)
        # o'qiladi. Undan kichik hamma `id` shu snapshot'da allaqachon ko'rinadi
        # (yozish qulfi: COMMIT tartibi = `id` tartibi), demak `keyin_id`..ufq
        # oralig'i to'liq ko'rib chiqiladi va kursor ufqqa siljishi mumkin.
        # Busiz kam uchraydigan filtr (`tur=kirish`) bo'sh sahifa qaytarib
        # kursorni joyida qoldirardi: har chaqiruv jadvalni o'sha `id` dan
        # qayta ko'rardi, jadval o'sgan sari — `statement_timeout` (503) gacha.
        # LEFT JOIN: mos qator bo'lmasa ham ufqli bitta qator qaytadi.
        sql = ("SELECT ufq.id, sahifa.* FROM (SELECT coalesce(max(id), 0) AS id "
               f"FROM sorov_jurnali) ufq LEFT JOIN LATERAL ({sql}) sahifa ON true "
               "ORDER BY sahifa.id")
    return sql, qiymatlar + [limit], {"dan": dan, "gacha": gacha, "limit": limit,
                                      "keyin_id": keyin}


def _oqi(sql, prm):
    """So'rovni O'Z qisqa umrli ulanishida bajaradi. Qaytadi: qatorlar ro'yxati.

    API ning yagona ulanishi (`store.ULANISH`) va qulfiga ATAYLAB tegmaydi:
    jurnal bo'yicha vaqt oralig'i so'rovi qulfni ushlab tursa, `/check`
    (hodisa siklida, o'sha qulfni kutib) va `/health` (healthcheck, 10 s)
    to'xtab qolardi. Har chaqiruvga yangi ulanish bu yerda o'rinli: o'qish —
    operatorning kamdan-kam so'rovi, sherikning 200 so'rov/daqiqasi emas
    (`store.py` dagi (c) sabab o'sha yo'lga tegishli), va ishlovchi sinxron —
    sovuq ulanish threadpool oqimini band qiladi, hodisa siklini emas.
    """
    with psycopg.connect(DATABASE_URL, autocommit=True, connect_timeout=10) as conn:
        # UTC: vaqt baza mintaqasida (Asia/Tashkent) emas, har doim bir xil qaytsin.
        conn.execute("SET statement_timeout = '5s'; SET TIME ZONE 'UTC'")
        return conn.execute(sql, prm).fetchall()


def _lugat(nomlar, qator):
    return {nom: (q.astimezone(datetime.timezone.utc).isoformat()
                  if isinstance(q, datetime.datetime) else q)
            for nom, q in zip(nomlar, qator)}


def _baza_xatosi(exc):
    log(f"[jurnal] o'qib bo'lmadi: {type(exc).__name__}", "warning")
    return _xato(503, "baza mavjud emas")


def royxatni_ol(request):
    """`GET /jurnal` — yozuvlar ro'yxati (tanalarsiz). Filtrlar: `_filtrlar`."""
    rad = _ruxsat(request)
    if rad is not None:
        return rad
    try:
        sql, prm, tavsif = _filtrlar(request.query_params)
    except ValueError as exc:
        return _xato(400, str(exc))
    try:
        natija = _oqi(sql, prm)
    except psycopg.Error as exc:
        return _baza_xatosi(exc)

    keyin = tavsif["keyin_id"]
    if keyin is not None:
        # Kuzatish: har qator boshida ufq (`_filtrlar`); mos qator bo'lmasa
        # ufq va NULL lardan iborat bitta qator keladi.
        ufq = max(natija[0][0], keyin) if natija else keyin
        natija = [q[1:] for q in natija if q[1] is not None]
    qatorlar = [_lugat(_ROYXAT, q) for q in natija]

    javob = {"qatorlar": qatorlar}
    if keyin is not None:
        # To'la sahifa: ufqqacha yana mos qator bo'lishi mumkin — oxirgisidan
        # davom etiladi. To'la emas: `keyin_id`..ufq oralig'i to'liq ko'rildi,
        # filtr hech narsa topmagan bo'lsa ham kursor ufqqa siljiydi. Hech
        # qachon `keyin_id` dan orqaga emas: jadval tozalangan yoki boshqa
        # nusxadan tiklangan bo'lsa ufq undan kichik bo'lishi mumkin.
        javob["keyingi_keyin_id"] = (qatorlar[-1]["id"] if len(qatorlar) == tavsif["limit"]
                                     else ufq)
    else:
        javob["dan"] = tavsif["dan"].isoformat()
        javob["gacha"] = tavsif["gacha"].isoformat() if tavsif["gacha"] else None
        javob["keyingi_oldin_id"] = (qatorlar[-1]["id"]
                                     if len(qatorlar) == tavsif["limit"] else None)
    return javob


def yozuvni_ol(request, jid):
    """`GET /jurnal/{jid}` — bitta yozuv TO'LIQ (sarlavhalar va tanalar bilan)."""
    rad = _ruxsat(request)
    if rad is not None:
        return rad
    if not (jid.isascii() and jid.isdigit() and len(jid) <= 18):
        return _xato(404, "yozuv topilmadi")
    try:
        qatorlar = _oqi(f"SELECT {', '.join(_TOLIQ)} FROM sorov_jurnali WHERE id = %s",
                        (int(jid),))
    except psycopg.Error as exc:
        return _baza_xatosi(exc)
    if not qatorlar:
        return _xato(404, "yozuv topilmadi")
    return _lugat(_TOLIQ, qatorlar[0])
