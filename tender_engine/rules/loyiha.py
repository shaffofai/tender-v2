# -*- coding: utf-8 -*-
"""LOYIHA («5-ILOVA» / SMETA HISOBI) — FAQAT NARX USTUNI.

Kelishilgan siyosat: faqat «Qiymat» ustuni to'ldirilganmi tekshiriladi.
(Ilgari `set_validator.py` da — ko'chirilgan, o'zgartirilmagan.)
"""

import math

from tender_engine.normalize import (
    is_numeric_nonzero as _is_numeric_nonzero,
    normk_asosiy as _norm,
)
from tender_engine.roles import (
    LOYIHA_NAME_KEYWORDS,
    LOYIHA_PRICE_KEYWORDS,
    is_price_keyword,
    loyiha_varagi,
)
from tender_engine.rules.common import (
    _defekt_bormi,
    _eng_toldirilgan_ustun,
    _etalon_ustun_nomi,
    _find_row_with_any,
    _first_col_matching,
    _is_numbering_row,
    _jadval_tegilmagan,
    _jami_qatormi,
    _korinish,
    _matnli,
    _mos_ustunlar,
    _muqobil_narx_ustuni,
    _muqobil_talab,
    _norm_header_row,
    _trim_trailing_empty,
)
from tender_engine.structure import _lcs_col_map


# ---------------------------------------------------------------------------
# LOYIHA («5-ILOVA» / SMETA HISOBI) — FAQAT NARX USTUNI
# ---------------------------------------------------------------------------

def _futer_qatormi(row, price_idx):
    """Qator merge qilingan to'liq kenglikdagi matn (futer) mi?

    «SMETA HISOBI» shaklining oxirida shunday qator turadi:

        Jami: QQS bilan 1 174 200 so'm.  |  (xuddi shu)  |  (xuddi shu)  | 1236000

    Chapdagi matn bir necha ustunga merge qilingan, o'ngdagi son esa LOTNING
    boshlang'ich narxi — ishtirokchi kiritgan taklif emas. Uni «to'ldirilgan»
    deb sanash bo'sh hujjatni qabul qilib yuborardi.
    """
    qiymatlar = [_norm(row[c]) if c < len(row) else "" for c in range(max(0, price_idx))]
    mazmunli = [v for v in qiymatlar if v]
    return len(mazmunli) >= 2 and len(set(mazmunli)) == 1


def _loyiha_narx_soni(rows, data_start, price_idx):
    """Narx ustunida ishtirokchi kiritgan HAQIQIY qiymatlar soni.

    Sanaladi: narxdan oldingi ustunlarda mazmun bo'lgan qatorlar
    (band nomi yoki «Jami ...» satri). Futer qatorlari sanalmaydi.
    """
    n = 0
    for row in rows[data_start:]:
        if _is_numbering_row(row) or _futer_qatormi(row, price_idx):
            continue
        oldingi = any(_norm(row[c]) for c in range(max(0, price_idx))
                      if c < len(row))
        if not oldingi:
            continue
        qiymat = row[price_idx] if price_idx < len(row) else None
        if _is_numeric_nonzero(qiymat):
            n += 1
    return n


