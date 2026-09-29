# -*- coding: utf-8 -*-
"""JAMLANMA (Форма 4) — QAT'IY tuzilma.

Buyurtmachi shablonidagi yagona narx ustuni to'ldirilganmi.
(Ilgari `set_validator.py` da — ko'chirilgan, o'zgartirilmagan.)
"""

from tender_engine.normalize import normk_asosiy as _norm
from tender_engine.roles import (
    FILL_THRESHOLD,
    JAMLANMA_HEADER_ANCHORS,
    JAMLANMA_NAME_KEYWORDS,
    JAMLANMA_PRICE_KEYWORDS,
)
from tender_engine.rules.common import (
    _col_nonempty,
    _col_stats,
    _defekt_bormi,
    _etalon_ustun_nomi,
    _find_row_with_any,
    _first_col_matching,
    _is_numbering_row,
    _korinish,
    _muqobil_narx_ustuni,
    _muqobil_talab,
    _norm_header_row,
    _q3_data_boshi,
    _q3_tekshir,
    _tekshiriladigan_varaq,
    _trim_trailing_empty,
)


# ---------------------------------------------------------------------------
# JAMLANMA (Форма 4) — QAT'IY
# ---------------------------------------------------------------------------

def validate_jamlanma(etalon_sheets, part_sheets):
    findings = []
    if not part_sheets:
        findings.append({"code": "FILE_EMPTY", "sheet": None,
                         "detail_uz": "Fayl bo'sh yoki jadvallar yo'q."})
        return findings

    pt_name = _tekshiriladigan_varaq(etalon_sheets, part_sheets,
                                     JAMLANMA_HEADER_ANCHORS)
    pt_rows = part_sheets[pt_name]

    # etalon konteksti (nom o'zgartirilganda pozitsion fallback uchun)
    et_cols = None
    et_hdr = None
    et_price_idx = None
    et_name_idx = None
    et_xom_hdr = None
    if etalon_sheets:
        et_name = list(etalon_sheets.keys())[0]
        # D+ (2026-08-04): sarlavha QATORI kengaytirilgan langarlar bilan
        # topiladi (narx nomi ham langar bo'la oladi), lekin ustun INDEKSLARI
        # faqat o'z ro'yxatlaridan — name_idx hech qachon narx nomi bilan
        # topilmaydi (aks holda name == price bo'lib nisbat o'z-o'zini
        # tasdiqlab yuborardi).
        et_hdr = _find_row_with_any(etalon_sheets[et_name], JAMLANMA_HEADER_ANCHORS)
        if et_hdr is not None:
            et_xom_hdr = etalon_sheets[et_name][et_hdr]
            et_cols = _trim_trailing_empty(_norm_header_row(et_xom_hdr))
            et_price_idx = _first_col_matching(et_cols, JAMLANMA_PRICE_KEYWORDS)
            et_name_idx = _first_col_matching(et_cols, JAMLANMA_NAME_KEYWORDS)

    hdr = _find_row_with_any(pt_rows, JAMLANMA_HEADER_ANCHORS)
    if hdr is None and et_hdr is not None and et_hdr < len(pt_rows):
        # sarlavha nomlari o'zgartirilgan bo'lishi mumkin — etalon pozitsiyasi
        if sum(1 for cell in pt_rows[et_hdr] if _norm(cell)) >= 2:
            hdr = et_hdr
            findings.append({"code": "HEADER_RENAMED", "sheet": pt_name, "severity": "note",
                             "detail_uz": "Jadval sarlavhasi etalon nomlaridan farq qiladi — "
                                          "pozitsiya bo'yicha tekshirildi."})
    if hdr is None:
        findings.append({"code": "HEADER_NOT_FOUND", "sheet": pt_name,
                         "detail_uz": "Jamlanma jadvalining sarlavhasi topilmadi — "
                                      "hujjat Форма 4 (Жамланма жадвал) tuzilmasiga mos emas."})
        return findings

    col_names = _trim_trailing_empty(_norm_header_row(pt_rows[hdr]))

    # Narx ustuni nomi — xabarlarda etalondagi ASL nomni ko'rsatamiz, chunki
    # hujjat ruscha ham, o'zbekcha ham bo'lishi mumkin.
    narx_nomi = _etalon_ustun_nomi(et_xom_hdr, et_cols, et_price_idx, "Жами қиймати")

    # narx ustuni: nomi bo'yicha (ikki tilda), topilmasa etalon pozitsiyasi
    price_idx = _first_col_matching(col_names, JAMLANMA_PRICE_KEYWORDS)
    if price_idx is None and et_price_idx is not None:
        price_idx = et_price_idx
        findings.append({"code": "COLUMN_RENAMED", "sheet": pt_name, "severity": "note",
                         "detail_uz": f"«{narx_nomi}» ustuni nomi o'zgartirilgan — "
                                      f"etalon pozitsiyasi bo'yicha tekshirildi."})
    if price_idx is None:
        findings.append({"code": "COLUMN_MISSING", "sheet": pt_name,
                         "detail_uz": f"«{narx_nomi}» ustuni topilmadi."})
        return findings

    # Struktura. Ustun QO'SHISH — kutilgan xatti-harakat: ishtirokchi o'z
    # narx/izoh ustunlarini kiritadi, shu sababli bu KAMCHILIK emas.
    # Ustun O'CHIRISH esa buzg'unchi: buyurtmachi so'ragan ma'lumot yo'qoladi.
    if et_cols is not None:
        if len(col_names) < len(et_cols):
            findings.append({"code": "COLUMN_COUNT", "sheet": pt_name,
                             "detail_uz": f"Ustunlar o'chirilgan: etalonda {len(et_cols)} ta "
                                          f"ustun bor, hujjatda {len(col_names)} ta qoldi."})
        elif len(col_names) > len(et_cols):
            findings.append({"code": "COLUMN_ADDED", "sheet": pt_name, "severity": "note",
                             "detail_uz": f"Hujjatga {len(col_names) - len(et_cols)} ta "
                                          f"qo'shimcha ustun kiritilgan."})
        for ci in range(min(len(col_names), len(et_cols))):
            if col_names[ci] != et_cols[ci]:
                findings.append({"code": "COLUMN_RENAMED", "sheet": pt_name, "severity": "note",
                                 "detail_uz": f"{ci+1}-ustun nomi o'zgartirilgan: "
                                              f"«{et_cols[ci]}» → «{col_names[ci]}»."})

    # ma'lumot boshlanishi: sarlavhadan keyin raqamlash qatorini o'tkazish.
    # Sarlavha ikki qatorga bo'lingan bo'lsa, ikkinchi qator ham o'tkaziladi.
    _sarlavha_qoldiqlari = tuple(JAMLANMA_NAME_KEYWORDS) + tuple(JAMLANMA_PRICE_KEYWORDS)
    data_start = hdr + 1
    while data_start < len(pt_rows):
        katak = _norm(pt_rows[data_start][price_idx]
                      if price_idx < len(pt_rows[data_start]) else None)
        if _is_numbering_row(pt_rows[data_start]) or (
                katak and any(kw in katak for kw in _sarlavha_qoldiqlari)):
            data_start += 1
            continue
        break

    # to'ldirilganlik: harajat nomi bor qatorlar nisbatan (qat'iy son emas)
    name_idx = _first_col_matching(col_names, JAMLANMA_NAME_KEYWORDS)
    q3_pt_name = name_idx if name_idx is not None else et_name_idx
    if name_idx is None:
        name_idx = et_name_idx if et_name_idx is not None else max(price_idx - 1, 0)

    filled, _errors = _col_stats(pt_rows, data_start, price_idx)
    nameable = _col_nonempty(pt_rows, data_start, name_idx)
    ratio = (filled / nameable) if nameable else 0.0
    muqobil_col = None
    if filled == 0 or ratio < FILL_THRESHOLD:
        # MUQOBIL USTUN ZAXIRASI (2026-08-05): ishtirokchi narxni o'zi
        # qo'shgan/boshqa nomdagi ustunga kiritgan bo'lishi mumkin — masalan
        # buyurtmachi slotga Форма-4 shablonini qo'ygan-u, ishtirokchi to'g'ri
        # shaklda «на единицу измерения»/«сумма» ustunlarini to'ldirgan.
        # Andijon test to'plamida shu naqsh 24 tadan ~20 noo'rin rad berdi.
        band_idx = q3_pt_name if q3_pt_name is not None else name_idx
        et_rows_j = (etalon_sheets[et_name]
                     if etalon_sheets and et_hdr is not None else None)
        muqobil_col, muqobil_f = _muqobil_narx_ustuni(
            pt_rows, data_start, col_names, band_idx,
            _muqobil_talab(pt_rows, data_start, band_idx),
            tashqari={price_idx},
            et_rows=et_rows_j,
            et_ds=_q3_data_boshi(et_rows_j, et_hdr) if et_rows_j else 0,
            et_band_idx=et_name_idx)
        if muqobil_col is not None:
            nom = _korinish(col_names[muqobil_col]) \
                if muqobil_col < len(col_names) and col_names[muqobil_col] \
                else f"{muqobil_col + 1}-ustun (yorliqsiz)"
            findings.append({
                "code": "PRICE_IN_OTHER_COLUMN", "sheet": pt_name,
                "severity": "note",
                "detail_uz": f"Narx «{narx_nomi}» o'rniga «{nom}» ustuniga "
                             f"kiritilgan ({muqobil_f} ta qiymat)."})
        else:
            findings.append({"code": "PRICE_EMPTY", "sheet": pt_name,
                             "detail_uz": f"«{narx_nomi}» ustuni to'ldirilmagan — "
                                          f"harajat qiymatlari kiritilmagan."})

    # ── Q3: «jiddiy to'ldirish» (faqat boshqa kamchilik topilmaganda) ─────
    if not _defekt_bormi(findings) and etalon_sheets and et_hdr is not None:
        q3_cols = [price_idx] + ([muqobil_col] if muqobil_col is not None else [])
        q3 = _q3_tekshir(
            "jamlanma", pt_name, narx_nomi,
            etalon_sheets[et_name], _q3_data_boshi(etalon_sheets[et_name], et_hdr),
            et_name_idx, [et_price_idx] if et_price_idx is not None else [],
            pt_rows, data_start, q3_pt_name, q3_cols)
        if q3:
            findings.append(q3)
    return findings
