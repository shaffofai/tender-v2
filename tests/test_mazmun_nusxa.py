# -*- coding: utf-8 -*-
"""S1b (#6, 2026-09-07) — mazmun-tenglik darvozasi: qayta saqlangan nusxa.

sha256 darvozasi baytlarga qaraydi; ishtirokchi shablonni Excel'da ochib
hech narsa kiritmasdan saqlasa baytlar o'zgaradi, MAZMUN esa aynan qoladi.
`_mazmun_nusxa_res` shu holatni `decision._bir_xilmi` (katak-ba-katak
`normk` tengligi) bilan tutadi. Bu TENGLIK, hukm emas — testlar ikki
tomonlama kafolatni qotiradi:
  - mazmun aynan teng bo'lsa RAD (+ AYNAN_NUSXA, aniq izoh);
  - har qanday farqda (bitta katak, varaq soni) JIM chetlanadi (None) —
    yolg'on «nusxa» bo'lishi mumkin emas;
  - `decision` yo'q yoki yiqilsa — None (asosiy oqim davom etadi).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.worker import gates
from tender_engine.validate import STATUS_DEFECT

ETALON = {
    "Лист1": [["№", "Наименование", "Ед.", "Кол-во", "Цена"],
              [1, "Цемент М400", "т", 12.5, None],
              [2, "Арматура А500", "т", 3.25, None],
              ["", "ИТОГО", "", "", None]],
}


def _nusxa(varaqlar):
    return {k: [list(q) for q in v] for k, v in varaqlar.items()}


def test_mazmun_teng_rad_etiladi():
    pt = _nusxa(ETALON)                      # baytlar boshqa, mazmun aynan
    res = gates._mazmun_nusxa_res("f.xlsx", "resurs", ETALON, pt)
    assert res is not None
    assert res["status"] == STATUS_DEFECT
    assert [f["code"] for f in res["findings"]] == ["AYNAN_NUSXA"]
    assert res["comment_uz"] == gates.IZOH_MAZMUN_NUSXA
    assert "qayta saqlangan" in res["comment_uz"]
    assert res["role"] == "resurs"


def test_bitta_katak_farq_chetlanadi():
    """Bitta narx kiritilgan — nusxa EMAS; darvoza aralashmaydi."""
    pt = _nusxa(ETALON)
    pt["Лист1"][1][4] = 1_250_000.0
    assert gates._mazmun_nusxa_res("f.xlsx", "resurs", ETALON, pt) is None


def test_son_yaxlitlanmaydi():
    """0.1 farq ham farq — sonlar yaxlitlanmaydi (yolg'on nusxa bo'lmasin)."""
    pt = _nusxa(ETALON)
    pt["Лист1"][1][3] = 12.6
    assert gates._mazmun_nusxa_res("f.xlsx", "resurs", ETALON, pt) is None


def test_varaq_qoshilsa_chetlanadi():
    pt = _nusxa(ETALON)
    pt["Оферта"] = [["Narx", 100]]
    assert gates._mazmun_nusxa_res("f.xlsx", "resurs", ETALON, pt) is None


def test_bosh_bolsa_none():
    # (`tender_engine` yo'qligi holati endi yo'q — dvigatel paketning majburiy qismi.)
    assert gates._mazmun_nusxa_res("f.xlsx", None, {}, _nusxa(ETALON)) is None
    assert gates._mazmun_nusxa_res("f.xlsx", None, ETALON, {}) is None
