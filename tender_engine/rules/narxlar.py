# -*- coding: utf-8 -*-
"""NARXLAR jadvali — QATORLAR ERKIN.

Narx ustuni (yoki ishtirokchi tanlagan muqobil ustun) to'ldirilganmi.
(Ilgari `set_validator.py` da — ko'chirilgan, o'zgartirilmagan.)
"""

from tender_engine.normalize import (
    is_numeric_nonzero as _is_numeric_nonzero,
    normk_asosiy as _norm,
)
from tender_engine.roles import (
    FILL_THRESHOLD,
    NARXLAR_NAME_KEYWORDS,
    NARXLAR_PRICE_KEYWORDS,
    is_price_keyword,
)
from tender_engine.rules.common import (
    _BIRLIK_KEYWORDS,
    _defekt_bormi,
    _etalon_ustun_nomi,
    _find_row_with_any,
    _first_col_matching,
    _is_numbering_row,
    _korinish,
    _muqobil_narx_ustuni,
    _muqobil_talab,
    _narx_nomimi,
    _norm_header_row,
    _q3_data_boshi,
    _q3_tekshir,
    _tekshiriladigan_varaq,
    _trim_trailing_empty,
)
from tender_engine.structure import _lcs_col_map


# ---------------------------------------------------------------------------
# NARXLAR jadvali — QATORLAR ERKIN
# ---------------------------------------------------------------------------

