# -*- coding: utf-8 -*-
"""tender_engine — hujjat tekshiruv dvigateli (tekshiruv MANTIG'I shu yerda).

Modullar:
    reader     — Excel o'qish (xlsx/xls, merge ochish, keng varaq, buzuq nomlar)
                 + tiklash_* zaxira zanjiri (rc4, xom_xml, nol_bayt, xlsb)
    normalize  — matn normallashtirish (homoglif fold, NBSP, kalit so'z)
    roles      — fayl rolini aniqlash (jamlanma/narxlar/resurs/loyiha)
    structure  — varaq juftlash va ustun moslashtirish
    rules/     — har rolning qoidasi (bitta modul — bitta hujjat turi)
    validate   — `validate_one` (kirish nuqtasi), etalon o'qish keshi
    decision   — yakuniy hukm (5 ichki status + shakl-erkin hukm)
    evidence   — verdikt dalillari (validation_evidence yozuvi)

Bog'liqlik bir tomonlama: reader/normalize ← roles, structure ← rules ←
validate ← decision. Xizmat qatlami (`app/`) dvigatelni ishlatadi, dvigatel
esa `app/` ni BILMAYDI.

Bu yerda submodullar ATAYLAB import qilinmaydi: `reader` openpyxl/xlrd ni
yuklaydi, kerakli modul to'g'ridan-to'g'ri import qilinadi:
`from tender_engine import reader`.
"""
