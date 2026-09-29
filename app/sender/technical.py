# -*- coding: utf-8 -*-
"""TEXNIK xabarlar — o'lgan (taslim bo'lingan, `files.status=4`) fayllar uchun
«tekshirib bo'lmadi» xabarini navbatga qo'yish (§7.5, H1/H2/H3 himoyalari).

(Ilgari `yuboruvchi.py` da — ko'chirilgan.)
"""

from app import config
from app.db import schema
from app.db.schema import JOBS_TABLE, _kalit_join, _pk, _yashamayotgan, _yuborilmagan
from app.log import log
from app.sender.statuses import STATUS_TEXNIK, status_qabulmi, tashqi_status

_S = config.yuboruvchi()
TEXNIK_YUBORISH = _S.texnik_yuborish
YUBORISH_TEXNIK_KECHIKISH = _S.yuborish_texnik_kechikish
YUBORISH_TEXNIK_LEASE = _S.yuborish_texnik_lease
TEXNIK_TOSHQIN_CHEGARA = _S.texnik_toshqin_chegara


# ---------------------------------------------------------------------------
# 4) TEXNIK xabarlar — §7.5
# ---------------------------------------------------------------------------
# Uchta sinf, 14 ta emas. Ko'rik: aniq sinflarning yarmida NOTO'G'RI faktik
# da'vo bor edi — masalan «Excel emas (PDF)» matni HTML xato sahifasi uchun
# ham chiqadi; «shablon topilmadi» bizning o'qish xatomizni ham qamrab olardi.
# Sinf qanchalik yirik bo'lsa, noto'g'ri da'vo ehtimoli shuncha kichik.
#
# Uch qat'iy qoida: (1) «rad» ma'nosi yo'q; (2) ishtirokchi aybdor emas —
# `files.link` ni PLATFORMA yozadi (194 322/194 325 UUID naqshida), «qaytadan
# yuklang» deyilmaydi; (3) bajarib bo'lmaydigan va'da yo'q — `dead_at` ni
# avtomatik tozalaydigan hech narsa yo'q, «qayta tekshiriladi» deyilmaydi.
MATN_FAYL = ("Hujjat fayli saqlash tizimidan olinmadi, shuning uchun avtomatik "
             "tekshiruv o'tkazilmadi. Bu hujjat mazmuniga berilgan baho emas va "
             "sizdan hech narsa talab qilinmaydi.")
MATN_SHABLON = ("Solishtirish uchun zarur bo'lgan buyurtmachi shabloni mavjud emas "
                "yoki avtomatik o'qilmadi. Bu hujjatingizga berilgan baho emas.")
MATN_UMUMIY = ("Hujjatni avtomatik tekshirish texnik sabab bilan yakunlanmadi. Bu "
               "hujjat mazmuniga berilgan baho emas va sizdan hech narsa talab "
               "qilinmaydi.")

# `dead_reason` matnlari `common.download` / `jobs_worker` dan (kichik harfda solishtiriladi)
_FAYL_NAQSH = ("havola mavjud emas", "bo'sh fayl", "excel fayli emas",
               "yuklab olishda xato", "http 5", "timeout", "connecterror",
               "readtimeout", "fayl juda katta", "havola rad etildi",
               "yo'naltirish rad etildi", "yuklangan fayl yo'qoldi")
_SHABLON_NAQSH = ("etalon_kutilmoqda", "etalon yo'q", "etalon aniqlanmadi",
                  "etalon o'qilmadi", "buyurtmachi shabloni", "etalon_unparsed",
                  "sarlavha topilmadi", "yaroqli etalon", "shablon")

_TAQIQ_SOZLAR = ("qaytadan yuklang", "qayta yuklang", "rad etil", "rad qilin")


def texnik_matn(dead_reason):
    """`dead_reason` → ishtirokchiga ko'rinadigan matn (uch sinfdan biri)."""
    s = (dead_reason or "").lower()
    if any(n in s for n in _SHABLON_NAQSH):
        return MATN_SHABLON
    if any(n in s for n in _FAYL_NAQSH):
        return MATN_FAYL
    return MATN_UMUMIY


for _m in (MATN_FAYL, MATN_SHABLON, MATN_UMUMIY):
    # Import paytida qotirilgan: matn o'zgartirilsa ham taqiq buzilmasin.
    assert not any(t in _m.lower() for t in _TAQIQ_SOZLAR), _m


def _texnik_nomzod_sharti():
    """§7.5 WHERE bloki — sanash va yozish uchun BIR XIL (H3 shu bilan ta'minlanadi)."""
    return f"""
          FROM jobs_state s
          JOIN {JOBS_TABLE} f ON {_kalit_join('f', 's')}
         WHERE s.dead_at IS NOT NULL
           AND s.dead_at < now() - interval '{YUBORISH_TEXNIK_KECHIKISH} seconds'
           AND (s.claimed_at IS NULL
                OR s.claimed_at < now() - interval '{YUBORISH_TEXNIK_LEASE} seconds')
           AND f.status = {int(STATUS_TEXNIK)}
           AND {_yuborilmagan('f')}{_yashamayotgan('f')}
           AND NOT EXISTS (SELECT 1 FROM yuborish_navbati y
                            WHERE y.fayl_id = f.{_pk()} AND y.sabab = 'texnik'
                              AND y.created_at > s.dead_at)
    """


