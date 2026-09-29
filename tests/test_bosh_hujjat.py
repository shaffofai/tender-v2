# -*- coding: utf-8 -*-
"""#7 (2026-09-07) — ACCEPT yo'lida bo'sh-hujjat himoyasi.

`validate_one` 1 qaytarsa `hukm` chaqirilmaydi — ACCEPT yo'lida Q1/Q3 yo'q
edi. `decision.bosh_hujjat_qabulmi` faqat HECH QANDAY yangi qiymat bo'lmagan
hujjatni tutadi; uchta istisno (prefilled etalon, bo'sh kataklar to'ldirilgan,
bironta nolmas yangi son) uni O'CHIRADI — ustuvorlik qoidasi: ikkilanishda
qabul. Korpusda o'lchangan: B 0/2 779, A 131/185.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.worker import gates
from tender_engine.validate import STATUS_DEFECT
from tender_engine import decision as D

# Etalon: 3 band, narx ustuni BO'SH (buyurtmachi to'ldirishni kutadi)
ETALON = {"Лист1": [
    ["№", "Наименование", "Ед.", "Кол-во", "Цена", "Сумма"],
    [1, "Цемент М400", "т", 12.5, None, None],
    [2, "Арматура А500", "т", 3.25, None, None],
    [3, "Песок", "м3", 40, None, None],
    ["", "ИТОГО", "", "", None, None],
]}


def _n(v):
    return {k: [list(q) for q in rows] for k, rows in v.items()}


def test_faqat_nol_yozilgan_bosh_hujjat():
    """83571/108998 naqshi: hamma narx katagiga 0 — bo'sh hujjat."""
    pt = _n(ETALON)
    for i in (1, 2, 3):
        pt["Лист1"][i][4] = 0
        pt["Лист1"][i][5] = 0
    assert D._nolmas_yangi_son(ETALON, pt) == 0
    assert D.bosh_hujjat_qabulmi(ETALON, pt) is True


def test_faqat_matn_ozgargan_bosh_hujjat():
    pt = _n(ETALON)
    pt["Лист1"][1][1] = "Цемент М400 (Ahangaran)"      # matn, son emas
    assert D.bosh_hujjat_qabulmi(ETALON, pt) is True


def test_bitta_nolmas_yangi_son_otkazadi():
    """Ustuvorlik qoidasi: bironta nolmas yangi son — qabul qoladi."""
    pt = _n(ETALON)
    pt["Лист1"][1][4] = 7                                # kichik, narxsimon emas
    assert D._nolmas_yangi_son(ETALON, pt) == 1
    assert D.bosh_hujjat_qabulmi(ETALON, pt) is False


def test_narx_kiritilgan_otkazadi():
    pt = _n(ETALON)
    pt["Лист1"][1][4] = 1_250_000.0
    pt["Лист1"][2][4] = 9_800_000.0
    assert D.bosh_hujjat_qabulmi(ETALON, pt) is False


def test_prefilled_etalon_otkazadi():
    """Buyurtmachi shablonni o'zi narxlab yuklagan — ishtirokchidan talab yo'q."""
    et = _n(ETALON)
    for i, n in ((1, 1_000_000.0), (2, 2_000_000.0), (3, 3_000_000.0)):
        et["Лист1"][i][4] = n
    # 3 band < NARX_MIN_BAND(5) — proksi ishlamasligi uchun 5 bandga yetkazamiz
    et["Лист1"].insert(4, [4, "Щебень", "м3", 10, 4_000_000.0, None])
    et["Лист1"].insert(5, [5, "Гравий", "м3", 10, 5_000_000.0, None])
    pt = _n(et)                                           # aynan qaytarilgan
    assert D.narx_nisbati({}, et)[1] >= D.NARX_MIN_BAND
    assert D.bosh_hujjat_qabulmi(et, pt) is False


def test_bosh_kirish_otkazadi():
    assert D.bosh_hujjat_qabulmi({}, _n(ETALON)) is False
    assert D.bosh_hujjat_qabulmi(ETALON, {}) is False


def test_res_yordamchisi():
    res = gates._bosh_hujjat_res("f.xlsx", "narxlar")
    assert res["status"] == STATUS_DEFECT
    assert [f["code"] for f in res["findings"]] == ["NO_NEW_VALUES"]
    assert res["comment_uz"] == gates.IZOH_YANGI_QIYMAT_YOQ
    # `hukm` uni qayta ocholmasin: kod _REVIEW_STRUKTURA da YO'Q
    assert "NO_NEW_VALUES" not in D._REVIEW_STRUKTURA
