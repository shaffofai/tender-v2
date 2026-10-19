# -*- coding: utf-8 -*-
"""Tekshiruv navbati — worker'ning xizmat holati `jobs_state` jadvalida.

Kashfiyot, atomik band qilish (lease + fencing tokeni), texnik xatodan keyin
backoff, «shablon kutilmoqda» (F1), o'lik qatorlar va qayta navbat. Qulflash
FAQAT `jobs_state` da; `files` ga faqat `status` (+ `updated_at`) yoziladi.

(Ilgari `jobs_worker.py` da — ko'chirilgan; ish vaqtida aniqlanadigan sxema
shartlari o'rniga qat'iy sxema, `app/db/schema.py`.)
"""

import time
import uuid as uuid_module

from app import config
from app.db import schema
from app.db.schema import (
    JOBS_TABLE,
    TEMPLATES_TABLE,
    _kalit_join,
    _kalit_tengligi,
    _pk,
    _yashamayotgan,
    _yuborilmagan,
)
from app.log import log
from tender_engine import evidence as _evidence

_S = config.worker()
LEASE_MINUTES = _S.lease_minutes
MAX_ATTEMPTS = _S.max_attempts
RETRY_BASE_SECONDS = _S.retry_base_seconds
RETRY_MAX_SECONDS = _S.retry_max_seconds


#: Shablon (buyurtmachi fayli) yo'qligi sababli o'lgan qatorlarning
#: `last_error` / `dead_reason` boshi. 2026-10-19 gacha bu «kutish» belgisi
#: edi (F1: fayl shablonni soatlab kutardi); endi kutilmaydi — fayl darhol
#: status 4 oladi (`verdict.shablon_topilmadi_yoz`), lekin belgi o'sha:
#: `shablon_kelganini_tekshir` (N1) va yuboruvchining toshqin to'xtatgichi
#: shu bo'yicha taniydi, eski qatorlar ham.
KUTISH_BELGI = "ETALON_KUTILMOQDA"
#: Shu boshli `dead_reason` — worker o'zi status 4, izoh va xabarni yozgan
#: (`verdict.shablon_topilmadi_yoz`); yuboruvchi bunday faylga o'z texnik
#: xabarini YOZMAYDI (`app/sender/technical.py` dagi nusxasi bilan bir xil).
SHABLON_YOQ_BELGI = KUTISH_BELGI + ": SHABLON_YOQ"


# 4 — texnik sabab bilan tekshirib bo'lmadi, taslim bo'lindi (2026-09-23).
# `olik_belgilash` va (shablon yo'q, 2026-10-19) `verdict.shablon_topilmadi_yoz`
# qo'yadi; `requeue`, `shablon_kelganini_tekshir` (N1),
# `api_server` (link o'zgarsa) 0 ga qaytaradi; `natijani_yoz` verdikt bilan bosadi.
STATUS_TEXNIK = _evidence.STATUS_TEXNIK


# ── Kashfiyot (discovery) ──────────────────────────────────────────────────
# To'liq skanerlash QIMMAT: navbatda 168 000 qator bo'lsa, har safar hammasini
# ko'rib chiqish har bir fayl uchun bir necha soniya qo'shadi (jami O(n²)).
# Shuning uchun to'liq skan faqat vaqti-vaqti bilan, oraliqda esa faqat YANGI
# qatorlar (birlamchi kalit suv belgisidan katta) ko'riladi.
KASHF_INTERVAL = _S.discovery_interval
_KASHF = {"oxirgi_toliq": 0.0, "suv_belgisi": None}