def texnik_xabarlarni_yig(conn):
    """O'lgan (taslim bo'lingan) fayllar uchun status=0 xabarini navbatga qo'yadi.

    Yagona belgi — `jobs_state.dead_at`. `status=0` har qatorning BOSHLANG'ICH
    qiymati, uni o'zicha ishlatsak hali tekshirilmagan har faylni «texnik
    muammo» deb yuborardik. `attempts >= MAX_ATTEMPTS` ham yaramaydi — `ish_ol`
    claim paytida oshiradi, ayni paytda ishlanayotgan sog'lom faylda ham 5 turadi.

    Qaytadi: (yozildi, nomzod_soni, toxtatildi).
    """
    if not TEXNIK_YUBORISH:
        return 0, 0, False
    if not status_qabulmi(tashqi_status(STATUS_TEXNIK)):
        # Tender 4 ni olmaydi (validator `in:1,2`) — outbox'ga ma'nosiz qator
        # yozib, keyin «tashlandi» qilishdan ko'ra umuman yozmaymiz. Sherik
        # validatorni kengaytirgach TENDER_QABUL_STATUSLAR=1,2,4 (yoki 1,2,3,4)
        # — shu bilan yo'l ochiladi, kod o'zgarmaydi.
        global _texnik_ogohlantirildi
        if not _texnik_ogohlantirildi:
            log(f"[texnik] status={STATUS_TEXNIK} tender tomonidan qabul qilinmaydi "
                "(TENDER_QABUL_STATUSLAR) — texnik xabarlar yozilmaydi", "warning")
            _texnik_ogohlantirildi = True
        return 0, 0, False
    schema.tekshir(conn)
    shart = _texnik_nomzod_sharti()
    with conn.cursor() as cur:
        # H2 — avval SANAYMIZ. Soni chegaradan oshsa hech narsa yozilmaydi:
        # 50 dan ortiq fayl bir vaqtda o'lgan bo'lsa bu ishtirokchilar emas,
        # TIZIM haqida xabar (saqlash tizimi / FILE_BASE_URL / tarmoq).
        cur.execute(f"SELECT count(*) {shart}")
        nomzod = int(cur.fetchone()[0] or 0)
        if nomzod > TEXNIK_TOSHQIN_CHEGARA:
            conn.rollback()
            log(f"[TOSHQIN] {nomzod} ta o'lik fayl (chegara {TEXNIK_TOSHQIN_CHEGARA}) — "
                f"texnik xabarlar YOZILMADI. Bu ommaviy nosozlik belgisi: saqlash "
                f"tizimi / FILE_BASE_URL / tarmoqni tekshiring, keyin --requeue.",
                "error")
            return 0, nomzod, True
        if not nomzod:
            conn.rollback()
            return 0, 0, False
        # H3 — yozish paytida shart QAYTA baholanadi: INSERT ... SELECT bitta
        # bayonot, ya'ni `dead_at` hamon o'rnida, `status` hamon 0 ekani AYNAN
        # yozilayotgan lahzada tekshiriladi. Sanash bilan yozish orasida
        # `--requeue` bo'lsa qator shartdan chiqib ketadi va yozilmaydi.
        # Sinflash SQL ichida, naqshlar PARAMETR (text[]) sifatida. F-string
        # bilan literalga qo'yib BO'LMAYDI: naqshlarda apostrof bor («bo'sh
        # fayl», «yo'naltirish») — SQL sintaksisi buziladi. Soxta ulanish SQL ni
        # parse qilmagani uchun bu faqat haqiqiy bazada (e2e) ko'rinadi.
        cur.execute(
            f"""
            INSERT INTO yuborish_navbati (fayl_id, file_id, status, comment, sabab)
            SELECT f.{_pk()}, f.file_id, {int(STATUS_TEXNIK)},
                   CASE
                     WHEN lower(coalesce(s.dead_reason, '')) LIKE ANY(%(sh_naqsh)s) THEN %(sh)s
                     WHEN lower(coalesce(s.dead_reason, '')) LIKE ANY(%(fy_naqsh)s) THEN %(fy)s
                     ELSE %(um)s
                   END,
                   'texnik'
            {shart}
             ORDER BY s.dead_at
             LIMIT %(n)s
            ON CONFLICT (fayl_id, status, md5(comment)) DO NOTHING
            """, {"sh": MATN_SHABLON, "fy": MATN_FAYL, "um": MATN_UMUMIY,
                  "sh_naqsh": _like_naqshlar(_SHABLON_NAQSH),
                  "fy_naqsh": _like_naqshlar(_FAYL_NAQSH),
                  "n": TEXNIK_TOSHQIN_CHEGARA})
        yozildi = cur.rowcount
    conn.commit()
    if yozildi:
        log(f"[texnik] {yozildi} ta o'lik fayl uchun status={STATUS_TEXNIK} xabari navbatga qo'yildi")
    return yozildi, nomzod, False


_texnik_ogohlantirildi = False


def _like_naqshlar(naqshlar):
    """Naqsh ro'yxati → `LIKE ANY(text[])` uchun `%...%` ko'rinishida.

    `texnik_matn()` (Python) bilan BIR XIL ro'yxatdan — ikkisi ajralib ketmasin.
    """
    return ["%" + n + "%" for n in naqshlar]
