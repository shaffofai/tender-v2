# -*- coding: utf-8 -*-
"""RESURS (Физобъемлар / КАП_РЕМОНТ) — ADDITIVE / ARALASH.

Ko'p varaqli smeta: har varaq etalon varag'iga juftlanadi, narx
ustunlari to'ldirilganmi tekshiriladi.
(Ilgari `set_validator.py` da — ko'chirilgan, o'zgartirilmagan.)
"""

from tender_engine.normalize import normk_asosiy as _norm
from tender_engine.rules.common import (
    _col_nonempty,
    _col_stats,
    _distinct_width,
    _etalon_ustun_nomi,
    _jadval_tegilmagan,
    _parse_resource_sheet,
    _price_col_indices,
    _q3_tekshir,
)
from tender_engine.structure import _lcs_col_map, _varaq_moslash


# ---------------------------------------------------------------------------
# RESURS (Физобъемлар / КАП_РЕМОНТ) — ADDITIVE / ARALASH
# ---------------------------------------------------------------------------

def validate_resurs(etalon_sheets, part_sheets):
    findings = []
    if not part_sheets:
        findings.append({"code": "FILE_EMPTY", "sheet": None,
                         "detail_uz": "Fayl bo'sh yoki jadvallar yo'q."})
        return findings
    if not etalon_sheets:
        # etalonsiz resursda HECH NARSA tekshirib bo'lmaydi — jim "to'g'ri"
        # deb o'tkazish mumkin emas (yolg'on status 1 xavfi)
        findings.append({"code": "ETALON_MISSING", "sheet": None,
                         "detail_uz": "Buyurtmachi etaloni topilmadi — resurs faylini "
                                      "solishtirib tekshirib bo'lmadi."})
        return findings

    et_map = {_norm(k): k for k in etalon_sheets}
    # Varaqni NOMI bo'yicha emas, mazmuni bo'yicha ham topamiz: Excel tili
    # yoki nusxa qo'shimchasi tufayli o'zgargan nom «jadval o'chirilgan»
    # degani emas.
    pt_map = _varaq_moslash(etalon_sheets, part_sheets)

    # 1. VARAQLAR TO'PLAMI — etalondagi har bir varaq bo'lishi shart
    for nrm, orig in et_map.items():
        if nrm not in pt_map:
            findings.append({"code": "SHEET_DELETED", "sheet": orig,
                             "detail_uz": f"«{orig}» jadvali o'chirilgan — "
                                          f"buyurtmachi shablonidagi jadval mavjud emas."})

    seen = set()   # takroriy findinglarni oldini olish
    sheets_missing_price = []   # narx qo'shilmagan varaqlar (varaq-darajali talab)

    def _add(code, sheet, detail, severity=None):
        key = (code, sheet, detail)
        if key not in seen:
            seen.add(key)
            f = {"code": code, "sheet": sheet, "detail_uz": detail}
            if severity:
                f["severity"] = severity
            findings.append(f)

    for nrm, et_orig in et_map.items():
        et_rows = etalon_sheets[et_orig]
        et_info = _parse_resource_sheet(et_rows)
        et_price_cols = _price_col_indices(et_info["col_names"]) if et_info else []
        # Q3 uchun: shu jadvalda DEFEKT chiqdimi (note lar sanalmaydi)
        oldingi_defekt = sum(1 for f in findings
                             if f.get("severity") not in ("note",))

        if nrm not in pt_map:
            continue  # allaqachon SHEET_DELETED sifatida qayd etilgan
        pt_rows = part_sheets[pt_map[nrm]]
        pt_info = _parse_resource_sheet(pt_rows)
        if et_info is None:
            # Etalon varag'ining o'zi jadval emas (muqova/xulosa varag'i) —
            # ishtirokchidan talab qo'yib bo'lmaydi. Bu tekshiruv ishtirokchi
            # varag'idan OLDIN turishi shart: aks holda ikkalasi ham o'qilmasa
            # ayb ishtirokchiga yozilardi («tushunmadim» ≠ «rad etaman»).
            continue
        if pt_info is None:
            _add("HEADER_NOT_FOUND", pt_map[nrm],
                 f"«{pt_map[nrm]}» jadvalida sarlavha topilmadi.")
            continue

        pt_ds = pt_info["data_start"]
        et_names = et_info["col_names"]
        pt_names = pt_info["col_names"]

        # USTUN MOSLASHUVI: etalon ustunlari ishtirokchida NOM bo'yicha topiladi —
        # ishtirokchi o'rtaga ustun qo'shsa (surilish) ham to'g'ri ishlaydi.
        # Faqat HAQIQIY (distinct) kenglik olinadi: sarlavha ro'yxatidagi quyruq
        # bo'sh (phantom) ustunlar va merge-kengaytirilgan sarlavha/futer
        # matnlari moslashuvga kiritilmaydi, aks holda ular ishtirokchi
        # qo'shgan ustunni noto'g'ri "egallab" qo'yadi.
        et_total = _distinct_width(et_rows)
        pt_total = _distinct_width(pt_rows)
        et_list = [et_names[c] if c < len(et_names) else "" for c in range(et_total)]
        pt_list = [pt_names[c] if c < len(pt_names) else "" for c in range(pt_total)]
        colmap = _lcs_col_map(et_list, pt_list)
        mapped_pt = set(colmap.values())

        # (5-taklif, 2026-08-04) Buyurtmachining O'ZI to'ldirib bergan jadval:
        # BARCHA narx ustunlari etalonda allaqachon qiymatli bo'lsa, ishtirokchi
        # bu jadvalga tegmasligi TABIIY — «to'ldirilmagan» deb ayblanmaydi.
        # (Haqiqiy bazada shunday 24 ta etalon topildi — 99 ta fayl faqat shu
        # sabab rad etilgan edi.)
        et_prefilled = bool(et_price_cols) and all(
            _col_stats(et_rows, et_info["data_start"], c, skip_numbering=True)[0] > 0
            for c in et_price_cols)

        # (0) JADVAL BUTUNLAY TEGILMAGANMI? Ishtirokchi shu jadvalga hech narsa
        #     qo'shmagan bo'lsa, ayrim ustunlarni sanab o'tirish chalg'itadi —
        #     bitta aniq xabar beramiz va bu jadval bo'yicha boshqa tekshiruv
        #     o'tkazmaymiz.
        if _jadval_tegilmagan(et_rows, pt_rows, pt_ds, max(et_total, pt_total)):
            if et_prefilled:
                _add("SHEET_PREFILLED", pt_map[nrm],
                     f"«{pt_map[nrm]}» jadvali buyurtmachi tomonidan oldindan "
                     f"to'ldirilgan — ishtirokchidan qiymat talab qilinmaydi.",
                     severity="note")
                continue
            _add("SHEET_UNFILLED", pt_map[nrm],
                 f"«{pt_map[nrm]}» jadvali umuman to'ldirilmagan — buyurtmachi "
                 f"shablonidagi holatida qoldirilgan.")
            continue

        # (a) ETALONDA MAVJUD, BO'SH narx ustunlari -> ishtirokchi to'ldirishi SHART
        # Eslatma: skip_numbering=True — sahifa boshida takrorlanadigan
        # '1 2 3 4...' raqamlash qatorlari «soxta to'ldirilgan» deb sanalmasin
        # (2026-08-05) Hukm (b) bosqichidan KEYIN chiqariladi: ishtirokchi
        # majburiy ustunni emas, O'ZI QO'SHGAN ustunni to'ldirgan bo'lsa —
        # bu Q2 bo'yicha qabul (noo'rin rad noo'rin qabuldan yomonroq).
        bosh_majburiy = []
        for c in et_price_cols:
            ef, _ = _col_stats(et_rows, et_info["data_start"], c, skip_numbering=True)
            if ef > 0:
                continue  # etalonning o'zida to'ldirilgan (ma'lumotnoma ustuni) -> talab emas
            # Izohga etalonning ASL sarlavhasi tushsin (kichraytirilmagan) —
            # aralash klaviaturada yozilgan nomlar buzuq ko'rinmasin.
            colname = _etalon_ustun_nomi(
                et_info.get("col_names_xom"), et_info["col_names"], c, "")
            pt_c = colmap.get(c, c)
            pf, _pe = _col_stats(pt_rows, pt_ds, pt_c, skip_numbering=True)
            if pf == 0:
                bosh_majburiy.append(colname)

        # (b) QO'SHILGAN ustunlar = etalonga moslanmagan ishtirokchi ustunlari.
        #     Nomi/tili muhim emas — qiymat bo'lishi muhim.
        sheet_added_numeric = 0
        bosh_ustun_qayd = False      # shu jadvalda ADDED_COLUMN_EMPTY yozildimi
        for j in range(pt_total):
            if j in mapped_pt:
                continue
            pf, _pe = _col_stats(pt_rows, pt_ds, j, skip_numbering=True)
            nonempty = _col_nonempty(pt_rows, pt_ds, j)
            # Ishtirokchi qo'shgan ustun nomi ham asl registrda ko'rsatiladi
            hdr_name = _etalon_ustun_nomi(
                pt_info.get("col_names_xom"), pt_names, j, "")
            if pf == 0 and nonempty == 0:
                if hdr_name:
                    # sarlavhasi bor, ichi butunlay bo'sh qo'shilgan ustun -> kamchilik
                    _add("ADDED_COLUMN_EMPTY", pt_map[nrm],
                         f"«{pt_map[nrm]}» jadvalida qo'shilgan «{hdr_name}» ustuni "
                         f"bo'sh — qiymatlar kiritilmagan.")
                    bosh_ustun_qayd = True
                continue
            sheet_added_numeric += pf

        # (a-hukm) Majburiy bo'sh ustunlar: ishtirokchi ularni EMAS, o'zi
        #     qo'shgan ustunlarni to'ldirgan bo'lsa — eslatma bilan qabul (Q2).
        if bosh_majburiy:
            # kamida 3 ta qiymat — Q3 ning mutlaq chegarasi (bitta tasodifiy
            # son bilan majburiy ustun talabi bekor bo'lmasin)
            if sheet_added_numeric >= 3:
                _add("PRICE_IN_OTHER_COLUMN", pt_map[nrm],
                     f"«{pt_map[nrm]}» jadvalida narx etalon ustuniga emas, "
                     f"ishtirokchi qo'shgan ustun(lar)ga kiritilgan "
                     f"({sheet_added_numeric} ta qiymat).", severity="note")
            else:
                for colname in bosh_majburiy:
                    _add("PRICE_EMPTY", pt_map[nrm],
                         f"«{pt_map[nrm]}» jadvalidagi «{colname}» narx ustuni "
                         f"bo'sh — narx kiritilmagan.")

        # (c) JADVAL-DARAJALI TALAB: etalonda narx ustuni bo'lmagan jadvalga
        #     ishtirokchi narx ustuni qo'shib to'ldirishi SHART.
        #     Agar ADDED_COLUMN_EMPTY allaqachon yozilgan bo'lsa takrorlamaymiz —
        #     u aniqroq (ustun nomini ham ko'rsatadi).
        if not et_price_cols and sheet_added_numeric == 0 and not bosh_ustun_qayd:
            sheets_missing_price.append(pt_map[nrm])

        # (d) Q3 «jiddiy to'ldirish» — faqat shu jadvalda boshqa kamchilik
        #     topilmaganda. Narx ustunlari BIRLASHGAN (union): etalonning
        #     moslangan narx ustunlari + ishtirokchi QO'SHGAN raqamli ustunlar.
        hozirgi_defekt = sum(1 for f in findings
                             if f.get("severity") not in ("note",))
        if hozirgi_defekt == oldingi_defekt and pt_map[nrm] not in sheets_missing_price:
            q3_pt_cols = sorted({colmap.get(c, c) for c in et_price_cols} | {
                j for j in range(pt_total)
                if j not in mapped_pt
                and _col_stats(pt_rows, pt_ds, j, skip_numbering=True)[0] > 0})
            if q3_pt_cols:
                q3 = _q3_tekshir(
                    "resurs", pt_map[nrm], None,
                    et_rows, et_info["data_start"], None, et_price_cols,
                    pt_rows, pt_ds, None, q3_pt_cols)
                if q3:
                    _add(q3["code"], q3["sheet"], q3["detail_uz"])

    # Har jadval uchun ALOHIDA yozuv — izoh renderer'i sonini cheklaydi
    for s in sheets_missing_price:
        findings.append({"code": "NO_PRICE_FILLED", "sheet": s,
                         "detail_uz": f"«{s}» jadvalida narx ustuni "
                                      f"qo'shilmagan — narx kiritilmagan."})

    return findings
