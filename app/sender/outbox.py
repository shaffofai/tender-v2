# -*- coding: utf-8 -*-
"""Chiquvchi navbat (`yuborish_navbati`) — band qilish, natija, qayta ochish.

(Ilgari `yuboruvchi.py` da — ko'chirilgan.)
"""

from app import config
from app.log import log
from app.sender.delivery import KOD_STATUS_RUXSATSIZ

_S = config.yuboruvchi()
YUBORISH_TOPLAM = _S.yuborish_toplam
YUBORISH_MAX_URINISH = _S.yuborish_max_urinish
BAND_DAQIQA = _S.band_daqiqa
YUBORISH_RETRY_BAZA = _S.yuborish_retry_baza
YUBORISH_RETRY_MAKS = _S.yuborish_retry_maks


# ---------------------------------------------------------------------------
# 1) Navbatni olish — §7.4 qulflash
# ---------------------------------------------------------------------------
def navbatni_ol(conn, toplam=None):
    """Nomzodlarni tanlab DARHOL band qiladi. Qaytadi: [(id, file_id, status, comment)].

    `WITH ... FOR UPDATE SKIP LOCKED` + tashqi `WHERE` da shartlar TAKRORLANGAN.
    Nega: `READ COMMITTED` da PostgreSQL qulf kutilgandan keyin FAQAT tashqi
    `UPDATE` ning `WHERE` ini qayta baholaydi — CTE ichidagini emas. Tashqi
    shart faqat `y.id = n.id` bo'lsa ikkita yuboruvchi bir qatorni ikkalasi ham
    oladi va ishtirokchiga bir xil xabar ikki marta ketadi.

    Chaqiruvchi shundan keyin DARHOL commit qiladi — HTTP ochiq tranzaksiyada
    yuborilmaydi.
    """
    toplam = toplam or YUBORISH_TOPLAM
    with conn.cursor() as cur:
        cur.execute(
            f"""
            WITH nomzod AS (
                SELECT id FROM yuborish_navbati
                 WHERE holat IN (0,1)
                   AND urinish < %(max)s
                   AND (band_until IS NULL OR band_until < now())
                 ORDER BY id
                 LIMIT %(n)s
                 FOR UPDATE SKIP LOCKED
            )
            UPDATE yuborish_navbati y
               SET band_until = now() + interval '{BAND_DAQIQA} minutes'
              FROM nomzod n
             WHERE y.id = n.id
               AND y.holat IN (0,1)
               AND y.urinish < %(max)s
               AND (y.band_until IS NULL OR y.band_until < now())
            RETURNING y.id, y.file_id, y.status, y.comment
            """, {"max": YUBORISH_MAX_URINISH, "n": toplam})
        qatorlar = cur.fetchall()
    conn.commit()
    return qatorlar


# ---------------------------------------------------------------------------
# 3) Natijani yozish — §7.4 javob jadvali
# ---------------------------------------------------------------------------
#: Bu 4xx lar VAQTINCHALIK deb sanaladi:
#:   408 timeout, 429 juda ko'p so'rov — tarmoq/yuk;
#:   401/403 — BIZNING kalitimiz (parol eskirgan bo'lishi mumkin). «Doimiy» deb
#:   sanasak parol eskirgan paytda butun navbat bir zumda holat=3 bo'lib qoladi.
_VAQTINCHALIK_4XX = {408, 429, 401, 403}


#: Tender validatori hali kengaytirilmagan bo'lsa 3/4 ga shu kodlarni beradi.
#: Laravel `ValidationException` — 422; ba'zi sozlamalarda 400.
_VALIDATOR_KODLARI = (422, 400)


def holatni_aniqla(kod, status=None):
    """(holat, urinish_oshadimi, ogohlantirish) — HTTP kodi bo'yicha.

    `status` berilsa 3/4 uchun maxsus qoida ishlaydi — pastga qarang.
    """
    if kod == 200:
        return 2, False, None
    if kod == KOD_STATUS_RUXSATSIZ:
        return 3, False, None          # doimiy: sozlama o'zgarmaguncha ma'nosiz
    if kod in _VALIDATOR_KODLARI and status in (3, 4):
        # 3 (aynan nusxa) va 4 (texnik) ni tender validatori qabul qilmasligi
        # MUMKIN: 2026-09-23 da ko'rsatilgan kodi `in:1,2` edi. Bu VAQTINCHALIK
        # holat — ular validatorni kengaytirsa o'sha xabar o'tadi. Shuning uchun
        # «doimiy 4xx» deb tashlab yubormaymiz: urinish oshadi (3 tadan keyin
        # baribir to'xtaydi), lekin sabab ANIQ yoziladi va `--qayta-och` bilan
        # bitta buyruqda qaytariladi. Aks holda verdikt jimgina yo'qolardi.
        return 1, True, (f"HTTP {kod} — tender status={status} ni qabul qilmadi. "
                         f"Validator hali `in:1,2` bo'lishi mumkin; kengaytirilgach "
                         f"`python yuboruvchi.py --qayta-och` bilan qaytaring")
    if kod in (401, 403):
        return 1, True, f"HTTP {kod} — TENDER_API_LOGIN/PAROL eskirgan bo'lishi mumkin"
    if kod in (408, 429):
        return 1, True, None
    if 400 <= kod < 500:
        return 3, False, None          # doimiy: qayta urinilmaydi
    return 1, True, None               # 0 (tarmoq), 5xx


