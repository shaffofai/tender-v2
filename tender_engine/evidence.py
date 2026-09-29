# -*- coding: utf-8 -*-
"""Evidence (dalil) qatlami — har verdiktning «nega»si (P1).

Har tekshiruv uchun bitta yozuv: fayl XESHI bilan bog'lanadi (id EMAS —
sherik jadvalni almashtirsa id qayta ishlatiladi, xesh esa mazmunga
bog'liq). REVIEW→1 qarori (2026-08-11) shu yerda iz qoldiradi:
`sinf='review'` — «ishonchsizlik bilan qabul qilinganlar» ro'yxati
har doim bitta SQL bilan chiqadi.

Jadval sxemasi (append-only trigger bilan) — versiyalangan migratsiyada:
`app/db/migrations/0001_boshlangich_sxema.sql` (`python -m app.db.migrate`).
"""

import json

ENGINE_VERSION = "engine-0.1"

# Ichki 5 status (QAYTA_QURISH Q-C) + Q1 darvozasi holati (2026-09-18)
ACCEPT_FILLED = "ACCEPT_FILLED"            # tashqi 1
REJECT_NO_PRICE = "REJECT_NO_PRICE"        # tashqi 2
STRUCTURE_VIOLATION = "STRUCTURE_VIOLATION"  # tashqi 2
TECHNICAL_ERROR = "TECHNICAL_ERROR"        # tashqi 0
REVIEW_AMBIGUOUS = "REVIEW_AMBIGUOUS"      # tashqi 1 (vaqtinchalik siyosat)
# Q1 darvozasi (buyurtmachi qarori 2026-09-18): ishtirokchi fayli buyurtmachi
# shabloni bilan BAYT-BA-BAYT teng (sha256) — alohida tashqi status 3.
# Bu hukm emas, tenglik fakti; asosiy dvigatelga yetib bormaydi.
IDENTICAL_COPY = "IDENTICAL_COPY"          # tashqi 3
STATUS_IDENTICAL = 3
# `files.status = 4` — TEXNIK sabab bilan tekshirib bo'lmadi, TASLIM BO'LINDI
# (buyurtmachi qarori 2026-09-23). Ilgari bu holat `0` da qolardi va faqat
# `jobs_state.dead_at` bilan «hali navbatda» dan ajratilardi — endi bitta ustun
# bitta haqiqat. Evidence darajasida bu HOLAT emas: har urinish TECHNICAL_ERROR
# (tashqi 0) bo'lib yoziladi, 4 esa navbat supurgisi (`olik_belgilash`) qo'yadi.
# Tenderga ham 4 boradi (`yuboruvchi`). Rad (2) EMAS — OLTIN QOIDA saqlanadi.
STATUS_TEXNIK = 4

TASHQI = {
    ACCEPT_FILLED: 1,
    REJECT_NO_PRICE: 2,
    STRUCTURE_VIOLATION: 2,
    TECHNICAL_ERROR: 0,
    REVIEW_AMBIGUOUS: 1,     # buyurtmachi qarori 2026-08-11; sinf='review' MAJBURIY
    IDENTICAL_COPY: STATUS_IDENTICAL,
}

# «Ishonchsizlik bilan qabul qilinganlar» — nizo bo'lsa birinchi so'rov shu
REVIEW_SQL = """
SELECT fayl_id, link, tender_id, type, fayl_hash, comment_uz, yaratildi
FROM validation_evidence
WHERE sinf = 'review'
ORDER BY yaratildi DESC
"""


def fayl_sha256(yol):
    import hashlib
    h = hashlib.sha256()
    with open(yol, "rb") as f:
        for blok in iter(lambda: f.read(1 << 20), b""):
            h.update(blok)
    return h.hexdigest()


# Tuzilma buzilishini bildiruvchi kodlar (qolgan 2 lar — narx kamchiligi)
STRUKTURA_KODLARI = {"SHEET_DELETED", "COLUMN_COUNT", "SHEET_UNFILLED",
                     "FILE_UNREADABLE"}


def eski_natijadan_ichki(status, kodlar):
    """Eski dvigatel (validate_one) natijasini ichki 5 statusga o'giradi.

    Eski kod REVIEW bilmaydi — u faqat yangi dvigatelda paydo bo'ladi;
    soya davrida eski verdiktlar 3 sinfga tushadi.
    """
    if status == 1:
        return ACCEPT_FILLED
    if status == 2:
        if any(k in STRUKTURA_KODLARI for k in kodlar):
            return STRUCTURE_VIOLATION
        return REJECT_NO_PRICE
    if status == STATUS_IDENTICAL:             # Q1 darvozasi (2026-09-18)
        return IDENTICAL_COPY
    return TECHNICAL_ERROR                     # 0 ham, 4 (taslim) ham — texnik


def yozuv_tayyorla(*, fayl_id, link, tender_id, typ, fayl_hash,
                   etalon_tpl_id, etalon_hash, rol, ichki_status,
                   findings=None, comment_uz="", varaq_dalil=None):
    """validate_one natijasidan evidence qatorini quradi (sof funksiya).

    REVIEW_AMBIGUOUS -> sinf='review' AVTOMATIK — uni unutib qo'yish
    mumkin emas (qaror sharti: har REVIEW->1 izli bo'lishi SHART).
    """
    if ichki_status not in TASHQI:
        raise ValueError(f"noma'lum ichki status: {ichki_status!r}")
    findings = findings or []
    return {
        "fayl_id": str(fayl_id) if fayl_id is not None else None,
        "link": link,
        "tender_id": tender_id,
        "type": typ,
        "fayl_hash": fayl_hash,
        "etalon_tpl_id": etalon_tpl_id,
        "etalon_hash": etalon_hash,
        "rol": rol,
        "ichki_status": ichki_status,
        "tashqi_status": TASHQI[ichki_status],
        "sinf": "review" if ichki_status == REVIEW_AMBIGUOUS else None,
        "kodlar": [f["code"] for f in findings
                   if f.get("severity") not in ("note", "technical")],
        "notelar": [f["code"] for f in findings
                    if f.get("severity") == "note"],
        "comment_uz": comment_uz,
        "varaq_dalil": varaq_dalil,
        "validator_version": ENGINE_VERSION,
    }


def yoz(conn, yozuv):
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO validation_evidence
               (fayl_id, link, tender_id, type, fayl_hash, etalon_tpl_id,
                etalon_hash, rol, ichki_status, tashqi_status, sinf,
                kodlar, notelar, comment_uz, varaq_dalil, validator_version)
               VALUES (%(fayl_id)s, %(link)s, %(tender_id)s, %(type)s,
                       %(fayl_hash)s, %(etalon_tpl_id)s, %(etalon_hash)s,
                       %(rol)s, %(ichki_status)s, %(tashqi_status)s, %(sinf)s,
                       %(kodlar)s, %(notelar)s, %(comment_uz)s,
                       %(varaq_dalil)s, %(validator_version)s)""",
            {**yozuv,
             "kodlar": json.dumps(yozuv["kodlar"], ensure_ascii=False),
             "notelar": json.dumps(yozuv["notelar"], ensure_ascii=False),
             "varaq_dalil": (json.dumps(yozuv["varaq_dalil"], ensure_ascii=False)
                             if yozuv.get("varaq_dalil") is not None else None)})
    conn.commit()
