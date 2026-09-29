# -*- coding: utf-8 -*-
"""#11-b (2026-09-07) — REVIEW yo'lida «nol = qiymat emas».

`hukm` ning REVIEW→1 flip'lari (`narx_ustuni_toldirilganmi`,
`bosh_ustun_toldirilganmi`, `jiddiy_toldirilganmi`) nollarni «qiymat» deb
sanashi mumkin edi — 83571/108998 (4 ustun × 11 qator faqat 0) shu yo'l
bilan qabul bo'lgan. Guard: hujjatda bironta NOLMAS yangi son bo'lmasa —
flip yo'q. 90704 (narx ustuni nol, lekin boshqa joyda 12 272 nolmas son)
va ming-so'mli shakllar TEGILMAYDI — guard faqat haqiqatan bo'sh hujjatga.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tender_engine import decision as D
from tender_engine import evidence as E
from tender_engine.validate import validate_one, STATUS_DEFECT

ETALON = {"Лист1": [
    ["№", "Наименование", "Ед.", "Кол-во", "Цена", "Сумма"],
    [1, "Цемент М400", "т", 12.5, None, None],
    [2, "Арматура А500", "т", 3.25, None, None],
    [3, "Песок", "м3", 40, None, None],
    [4, "Щебень", "м3", 55, None, None],
    [5, "Кирпич", "тыс.шт", 20, None, None],
    ["", "ИТОГО", "", "", None, None],
]}


def _n(v):
    return {k: [list(q) for q in rows] for k, rows in v.items()}


def _hukm(pt):
    # `hukm` ni to'g'ridan-to'g'ri sinaymiz: eski dvigatel natijasi qo'lda —
    # status=2, PRICE_EMPTY (fikstura umumiy sarlavhali, validate_one unga
    # ETALON_UNPARSED beradi; bu yerda sinalayotgan narsa guard, u emas).
    res = {"file": "f.xlsx", "role": "narxlar", "status": STATUS_DEFECT,
           "findings": [{"code": "PRICE_EMPTY", "sheet": "Лист1",
                         "detail_uz": "narx ustuni bo'sh"}],
           "comment_uz": "narx ustuni bo'sh"}
    return D.hukm("narxlar", ETALON, pt, "f.xlsx", eski_res=res)


def test_faqat_nollar_review_bolmaydi():
    """83571 naqshi: narx kataklarining hammasiga 0 — REVIEW→1 YO'Q."""
    pt = _n(ETALON)
    for i in range(1, 6):
        pt["Лист1"][i][4] = 0
        pt["Лист1"][i][5] = 0
    assert D._nolmas_yangi_son(ETALON, pt) == 0
    h = _hukm(pt)
    assert h["tashqi"] == 2
    assert h["ichki"] != E.REVIEW_AMBIGUOUS
    assert h["review_sabab"] is None


def test_haqiqiy_narx_bolsa_guard_ishlamaydi():
    """Bitta haqiqiy narx ham bor — guard o'chadi (flip qatlamlariga yo'l ochiq)."""
    pt = _n(ETALON)
    for i in range(1, 6):
        pt["Лист1"][i][4] = 0
    pt["Лист1"][1][4] = 1_250_000.0
    assert D._nolmas_yangi_son(ETALON, pt) == 1
    h = _hukm(pt)
    # Guard o'tkazib yubordi: `hukm` struktura yo'liga o'tib `band` ni
    # hisoblagan (guard qaytarsa band=None bo'lardi).
    assert h["band"] is not None
    assert h["band"] >= 5


def test_90704_naqshi_tegilmaydi():
    """Narx ustuni nol, lekin boshqa ustunda nolmas yangi sonlar — guard False."""
    pt = _n(ETALON)
    for i in range(1, 6):
        pt["Лист1"][i][4] = 0
        pt["Лист1"][i][3] = 2509.0 + i          # miqdor ustuni qayta hisoblangan
    assert D._nolmas_yangi_son(ETALON, pt) == 5
    assert not (D._nolmas_yangi_son(ETALON, pt) == 0
                and D.yangi_narxsimon_soni(ETALON, pt) == 0
                and D.narx_nisbati(ETALON, pt)[1] == 0)