def kashf_qil(conn, toliq=False):
    """Jadvaldagi yangi status=0 qatorlar uchun holat yozuvi yaratadi.

    Asosiy jadvalga TEGMAYDI — faqat o'qiydi va o'z jadvaliga yozadi.

    `toliq=False` bo'lsa faqat suv belgisidan keyingi qatorlar ko'riladi.
    To'liq skan `KASHF_INTERVAL` soniyada bir marta baribir bajariladi —
    boshqa jamoa qatorni status=0 ga QAYTARSA ham uni ko'ramiz.
    """
    pk = _pk()
    hozir = time.monotonic()
    toliq = (toliq
             or _KASHF["suv_belgisi"] is None
             or (hozir - _KASHF["oxirgi_toliq"]) >= KASHF_INTERVAL)

    if not toliq:
        # O'Z-O'ZINI TIKLASH: `jobs_state` bo'shab qolgan bo'lsa (tozalash,
        # bazani tiklash va h.k.) arzon skan uni hech qachon to'ldira olmaydi
        # — suv belgisidan keyin yangi qator yo'q. Shu holatni arzon
        # tekshiramiz va to'liq skanga o'tamiz.
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM jobs_state LIMIT 1")
            if cur.fetchone() is None:
                toliq = True

    shart = f"j.status = 0 AND {_yuborilmagan('j')}{_yashamayotgan('j')}"
    args = ()
    if not toliq:
        shart += f" AND j.{pk} > %s"
        args = (_KASHF["suv_belgisi"],)

    with conn.cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO jobs_state (uuid)
            SELECT j.{pk}::text FROM {JOBS_TABLE} j
            WHERE {shart}
            ON CONFLICT (uuid) DO NOTHING
            """, args)
        n = cur.rowcount
        # Suv belgisi — jadvaldagi eng katta kalit. Undan keyin qo'shilgan
        # har qanday qator albatta kattaroq kalitga ega bo'ladi.
        cur.execute(f"SELECT max({pk}) FROM {JOBS_TABLE}")
        eng_katta = cur.fetchone()[0]
        if eng_katta is not None:
            _KASHF["suv_belgisi"] = eng_katta
    if toliq:
        _KASHF["oxirgi_toliq"] = hozir
    conn.commit()
    return n


def ish_ol(conn):
    """Bitta tayyor faylni atomik band qiladi.

    Returns: (ish_dict, token) yoki (None, None).
    Qulflash FAQAT `jobs_state` da — boshqa jamoa jadvali qulflanmaydi.
    """
    lease = f"{LEASE_MINUTES} minutes"
    pk = _pk()
    token = uuid_module.uuid4().hex
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                f"""
                WITH nomzod AS (
                    SELECT s.uuid
                    FROM jobs_state s
                    JOIN {JOBS_TABLE} j ON {_kalit_join()}
                    WHERE j.status = 0 AND {_yuborilmagan('j')}{_yashamayotgan('j')}
                      AND s.dead_at IS NULL
                      AND (s.claimed_at IS NULL OR s.claimed_at < now() - interval '{lease}')
                      AND (s.retry_after IS NULL OR s.retry_after <= now())
                      AND s.attempts < %s
                    ORDER BY s.created_at
                    LIMIT 1
                    FOR UPDATE OF s SKIP LOCKED
                )
                UPDATE jobs_state s
                SET claimed_at = now(), claim_token = %s,
                    attempts = s.attempts + 1, updated_at = now()
                FROM nomzod n
                WHERE s.uuid = n.uuid
                RETURNING s.uuid
                """,
                (MAX_ATTEMPTS, token),
            )
            qator = cur.fetchone()
            if not qator:
                return None, None
            uid = qator[0]
            cur.execute(
                f"SELECT {pk}::text, link, type, file_id, tender_id "
                f"FROM {JOBS_TABLE} WHERE {_kalit_tengligi()}",
                (uid,))
            j = cur.fetchone()
            if not j:
                return None, None
            ish = {"uuid": j[0], "link": j[1], "type": j[2],
                   "bidder_id": j[3], "tender_id": j[4]}
    return ish, token


def texnik_qoyib_yubor(conn, uuid, xato, doimiy=False, token=None):
    """Texnik xato: `jobs.status` O'ZGARMAYDI (0 qoladi), keyin qayta uriniladi.

    doimiy=True (404, Excel emas) → darhol «o'lik» deb belgilanadi.
    """
    fence = "AND (%(tok)s::text IS NULL OR claim_token = %(tok)s)"
    with conn.cursor() as cur:
        if doimiy:
            cur.execute(
                "UPDATE jobs_state SET claimed_at = NULL, claim_token = NULL, "
                "last_error = %(x)s, dead_at = now(), dead_reason = %(x)s, "
                f"updated_at = now() WHERE uuid = %(u)s AND dead_at IS NULL {fence}",
                {"x": xato[:2000], "u": uuid, "tok": token})
            if cur.rowcount:
                log(f"  [O'LIK] {uuid}: {xato[:110]}")
        else:
            cur.execute(
                "UPDATE jobs_state SET claimed_at = NULL, claim_token = NULL, "
                "last_error = %(x)s, updated_at = now(), "
                "retry_after = now() + least("
                "  interval '1 second' * %(b)s * power(2, greatest(attempts - 1, 0)),"
                "  interval '1 second' * %(c)s) "
                f"WHERE uuid = %(u)s {fence}",
                {"x": xato[:2000], "u": uuid, "tok": token,
                 "b": RETRY_BASE_SECONDS, "c": RETRY_MAX_SECONDS})
    conn.commit()


def shablon_kelganini_tekshir(conn):
    """Shabloni ENDI kelgan fayllarni qayta navbatga qaytaradi (F1/N1).

    `ETALON_KUTILMOQDA` belgili qatorlar `files` → `templates` (tender_id +
    type, deleted_at IS NULL) bilan solishtiriladi; shablon topilsa:
      1) hali tirik kutayotganlar (2026-10-19 dan oldingi eski qatorlar) —
         `retry_after = now()`;
      2) shablon yo'qligi uchun status 4 olganlar — tiriladi (status 4 → 0,
         izoh tozalanadi), haqiqiy verdikt ikkinchi xabar bo'lib ketadi.
    Qaytaradi: uyg'otilganlar soni.
    """
    shablon_bor = (f"EXISTS (SELECT 1 FROM {TEMPLATES_TABLE} t "
                   f"WHERE t.tender_id = j.tender_id AND t.type = j.type "
                   f"AND t.deleted_at IS NULL)")
    with conn.cursor() as cur:
        # 1) Hali TIRIK kutayotganlar — `retry_after` ni hozirga suramiz.
        cur.execute(
            f"""
            UPDATE jobs_state s
            SET retry_after = now(), updated_at = now()
            FROM {JOBS_TABLE} j
            WHERE {_kalit_join('j', 's')}
              AND s.dead_at IS NULL
              AND s.last_error LIKE %s
              AND s.retry_after IS NOT NULL AND s.retry_after > now()
              AND {shablon_bor}
            """, (KUTISH_BELGI + "%",))
        n = cur.rowcount
        # 2) N1 (2026-09-23): shablon yo'qligi uchun O'LGAN fayllar ham tirilsin.
        #    Ularda `dead_at` bor va `retry_after = NULL` — yuqoridagi shart
        #    esa aynan teskarisini talab qiladi; busiz shablon kelganda ham
        #    fayl ABADIY verdiktsiz qolardi. 2026-10-19 dan bu asosiy yo'l:
        #    shablon yo'q fayl darhol status 4 oladi (`shablon_topilmadi_yoz`).
        #    Faqat ETALON_KUTILMOQDA sababli o'lganlar tiriladi (boshqa
        #    o'liklar — tarmoq, buzuq fayl — bu yerga tegishli emas).
        #    Avval `files.status` 4 → 0 va «buyurtmachi fayllari topilmadi»
        #    izohi tozalanadi (`ish_ol` faqat 0 ni oladi; izoh verdikt bilan
        #    yangilanadi, texnik holatda eskisi qolmasin), keyin jobs_state —
        #    bitta tranzaksiya.
        cur.execute(
            f"""
            UPDATE {JOBS_TABLE} j
            SET status = 0, comment = NULL, updated_at = now()
            FROM jobs_state s
            WHERE {_kalit_join('j', 's')}
              AND j.status = %s
              AND s.dead_at IS NOT NULL
              AND s.dead_reason LIKE %s
              AND {shablon_bor}
            """, (STATUS_TEXNIK, KUTISH_BELGI + "%"))
        cur.execute(
            f"""
            UPDATE jobs_state s
            SET dead_at = NULL, dead_reason = NULL, attempts = 0,
                last_error = NULL, retry_after = now(), updated_at = now()
            FROM {JOBS_TABLE} j
            WHERE {_kalit_join('j', 's')}
              AND s.dead_at IS NOT NULL
              AND s.dead_reason LIKE %s
              AND j.status IN (0, %s)
              AND {shablon_bor}
            """, (KUTISH_BELGI + "%", STATUS_TEXNIK))
        tirildi = cur.rowcount
    conn.commit()
    if tirildi:
        log(f"[shablon keldi] {tirildi} ta shablonsiz (status 4) fayl qayta tekshiruvga qaytarildi")
    if n:
        log(f"[shablon keldi] {n} ta kutayotgan fayl navbatga qaytarildi")
    return n + tirildi


PAUZA_BELGI = "PAUZA"


def pauzada_qaytar(conn, uuid, sabab, kechikish, token=None):
    """PAUZA: yuklab olish MANBA nosozligi sababli bo'lmadi — fayl JAZOSIZ qaytadi.

    Urinish hisobi sarflanmaydi
    (`ish_ol` oshirgan bittasi qaytariladi), fayl o'lmaydi, `last_error`
    `PAUZA:` bilan boshlanadi — pauza yopilganda `pauza_uygot` shu belgi
    bo'yicha uyg'otadi. `kechikish` — keyingi sinov shu faylni emas, BOSHQA
    faylni olsin (bitta buzuq havola pauzani abadiy ushlab qolmasin).
    """
    fence = "AND (%(tok)s::text IS NULL OR claim_token = %(tok)s)"
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE jobs_state SET claimed_at = NULL, claim_token = NULL, "
            "attempts = greatest(attempts - 1, 0), last_error = %(x)s, "
            "retry_after = now() + interval '1 second' * %(i)s, updated_at = now() "
            f"WHERE uuid = %(u)s AND dead_at IS NULL {fence}",
            {"x": f"{PAUZA_BELGI}: {sabab}"[:2000], "u": uuid, "tok": token,
             "i": kechikish})
    conn.commit()


def pauza_tirilt(conn, uuidlar, sabab, kechikish):
    """PAUZA ochilgan zahoti: seriyadagi (pauzadan OLDIN xato bergan) fayllarni qaytaradi.

    Ular xato paytida odatdagidek yo'lga tushgan: 404 / «Excel emas» —
    darhol o'lik, `olik_belgilash` esa `files.status` ni 4 ga o'tkazgan;
    vaqtinchalik xatolar urinish sarflagan. Endi ma'lum: aybdor fayl emas,
    manba. `--requeue` ning shu fayllar uchun avtomatik nusxasi: avval
    `files.status` 4 → 0 (`ish_ol` faqat 0 ni oladi), keyin `jobs_state` —
    bitta tranzaksiyada. `dead_at` tozalangani uchun yuboruvchi ularga
    texnik xabar yozmaydi (kechikish hali tugamagan). Verdikt olgan yoki
    hozir boshqa jarayonda ishlanayotgan faylga tegilmaydi.
    Qaytaradi: qaytarilganlar soni.
    """
    if not uuidlar:
        return 0
    lease = f"{LEASE_MINUTES} minutes"
    royxat = sorted({str(u) for u in uuidlar})
    with conn.cursor() as cur:
        cur.execute(
            f"""
            UPDATE {JOBS_TABLE} j
            SET status = 0, updated_at = now()
            FROM jobs_state s
            WHERE {_kalit_join('j', 's')}
              AND s.uuid = ANY(%s)
              AND s.dead_at IS NOT NULL
              AND j.status = %s
              AND {_yuborilmagan('j')}{_yashamayotgan('j')}
            """, (royxat, STATUS_TEXNIK))
        cur.execute(
            f"""
            UPDATE jobs_state s
            SET dead_at = NULL, dead_reason = NULL,
                claimed_at = NULL, claim_token = NULL,
                attempts = greatest(s.attempts - 1, 0),
                last_error = %s,
                retry_after = now() + interval '1 second' * %s,
                updated_at = now()
            FROM {JOBS_TABLE} j
            WHERE {_kalit_join('j', 's')}
              AND s.uuid = ANY(%s)
              AND j.status = 0
              AND (s.claimed_at IS NULL OR s.claimed_at < now() - interval '{lease}')
            """, (f"{PAUZA_BELGI}: {sabab}"[:2000], kechikish, royxat))
        n = cur.rowcount
    conn.commit()
    return n


def pauza_uygot(conn):
    """Pauza yopildi (manba tiklandi): pauzada qaytarilgan fayllar DARHOL navbatga.

    Busiz ular `pauzada_qaytar` qo'ygan `retry_after` tugashini kutardi.
    Faqat `jobs_state` ga yoziladi. Qaytaradi: uyg'otilganlar soni.
    """
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE jobs_state SET retry_after = now(), updated_at = now() "
            "WHERE dead_at IS NULL AND last_error LIKE %s "
            "AND retry_after IS NOT NULL AND retry_after > now()",
            (PAUZA_BELGI + ":%",))
        n = cur.rowcount
    conn.commit()
    return n


def pauzadagilar_soni(conn):
    """Pauzada qaytarilib, hali qayta ishlanmagan fayllar soni. Faqat o'qiydi."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM jobs_state WHERE dead_at IS NULL AND last_error LIKE %s",
            (PAUZA_BELGI + ":%",))
        return int(cur.fetchone()[0] or 0)