def validate_narxlar(etalon_sheets, part_sheets):
    findings = []
    if not part_sheets:
        findings.append({"code": "FILE_EMPTY", "sheet": None,
                         "detail_uz": "Fayl bo'sh yoki jadvallar yo'q."})
        return findings

    pt_name = _tekshiriladigan_varaq(etalon_sheets, part_sheets,
                                     NARXLAR_NAME_KEYWORDS)
    pt_rows = part_sheets[pt_name]

    # etalon konteksti (nom o'zgartirilganda pozitsion fallback uchun)
    et_cols = None
    et_hdr = None
    et_price_idx = None
    et_unit_idx = None
    et_comp_idx = None
    et_xom_hdr = None
    if etalon_sheets:
        et_name = list(etalon_sheets.keys())[0]
        et_hdr = _find_row_with_any(etalon_sheets[et_name], NARXLAR_NAME_KEYWORDS)
        if et_hdr is not None:
            et_xom_hdr = etalon_sheets[et_name][et_hdr]
            et_cols = _trim_trailing_empty(_norm_header_row(et_xom_hdr))
            et_price_idx = _first_col_matching(et_cols, NARXLAR_PRICE_KEYWORDS)
            et_unit_idx = _first_col_matching(et_cols, _BIRLIK_KEYWORDS)
            et_comp_idx = _first_col_matching(et_cols, NARXLAR_NAME_KEYWORDS)
            if et_unit_idx == et_price_idx:
                et_unit_idx = None

    hdr = _find_row_with_any(pt_rows, NARXLAR_NAME_KEYWORDS)
    if hdr is None and et_hdr is not None and et_hdr < len(pt_rows):
        if sum(1 for cell in pt_rows[et_hdr] if _norm(cell)) >= 2:
            hdr = et_hdr
            findings.append({"code": "HEADER_RENAMED", "sheet": pt_name, "severity": "note",
                             "detail_uz": "Jadval sarlavhasi etalon nomlaridan farq qiladi — "
                                          "pozitsiya bo'yicha tekshirildi."})
    if hdr is None:
        findings.append({"code": "HEADER_NOT_FOUND", "sheet": pt_name,
                         "detail_uz": "Narxlar jadvalining sarlavhasi topilmadi — "
                                      "hujjat нархлар жадвали tuzilmasiga mos emas."})
        return findings

    col_names = _trim_trailing_empty(_norm_header_row(pt_rows[hdr]))

    # etalon<->ishtirokchi ustun moslashuvi (surilish/nom o'zgarishiga chidamli)
    colmap = _lcs_col_map(et_cols, col_names) if et_cols else {}

    # Xabarlarda etalondagi ASL nomni ko'rsatamiz (hujjat ruscha bo'lishi mumkin)
    narx_nomi = _etalon_ustun_nomi(et_xom_hdr, et_cols, et_price_idx, "Бирлик учун нарх")

    # narx ustuni — (1) nomi bo'yicha (ikki tilda), (2) etalon moslashuvi
    price_idx = _first_col_matching(col_names, NARXLAR_PRICE_KEYWORDS)
    if price_idx is None:
        for c, name in enumerate(col_names):
            if is_price_keyword(name, "narxlar"):
                price_idx = c
                break
        if price_idx is None and et_price_idx is not None:
            price_idx = colmap.get(et_price_idx, et_price_idx)
        if price_idx is not None:
            findings.append({"code": "COLUMN_RENAMED", "sheet": pt_name, "severity": "note",
                             "detail_uz": f"«{narx_nomi}» ustuni nomi o'zgartirilgan — "
                                          f"etalon pozitsiyasi bo'yicha tekshirildi."})
    if price_idx is None:
        findings.append({"code": "COLUMN_MISSING", "sheet": pt_name,
                         "detail_uz": f"«{narx_nomi}» ustuni topilmadi."})
        return findings

    # o'lchov birligi ustuni — nomi bo'yicha, topilmasa etalon moslashuvi
    unit_idx = _first_col_matching(col_names, _BIRLIK_KEYWORDS)
    if unit_idx is None and et_unit_idx is not None:
        unit_idx = colmap.get(et_unit_idx, et_unit_idx)
    if unit_idx == price_idx:
        unit_idx = None   # narx ustuniga tushib qolsa ratio doim ~1.0 bo'lardi

    # struktura: nom o'zgarishi faqat eslatma (qator soni TEKSHIRILMAYDI)
    if et_cols is not None:
        for ci in range(min(len(col_names), len(et_cols))):
            if col_names[ci] != et_cols[ci]:
                findings.append({"code": "COLUMN_RENAMED", "sheet": pt_name, "severity": "note",
                                 "detail_uz": f"{ci+1}-ustun nomi o'zgartirilgan: "
                                              f"«{et_cols[ci]}» → «{col_names[ci]}»."})

    data_start = hdr + 1
    while data_start < len(pt_rows) and _is_numbering_row(pt_rows[data_start]):
        data_start += 1

    # narxlanadigan qatorlar = o'lchov birligi bor qatorlar (bo'lim sarlavhalari emas);
    # birlik ustuni topilmasa — komponent nomi bor qatorlar
    comp_idx = _first_col_matching(col_names, NARXLAR_NAME_KEYWORDS)
    if comp_idx is None:
        comp_idx = colmap.get(et_comp_idx, et_comp_idx) if et_comp_idx is not None else 1

    # Narxlanadigan qatorlarni belgilovchi ustun: o'lchov birligi, u bo'sh
    # bo'lsa komponent nomi. Ilgari faqat birlik ustuni ishlatilardi — u
    # ba'zi shakllarda umuman to'ldirilmagan bo'ladi va natijada BIRONTA
    # qator sanalmay, ratio har doim 0 chiqardi: to'la to'ldirilgan hujjat
    # ham «narx ustuni bo'sh» deb rad etilardi.
    def _belgilovchi():
        for idx in (unit_idx, comp_idx):
            if idx is None:
                continue
            n = sum(1 for row in pt_rows[data_start:]
                    if _norm(row[idx] if idx < len(row) else None) != "")
            if n:
                return idx
        return None

    mark_idx = _belgilovchi()

    def _ustun_holati(idx):
        """Berilgan ustun narxlanadigan qatorlarning qanchasida to'ldirilgan."""
        p = f = 0
        for row in pt_rows[data_start:]:
            if mark_idx is not None:
                countable = _norm(row[mark_idx] if mark_idx < len(row) else None) != ""
            else:
                # hech qanday langar yo'q — qatorda biror ma'lumot bo'lsa sanaymiz
                countable = any(_norm(c) for c in row)
            if countable:
                p += 1
                if _is_numeric_nonzero(row[idx] if idx < len(row) else None):
                    f += 1
        return ((f / p) if p else 0.0), f

    ratio, filled = _ustun_holati(price_idx)

    muqobil_col = None
    if filled == 0 or ratio < FILL_THRESHOLD:
        # Narx BOSHQA nomdagi ustunga kiritilgan bo'lishi mumkin. Buyurtmachi
        # talabi: narx kiritilganmi — ustun NOMI emas. Ishtirokchilar amalda
        # «на единицу измерения» o'rniga «сумма» ustuniga yozishadi.
        muqobil = None
        for c, nm in enumerate(col_names):
            if c == price_idx or not nm or not _narx_nomimi(nm):
                continue
            r2, f2 = _ustun_holati(c)
            if f2 and r2 >= FILL_THRESHOLD:
                muqobil = (nm, r2)
                muqobil_col = c
                break
        if muqobil is None:
            # (2026-08-05) Keng zaxira: IXTIYORIY nomli (yorliqsiz ham)
            # ustunda jiddiy YANGI narx bo'lsa — qabul (Q2 keng talqini;
            # noo'rin rad noo'rin qabuldan yomonroq).
            et_rows_n = (etalon_sheets[et_name]
                         if etalon_sheets and et_hdr is not None else None)
            mq, mf = _muqobil_narx_ustuni(
                pt_rows, data_start, col_names, comp_idx,
                _muqobil_talab(pt_rows, data_start, comp_idx),
                tashqari={price_idx, unit_idx} - {None},
                et_rows=et_rows_n,
                et_ds=_q3_data_boshi(et_rows_n, et_hdr) if et_rows_n else 0,
                et_band_idx=et_comp_idx)
            if mq is not None:
                nom = _korinish(col_names[mq]) \
                    if mq < len(col_names) and col_names[mq] \
                    else f"{mq + 1}-ustun (yorliqsiz)"
                muqobil = (nom, None)
                muqobil_col = mq
                findings.append({
                    "code": "PRICE_IN_OTHER_COLUMN", "sheet": pt_name,
                    "severity": "note",
                    "detail_uz": f"Narx «{narx_nomi}» o'rniga «{nom}» "
                                 f"ustuniga kiritilgan ({mf} ta qiymat)."})
        elif muqobil:
            findings.append({
                "code": "PRICE_IN_OTHER_COLUMN", "sheet": pt_name, "severity": "note",
                "detail_uz": f"Narx «{narx_nomi}» o'rniga «{_korinish(muqobil[0])}» "
                             f"ustuniga kiritilgan ({muqobil[1]*100:.0f}%)."})
        if muqobil is None:
            findings.append({"code": "PRICE_EMPTY", "sheet": pt_name,
                             "detail_uz": f"«{narx_nomi}» ustuni to'ldirilmagan "
                                          f"(narxlanadigan qatorlarning {ratio*100:.0f}% i to'ldirilgan)."})

    # ── Q3: «jiddiy to'ldirish» (faqat boshqa kamchilik topilmaganda) ─────
    # Maxraj ETALON bandlaridan: ishtirokchi o'z faylida birlik/komponent
    # qatorlarini kamaytirib chetlab o'tolmasin (bo'sh hujjat teshigi).
    # Narx ustunlari BIRLASHGAN (union) olinadi — narx boshqa nomdagi
    # ustunga kiritilgan bo'lsa ham hisobga kiradi.
    if not _defekt_bormi(findings) and etalon_sheets and et_hdr is not None:
        pt_union = sorted({price_idx}
                          | ({muqobil_col} if muqobil_col is not None else set())
                          | {c for c, nm in enumerate(col_names)
                             if c != unit_idx and _narx_nomimi(nm)})
        et_union = sorted(({et_price_idx} if et_price_idx is not None else set()) | {
            c for c, nm in enumerate(et_cols or [])
            if c != et_unit_idx and _narx_nomimi(nm)})
        q3 = _q3_tekshir(
            "narxlar", pt_name, narx_nomi,
            etalon_sheets[et_name], _q3_data_boshi(etalon_sheets[et_name], et_hdr),
            et_comp_idx, et_union,
            pt_rows, data_start, comp_idx, pt_union)
        if q3:
            findings.append(q3)
    return findings
