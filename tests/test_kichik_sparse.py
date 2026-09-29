# -*- coding: utf-8 -*-
"""#9b (2026-09-07) — PRICE_SPARSE blokida kichik shakl (band < 5) uchun
#9-yumshatilgan `jiddiy_toldirilganmi` ikkinchi imkoniyati.

Eski dvigatel 4 bandli jadvalga PRICE_SPARSE bergan (5 ga yetib bo'lmaydi);
`narx_ustuni_toldirilganmi` sarlavha bo'yicha ustunni topa olmasa ham,
>=50% band narxlangan VA >=5 narxsimon katak bo'lsa — REVIEW→1.
FAQAT band < NARX_MIN_BAND: 5 va undan katta shakllar o'zgarmaydi
(#8 ning 12 B-regressiyasi hammasi band >= 5 edi).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tender_engine import decision as D
from tender_engine import evidence as E
from tender_engine.validate import STATUS_DEFECT

ETALON = {"Свод": [
    ["№", "Объект", "Смет.ст-ть", "НДС", "Всего", "Прочие"],
    [1, "МФЙ Навбахор — капремонт", None, None, None, None],
    [2, "МФЙ Гулистон — капремонт", None, None, None, None],
    [3, "МФЙ Чилонзор — капремонт", None, None, None, None],
    [4, "МФЙ Юнусобод — капремонт", None, None, None, None],
    ["", "ИТОГО", None, None, None, None],
]}
SPARSE = {"file": "f.xlsx", "role": "jamlanma", "status": STATUS_DEFECT,
          "findings": [{"code": "PRICE_SPARSE", "sheet": "Свод",
                        "detail_uz": "4 banddan 2 tasiga qiymat kiritilgan"}],
          "comment_uz": "siyrak"}


def _n(v):
    return {k: [list(q) for q in rows] for k, rows in v.items()}


def test_kichik_shakl_toliq_qatorlar_REVIEW():
    """4 band, 2 tasi 3 ustun bilan narxlangan (6 katak) — #9b REVIEW→1."""
    pt = _n(ETALON)
    pt["Свод"][1][2:5] = [471_841_805.0, 56_620_016.0, 528_461_821.0]
    pt["Свод"][2][2:5] = [65_851_109.0, 7_902_133.0, 73_753_242.0]
    band, narxli = D.narx_nisbati(ETALON, pt)
    assert band < D.NARX_MIN_BAND and narxli == 2
    assert D.yangi_narxsimon_soni(ETALON, pt) >= D.NARX_MIN_BAND
    h = D.hukm("jamlanma", ETALON, pt, "f.xlsx", eski_res=SPARSE)
    assert h["tashqi"] == 1 and h["ichki"] == E.REVIEW_AMBIGUOUS
    # P1 (2026-09-11): #9b bloki umumiy o'z-hujjat Q3 blokiga kengaytirildi —
    # xulq bir xil (REVIEW→1), iz matni endi band nisbatini ko'rsatadi.
    assert "2/4" in h["review_sabab"] and "narxlangan" in h["review_sabab"]
    assert h["band"] == 4 and h["narxli_band"] == 2


def test_kichik_shakl_kam_katak_RAD():
    """4 band, 2 tasi narxlangan, lekin jami 3 katak — teshik yopiq, rad."""
    pt = _n(ETALON)
    pt["Свод"][1][2] = 471_841_805.0
    pt["Свод"][2][2] = 65_851_109.0
    pt["Свод"][2][3] = 7_902_133.0
    h = D.hukm("jamlanma", ETALON, pt, "f.xlsx", eski_res=SPARSE)
    assert h["tashqi"] == 2


def test_katta_shakl_ozgarmaydi():
    """10 band, 2 tasi to'liq narxlangan — band >= 5: #9b ishlamaydi, rad."""
    et = _n(ETALON)
    for i in range(5, 11):
        et["Свод"].insert(i, [i, f"МФЙ №{i} — капремонт", None, None, None, None])
    pt = _n(et)
    pt["Свод"][1][2:5] = [471_841_805.0, 56_620_016.0, 528_461_821.0]
    pt["Свод"][2][2:5] = [65_851_109.0, 7_902_133.0, 73_753_242.0]
    band, narxli = D.narx_nisbati(et, pt)
    assert band >= D.NARX_MIN_BAND and narxli == 2
    h = D.hukm("jamlanma", et, pt, "f.xlsx", eski_res=SPARSE)
    assert h["tashqi"] == 2