def kutayotganlar_soni(conn):
    """F1: shablon kutayotgan fayllar — (soni, eng eskisining vaqti). Faqat o'qiydi."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*), min(created_at) FROM jobs_state "
            "WHERE dead_at IS NULL AND last_error LIKE %s", (KUTISH_BELGI + "%",))
        n, eng_eski = cur.fetchone()
    return int(n or 0), eng_eski


def olik_belgilash(conn):
    """Urinishlari tugagan (lekin hali o'lik deb belgilanmagan) qatorlar.

    VERDIKT OLGAN fayl o'lik bo'la olmaydi — «dead-letter» tushunchasi
    faqat hali javob berilmagan (status=0) qatorlarga tegishli. Bu shart
    bo'lmasa, eski urinish hisobi bilan qolgan, lekin bugun muvaffaqiyatli
    tekshirilgan fayllar ro'yxatni ifloslantiradi (2026-08-20 da 295 ta).
    """
    schema.tekshir(conn)
    lease = f"{LEASE_MINUTES} minutes"
    with conn.cursor() as cur:
        cur.execute(
            f"""
            UPDATE jobs_state s
            SET dead_at = now(),
                dead_reason = COALESCE(last_error, 'urinishlar soni tugadi'),
                claim_token = NULL, updated_at = now()
            WHERE s.dead_at IS NULL AND s.attempts >= %s
              AND (s.claimed_at IS NULL OR s.claimed_at < now() - interval '{lease}')
              AND EXISTS (SELECT 1 FROM {JOBS_TABLE} j
                          WHERE {_kalit_join('j', 's')} AND j.status = 0)
            RETURNING s.uuid, COALESCE(s.last_error, '')
            """,
            (MAX_ATTEMPTS,))
        qatorlar = cur.fetchall()
        # `files.status = 4` (texnik, taslim) — texnik o'limlar uchun shu yer.
        # `dead_at` ikki yo'ldan qo'yiladi (texnik_qoyib_yubor doimiy=True,
        # yuqoridagi urinishlar), ikkalasini shu supurgi bitta shart bilan
        # tutadi: o'lik VA hali 0. (Shablon yo'q holati status 4 ni o'zi,
        # izoh va xabar bilan bitta tranzaksiyada qo'yadi —
        # `verdict.shablon_topilmadi_yoz`.) Oradagi kechikish
        # eng ko'pi POLL_INTERVAL (5 s) — yuboruvchining 6 soatlik kechikishi
        # oldida sezilmaydi. Verdikt olgan (1/2/3) qatorga tegilmaydi.
        cur.execute(
            f"""
            UPDATE {JOBS_TABLE} j
            SET status = %s, updated_at = now()
            FROM jobs_state s
            WHERE {_kalit_join('j', 's')}
              AND s.dead_at IS NOT NULL
              AND j.status = 0
              AND {_yuborilmagan('j')}{_yashamayotgan('j')}
            """, (STATUS_TEXNIK,))
        if cur.rowcount:
            log(f"[texnik] {cur.rowcount} ta o'lik fayl status={STATUS_TEXNIK} ga o'tkazildi")
    conn.commit()
    for uid, xato in qatorlar:
        log(f"[O'LIK] {uid} — {MAX_ATTEMPTS} urinish behuda: {xato[:110]}")
    if qatorlar:
        log(f"[diqqat] {len(qatorlar)} ta fayl qayta ishlanmadi. "
            f"Ko'rish: python jobs_worker.py --dead-letters")
    return len(qatorlar)


def requeue(conn, nishon):
    schema.tekshir(conn)
    with conn.cursor() as cur:
        if nishon == "all":
            cur.execute(
                "UPDATE jobs_state SET attempts = 0, dead_at = NULL, dead_reason = NULL, "
                "claimed_at = NULL, claim_token = NULL, retry_after = NULL, "
                "last_error = NULL, updated_at = now() WHERE dead_at IS NOT NULL "
                "RETURNING uuid")
        else:
            cur.execute(
                "UPDATE jobs_state SET attempts = 0, dead_at = NULL, dead_reason = NULL, "
                "claimed_at = NULL, claim_token = NULL, retry_after = NULL, "
                "last_error = NULL, updated_at = now() WHERE uuid = %s "
                "RETURNING uuid", (nishon,))
        uuidlar = [r[0] for r in cur.fetchall()]
        n = len(uuidlar)
        # status=4 (texnik, taslim) → 0: `ish_ol` faqat 0 ni oladi, busiz
        # qaytarilgan fayl navbatga TUSHMAYDI (N1 sinfidagi xato).
        if uuidlar:
            cur.execute(
                f"UPDATE {JOBS_TABLE} SET status = 0"
                ", updated_at = now() "
                f"WHERE {_pk()} = ANY(%s::bigint[]) AND status = %s",
                ([int(u) for u in uuidlar if str(u).isdigit()], STATUS_TEXNIK))
    conn.commit()
    log(f"{n} ta qator qayta navbatga qo'yildi.")
    return n
