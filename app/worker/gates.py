# -*- coding: utf-8 -*-
"""Darvozalar — `validate_one` atrofidagi kichik, alohida tekshiruvlar.

Har biri tayyor natija dict yoki None qaytaradi va HUKM emas, TENGLIK yoki
aniq fakt o'lchovi:
    Q1  `_aynan_nusxa_res`   — bayt-ba-bayt aynan nusxa (sha256) → status 3
    #2  `_ochilmadi_res`     — fayl ochilmadi → QABUL (1) + aniq izoh
    S1b `_mazmun_nusxa_res`  — mazmun etalon bilan aynan teng → 2
    #7  `_bosh_hujjat_res`   — hech qanday yangi qiymat yo'q → 2
Ular QAYERDA va QAYSI TARTIBDA chaqirilishi — `pipeline.ishni_bajar` da.

(Ilgari `jobs_worker.py` da — ko'chirilgan.)
"""

import hashlib
import os

from tender_engine import decision as _decision
from tender_engine import evidence as _evidence
from tender_engine.validate import STATUS_DEFECT, STATUS_FILLED


# Q1 DARVOZASI holati (buyurtmachi qarori 2026-09-18): ishtirokchi fayli
# buyurtmachi shabloni bilan BAYT-BA-BAYT teng (sha256) → alohida status 3.
# Yagona manba `tender_engine.evidence.STATUS_IDENTICAL`.
STATUS_IDENTICAL = _evidence.STATUS_IDENTICAL
IZOH_AYNAN_NUSXA = "Buyurtmachi fayli bilan aynan bir xil"


def _sha256_fayl(yol):
    h = hashlib.sha256()
    with open(yol, "rb") as f:
        for blok in iter(lambda: f.read(1 << 20), b""):
            h.update(blok)
    return h.hexdigest()


def _aynan_nusxa_res(fayl_nomi, rol, fayl_yoli, etalon_yoli):
    """Q1 DARVOZASI: ishtirokchi fayli etalon bilan BAYT-BA-BAYT tengmi?

    Bu HUKM emas, TENGLIK O'LCHOVI — sarlavha/rol/ustun talqini yo'q,
    shuning uchun noo'rin rad ehtimoli ham yo'q: sha256 teng bo'lsa, bu
    AYNAN O'SHA fayl (13 855 faylli korpusda 6 099 ta shunday topilgan,
    mustaqil son-o'lchov bilan ziddiyat 0 ta). Izohga to'liq xesh yoziladi
    — nizoda bahslashib bo'lmaydigan dalil (FRE 902(14) uslubidagi isbot).

    Teng bo'lsa tayyor natija dict, aks holda None qaytadi.
    O'qish xatosida ham None — darvoza jim chetlanib, oddiy oqim davom etadi.
    """
    try:
        # Arzon oldindan tekshiruv: hajmi farq qilsa aynan bo'lishi mumkin emas
        if os.path.getsize(fayl_yoli) != os.path.getsize(etalon_yoli):
            return None
        fh = _sha256_fayl(fayl_yoli)
        eh = _sha256_fayl(etalon_yoli)
    except OSError:
        return None
    if fh != eh:
        return None
    # 2026-09-18, buyurtmachi qarori: aynan nusxa RAD (2) emas, ALOHIDA holat —
    # status 3. Izoh matni buyurtmachiniki; to'liq xesh (nizo dalili) izohdan
    # findings/audit iziga ko'chdi — evidence `comment_uz` va `jobs_validation_log`
    # findings da saqlanadi.
    return {
        "file": fayl_nomi,
        "role": rol,
        "status": STATUS_IDENTICAL,
        "findings": [{
            "code": "AYNAN_NUSXA", "sheet": None,
            "detail_uz": f"{IZOH_AYNAN_NUSXA} (sha256: {fh}).",
        }],
        "comment_uz": IZOH_AYNAN_NUSXA,
    }


IZOH_OQILMADI = "Hujjatni o'qib bo'lmadi."