def natijani_yoz(conn, xabar_id, kod, xato, status=None):
    """Alohida tranzaksiyada. `band_until` har holda tozalanadi."""
    holat, oshadi, ogoh = holatni_aniqla(kod, status)
    if ogoh:
        log(f"[yuborish] id={xabar_id}: {ogoh}", "warning")
    with conn.cursor() as cur:
        if holat == 2:
            cur.execute(
                "UPDATE yuborish_navbati SET holat = 2, yuborilgan_at = now(), "
                "xato = NULL, band_until = NULL WHERE id = %s", (xabar_id,))
        elif holat == 3:
            cur.execute(
                "UPDATE yuborish_navbati SET holat = 3, urinish = %s, xato = %s, "
                "band_until = NULL WHERE id = %s",
                (YUBORISH_MAX_URINISH, xato, xabar_id))
        else:
            # urinish oshadi; chegaraga yetsa 3 (operator ko'rigi). LEAST —
            # `yn_urinish_chk` (0..3) buzilmasin.
            #
            # BACKOFF (2026-09-25): ilgari bu yerda `band_until = NULL` edi va
            # qator DARHOL qayta nomzod bo'lardi. `SET` ifodalari ESKI qiymatni
            # ko'radi, shuning uchun `power(2, urinish)`: 0 → 300 s, 1 → 600 s.
            # Oxirgi urinishda (holat 3 bo'lganda) band_until KERAK EMAS —
            # qator boshqa olinmaydi, `--qayta-och` uni tozalaydi.
            cur.execute(
                """
                UPDATE yuborish_navbati
                   SET urinish = LEAST(urinish + 1, 3),
                       holat = CASE WHEN urinish + 1 >= %(max)s THEN 3 ELSE 1 END,
                       xato = %(x)s,
                       band_until = CASE
                           WHEN urinish + 1 >= %(max)s THEN NULL
                           ELSE now() + make_interval(secs => LEAST(
                                    %(baza)s * power(2, urinish), %(maks)s))
                       END
                 WHERE id = %(id)s
                RETURNING holat
                """, {"max": YUBORISH_MAX_URINISH, "x": xato, "id": xabar_id,
                      "baza": YUBORISH_RETRY_BAZA, "maks": YUBORISH_RETRY_MAKS})
            q = cur.fetchone()
            holat = q[0] if q else holat
    conn.commit()
    return holat


def qayta_och(conn, statuslar=None):
    """TASHLANGAN (holat=3) xabarlarni navbatga qaytaradi. Qaytadi: soni.

    Asosiy holat: sherik validatorini kengaytirdi (`in:1,2` → `in:1,2,3,4`) —
    ilgari 422 olgan 3/4 xabarlari endi o'tadi. Verdikt BAZADA saqlangan,
    shuning uchun qayta tekshirish shart emas — faqat navbatni ochamiz.
    """
    shart, prm = "holat = 3", []
    if statuslar:
        shart += " AND status = ANY(%s)"
        prm.append(list(statuslar))
    with conn.cursor() as cur:
        cur.execute(f"UPDATE yuborish_navbati SET holat = 0, urinish = 0, "
                    f"band_until = NULL, xato = NULL WHERE {shart} "
                    f"RETURNING file_id, status", prm)
        q = cur.fetchall()
    conn.commit()
    for fid, st in q[:10]:
        log(f"  ↻ file_id={fid} status={st} — navbatga qaytarildi")
    if len(q) > 10:
        log(f"  ... yana {len(q) - 10} ta")
    log(f"Qaytarildi: {len(q)} ta xabar" if q else "Qaytariladigan xabar yo'q")
    return len(q)


def holat(conn):
    with conn.cursor() as cur:
        cur.execute("""SELECT holat, sabab, count(*) FROM yuborish_navbati
                        GROUP BY holat, sabab ORDER BY holat, sabab""")
        qatorlar = cur.fetchall()
        cur.execute("SELECT count(*) FROM yuborish_navbati WHERE holat = 3")
        tashlandi = cur.fetchone()[0]
    nomlar = {0: "navbatda", 1: "xato (qayta)", 2: "yuborildi", 3: "tashlandi"}
    print("\n  yuborish_navbati:")
    for h, sabab, n in qatorlar:
        print(f"    {nomlar.get(h, h):<14} {sabab:<8} {n:>8}")
    if tashlandi:
        print(f"\n  ⚠ {tashlandi} ta xabar 3 urinishdan keyin TASHLANDI — operator ko'rigi:")
        print("    SELECT id, file_id, status, xato FROM yuborish_navbati WHERE holat = 3;")
    return qatorlar