def validate_loyiha(etalon_sheets, part_sheets):
    """`loyiha_excel` — o'zbekcha-lotin smeta shakli, bitta varaq.

        T/r | Ish turlari | Asos | Narxning kelib chiqishi | Qiymat (ming so'm)

    KELISHILGAN SIYOSAT (2026-07-31): faqat **«Qiymat» ustuni to'ldirilganmi**
    tekshiriladi. Ustun soni, ustun nomlari va qatorlar soni MUHIM EMAS —
    ishtirokchi ustun qo'shishi ham, olib tashlashi ham, nomini o'zgartirishi
    ham mumkin. Asosiy savol bitta: o'z qiymatlarini kiritdimi.

    Shu sababli bu yerda `COLUMN_COUNT`, `COLUMN_RENAMED` kabi tekshiruvlar
    ATAYLAB yo'q — ular noo'rin rad etishga olib kelardi.
    """
    findings = []
    if not part_sheets:
        findings.append({"code": "FILE_EMPTY", "sheet": None,
                         "detail_uz": "Fayl bo'sh yoki jadvallar yo'q."})
        return findings

    # «SMETA HISOBI» varag'ini tanlaymiz — u har doim ham birinchi emas
    # (oldida «4 ИЛОВА» / КАЛЕНДАРЬ ИШ ЖАДВАЛИ turishi mumkin).
    et_name = loyiha_varagi(etalon_sheets) if etalon_sheets else None
    pt_name = loyiha_varagi(part_sheets)
    if pt_name is None and et_name is not None:
        # Ishtirokchi sarlavhani o'zgartirgan bo'lishi mumkin — nom bo'yicha
        pt_name = next((n for n in part_sheets if _norm(n) == _norm(et_name)), None)
    if pt_name is None:
        pt_name = list(part_sheets.keys())[0]
    pt_rows = part_sheets[pt_name]

    if et_name is None and etalon_sheets:
        et_name = next((n for n in etalon_sheets if _norm(n) == _norm(pt_name)),
                       list(etalon_sheets.keys())[0])
    et_rows = etalon_sheets[et_name] if et_name else []

    # ── etalon konteksti ───────────────────────────────────────────────────
    et_cols = et_xom_hdr = None
    et_hdr = et_price_idx = None
    if et_rows:
        et_hdr = _find_row_with_any(et_rows, LOYIHA_NAME_KEYWORDS)
        if et_hdr is None:
            et_hdr = _find_row_with_any(et_rows, LOYIHA_PRICE_KEYWORDS)
        if et_hdr is not None:
            et_xom_hdr = et_rows[et_hdr]
            et_cols = _trim_trailing_empty(_norm_header_row(et_xom_hdr))
            et_price_idx = _first_col_matching(et_cols, LOYIHA_PRICE_KEYWORDS)

    narx_nomi = _etalon_ustun_nomi(et_xom_hdr, et_cols, et_price_idx, "Qiymat")

    # ── ishtirokchi sarlavhasi ─────────────────────────────────────────────
    hdr = _find_row_with_any(pt_rows, LOYIHA_NAME_KEYWORDS)
    if hdr is None:
        hdr = _find_row_with_any(pt_rows, LOYIHA_PRICE_KEYWORDS)
    if hdr is None and et_hdr is not None and et_hdr < len(pt_rows):
        hdr = et_hdr        # nomlar o'zgartirilgan — etalon pozitsiyasi
    if hdr is None:
        findings.append({"code": "HEADER_NOT_FOUND", "sheet": pt_name,
                         "detail_uz": "Jadval sarlavhasi topilmadi — hujjat "
                                      "«SMETA HISOBI» shakliga mos emas."})
        return findings

    col_names = _trim_trailing_empty(_norm_header_row(pt_rows[hdr]))

    # Sarlavha ikki qatorga bo'lingan bo'lishi mumkin (r6 va r7 bir xil)
    data_start = hdr + 1
    while data_start < len(pt_rows) and _is_numbering_row(pt_rows[data_start]):
        data_start += 1
    if data_start < len(pt_rows):
        keyingi = _norm_header_row(pt_rows[data_start])
        if _first_col_matching(keyingi, LOYIHA_PRICE_KEYWORDS) is not None:
            data_start += 1

    # ── narx ustuni: nomi bo'yicha, topilmasa etalon moslashuvi ────────────
    # (2026-08-04) «Qiymat (ming so'm)» nomli ustun IKKITA bo'lishi mumkin:
    # birinchisi matnli izoh («Obyektning taxminiy QMI qiymati …»), haqiqiy
    # narx keyingisida. Shuning uchun nomzodlar ichidan raqamlarga ENG BOYI
    # tanlanadi — birinchisi emas.
    price_idx = _eng_toldirilgan_ustun(
        _mos_ustunlar(col_names, LOYIHA_PRICE_KEYWORDS), pt_rows, data_start)
    if price_idx is None and et_cols and et_price_idx is not None:
        colmap = _lcs_col_map(et_cols, col_names)
        price_idx = colmap.get(et_price_idx, et_price_idx)
    if price_idx is None:
        # Zaxira: shaklda narx doim ENG O'NGDAGI ustun
        price_idx = len(col_names) - 1 if col_names else None
    if price_idx is None or price_idx < 0:
        findings.append({"code": "COLUMN_MISSING", "sheet": pt_name,
                         "detail_uz": f"«{narx_nomi}» ustuni topilmadi."})
        return findings

    # ── jadval umuman tegilmaganmi ────────────────────────────────────────
    if et_rows and _jadval_tegilmagan(et_rows, pt_rows, data_start,
                                      max(len(col_names), len(et_cols or []))):
        findings.append({"code": "SHEET_UNFILLED", "sheet": pt_name,
                         "detail_uz": "Jadval umuman to'ldirilmagan — "
                                      "buyurtmachi shablonidagi holatida qoldirilgan."})
        return findings

    # ── ASOSIY TEKSHIRUV: qiymat kiritilganmi ─────────────────────────────
    # Etalonda bu ustun bo'sh turadi. Ishtirokchi undan KO'PROQ qiymat
    # kiritgan bo'lishi kerak — shunda «o'z narxini qo'shdi» deymiz.
    pt_filled = _loyiha_narx_soni(pt_rows, data_start, price_idx)
    et_filled = 0
    if et_rows and et_price_idx is not None:
        et_data_start = (et_hdr + 1) if et_hdr is not None else data_start
        et_filled = _loyiha_narx_soni(et_rows, et_data_start, et_price_idx)

    if pt_filled == 0 or pt_filled <= et_filled:
        # MUQOBIL USTUN ZAXIRASI (2026-08-05): qiymat boshqa/qo'shilgan
        # (hatto yorliqsiz) ustunda bo'lishi mumkin — Q2 keng talqini.
        band_idx = _first_col_matching(col_names, LOYIHA_NAME_KEYWORDS)
        if band_idx is None:
            band_idx = 1 if len(col_names) > 1 else 0
        et_ds_l = (et_hdr + 1) if et_hdr is not None else data_start
        mq, mf = _muqobil_narx_ustuni(
            pt_rows, data_start, col_names, band_idx,
            _muqobil_talab(pt_rows, data_start, band_idx),
            tashqari={price_idx},
            et_rows=et_rows if et_rows else None, et_ds=et_ds_l)
        if mq is not None:
            nom = _korinish(col_names[mq]) \
                if mq < len(col_names) and col_names[mq] \
                else f"{mq + 1}-ustun (yorliqsiz)"
            findings.append({
                "code": "PRICE_IN_OTHER_COLUMN", "sheet": pt_name,
                "severity": "note",
                "detail_uz": f"Qiymat «{narx_nomi}» o'rniga «{nom}» ustuniga "
                             f"kiritilgan ({mf} ta qiymat)."})
            price_idx = mq
            pt_filled = _loyiha_narx_soni(pt_rows, data_start, price_idx)
            et_filled = (_loyiha_narx_soni(et_rows, et_ds_l, price_idx)
                         if et_rows else 0)

    if pt_filled == 0 or pt_filled <= et_filled:
        findings.append({"code": "PRICE_EMPTY", "sheet": pt_name,
                         "detail_uz": f"«{narx_nomi}» ustuni to'ldirilmagan — "
                                      f"qiymatlar kiritilmagan."})

    # ── Q3: «jiddiy to'ldirish» (faqat boshqa kamchilik topilmaganda) ─────
    # `_loyiha_narx_soni` JAMI qatorlaridagi sonlarni ham sanaydi — faqat
    # «Jami: … so'm» yozib qo'yilgan bo'sh hujjat shu yerda ushlanadi.
    if not _defekt_bormi(findings) and et_rows and et_hdr is not None:
        def _loy_futer(rows_, pidx):
            # futer qatorlarini Q3 dan oldin belgilab olamiz — ular band emas
            return {i for i, row in enumerate(rows_)
                    if _futer_qatormi(row, pidx)}

        def _loy_hisob(rows_, ds_, pidx, futerlar):
            band = priced = jami = 0
            for i in range(ds_, len(rows_)):
                row = rows_[i]
                if _is_numbering_row(row):
                    continue
                pv = _norm(row[pidx] if pidx < len(row) else None)
                if pv and is_price_keyword(pv, "loyiha"):
                    continue          # sarlavha qoldig'i
                son = _is_numeric_nonzero(row[pidx] if pidx < len(row) else None)
                if i in futerlar or _jami_qatormi(row):
                    if son:
                        jami += 1
                    continue
                if not any(_matnli(row[c]) for c in range(max(0, pidx))
                           if c < len(row)):
                    continue
                band += 1
                if son:
                    priced += 1
            return band, priced, jami

        e_pidx = et_price_idx if et_price_idx is not None else price_idx
        et_ds2 = (et_hdr + 1) if et_hdr is not None else data_start
        eb, ep, ej = _loy_hisob(et_rows, et_ds2, e_pidx,
                                _loy_futer(et_rows, e_pidx))
        pb, pp, pj = _loy_hisob(pt_rows, data_start, price_idx,
                                _loy_futer(pt_rows, price_idx))
        N = eb - ep
        F = max(0, pp - ep)
        T = max(0, pj - ej)
        if N >= 1:
            N_eff = min(N, pb) if pb > 0 else N
            qavat = 3 if N >= 3 else N
            talab = min(N, max(qavat, min(30, math.ceil(0.30 * N_eff))))
            if F < talab:
                if F == 0 and T > 0:
                    findings.append({
                        "code": "PRICE_ONLY_TOTAL", "sheet": pt_name,
                        "detail_uz": f"Faqat umumiy (jami) qiymat kiritilgan — "
                                     f"{N} ta ish turiga alohida qiymat "
                                     f"kiritilmagan."})
                else:
                    findings.append({
                        "code": "PRICE_SPARSE", "sheet": pt_name,
                        "detail_uz": f"«{narx_nomi}» ustunida {N} ta ish "
                                     f"turidan atigi {F} tasiga qiymat "
                                     f"kiritilgan — hujjat jiddiy "
                                     f"to'ldirilmagan."})
        elif pp == 0 and T > 0:
            # SKELET etalon (bandlar bo'sh — ishtirokchi qatorlarni o'zi
            # qo'shadi): bironta band narxlanmasdan faqat «Jami» qatoriga son
            # yozilgan — «faqat jami» teshigi bu holatda ham yopiq turadi.
            findings.append({
                "code": "PRICE_ONLY_TOTAL", "sheet": pt_name,
                "detail_uz": "Faqat umumiy (jami) qiymat kiritilgan — ish "
                             "turlariga alohida qiymat kiritilmagan."})
    return findings