def _ochilmadi_res(fayl_nomi, rol, xato):
    """Fayl tiklash zanjiridan ham o'tmadi — «ochib bo'lmadi».

    Buyurtmachi qarori (2026-09-07): texnik nosozlik RAD sababi EMAS —
    hujjat QABUL qilinadi, izoh aniq: «Hujjatni o'qib bo'lmadi.» Ilgari bu
    yo'l status=2 berardi va ishtirokchi dasturchi xatosini o'qirdi
    («'Chartsheet' object has no attribute 'max_row'») — OLTIN QOIDA
    buzilishi (92 fayl, 2026-09-04 auditi).

    Kod `FILE_UNREADABLE` findingda SAQLANADI: evidence'da ACCEPT_FILLED +
    kodlar=["FILE_UNREADABLE"] bo'lib tushadi — «nima uchun qabul» izi
    yo'qolmaydi. `hukm`/REVIEW qatlamlari status=1 da ishga tushmaydi.
    """
    return {"file": fayl_nomi, "role": rol, "status": STATUS_FILLED,
            "findings": [{"code": "FILE_UNREADABLE", "sheet": None,
                          "detail_uz": f"{IZOH_OQILMADI} ({str(xato)[:120]})"}],
            "comment_uz": IZOH_OQILMADI}


IZOH_MAZMUN_NUSXA = ("Shablon bilan mazmunan aynan bir xil — hujjatga hech narsa "
                     "kiritilmagan (fayl faqat qayta saqlangan).")


def _mazmun_nusxa_res(fayl_nomi, rol, et_varaqlar, pt_varaqlar):
    """S1b DARVOZASI (2026-09-07): baytlar farq qiladi, MAZMUN esa etalon
    bilan aynan teng — ishtirokchi shablonni Excel'da ochib, hech narsa
    kiritmasdan saqlagan (`docProps`, `topLeftCell`, ustun kengligi o'zgaradi,
    ma'lumot esa yo'q). sha256 darvozasi bunday faylni o'tkazib yuborardi:
    korpusda 12 431 ta, 19 tasi QABUL bo'lgan.

    `decision._bir_xilmi` — ishlab chiqarishda allaqachon ishlatiladigan
    (hukm_shakl_erkin → IDENTICAL_COPY) katak-ba-katak `normk` tengligi,
    sonlar yaxlitlanmaydi. Bu HUKM emas, TENGLIK: yolg'on «nusxa» imkonsiz,
    faqat o'tkazib yuborish (xavfsiz tomon) mumkin. Butun korpusda o'lchangan
    (2026-09-07): B da mazmun-teng+qabul = 0, foyda = 19, 1 318 ta allaqachon
    rad etilganda faqat izoh kuchayadi.

    `validate_one` dan OLDIN chaqiriladi. AYNAN_NUSXA `_REVIEW_STRUKTURA` da
    yo'q — «narx bor — qabul» qatlami buni qayta ochmaydi.
    """
    if not et_varaqlar or not pt_varaqlar:
        return None
    try:
        if not _decision._bir_xilmi(et_varaqlar, pt_varaqlar):
            return None
    except Exception:
        return None          # tenglik o'lchovi yiqilsa — jim chetlanadi
    return {"file": fayl_nomi, "role": rol, "status": STATUS_DEFECT,
            "findings": [{"code": "AYNAN_NUSXA", "sheet": None,
                          "detail_uz": IZOH_MAZMUN_NUSXA}],
            "comment_uz": IZOH_MAZMUN_NUSXA}


IZOH_YANGI_QIYMAT_YOQ = ("Hujjatga yangi qiymat kiritilmagan — buyurtmachi "
                         "shablonidagi sonlar o'zgarmagan, narx yo'q.")


def _bosh_hujjat_res(fayl_nomi, rol):
    """#7 (2026-09-07): `validate_one` QABUL degan, lekin hujjatda HECH QANDAY
    yangi qiymat yo'q (`decision.bosh_hujjat_qabulmi`). ACCEPT yo'lida ilgari
    Q1/Q3 himoyasi yo'q edi — 185 tasdiqlangan noo'rin qabul.

    Kod `NO_NEW_VALUES` ATAYLAB `_REVIEW_STRUKTURA` da YO'Q: `PRICE_EMPTY`
    bo'lsa 3b `hukm` uni qayta ochib, `narx_ustuni_toldirilganmi` nollarni
    yangi son deb sanab (u yerda `_narxsimon` filtri yo'q) qaytadan qabul
    qilishi mumkin edi. Bu kod bilan flip mantiqan imkonsiz.
    """
    return {"file": fayl_nomi, "role": rol, "status": STATUS_DEFECT,
            "findings": [{"code": "NO_NEW_VALUES", "sheet": None,
                          "detail_uz": IZOH_YANGI_QIYMAT_YOQ}],
            "comment_uz": IZOH_YANGI_QIYMAT_YOQ}
