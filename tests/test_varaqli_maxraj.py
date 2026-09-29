# -*- coding: utf-8 -*-
"""#12-V3 (2026-09-08) — Q3 nisbati ENG YAXSHI VARAQ bo'yicha, FAQAT tegilgan
varaqlar ko'pchilik bo'lsa (`decision.jiddiy_varaqli`, `jiddiy_toldirilganmi`
zaxirasi).

Korpus dalili: 41201 (13 varaqdan 7 tasi tegilgan, eng yaxshisi 42/57) —
global 144/1 375 = 10% bilan rad etilardi; 19239 (9 dan 1 tegilgan) va
91143 (6 dan 1) — to'g'ri rad, V3 ularga tegmaydi.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tender_engine import decision as D


def _varaq(nom, n, ustun=3):
    return [[i, f"{nom} ish turi {i}", "м3", None] for i in range(1, n + 1)]


def _etalon(varaqlar):
    return {nom: _varaq(nom, n) for nom, n in varaqlar}


def _nusxa(et):
    return {k: [list(q) for q in rows] for k, rows in et.items()}


def _narxla(pt, nom, k, boshl=1_000_000.0):
    for i in range(k):
        pt[nom][i][3] = boshl + 12_345.0 * (i + 1)


def test_bitta_varaq_toldirilgan_qolgani_tegilmagan_RAD():
    """19239 naqshi: 9 varaqdan faqat 1 tasi narxlangan — V3 ishlamaydi.

    (Varaq nomi «Умумий» EMAS — u `_JAMI` kaliti, tavsifda uchrasa qator
    jami deb chetlanadi.)
    """
    et = _etalon([("Асосий", 20)] + [(f"Л{i}", 30) for i in range(1, 9)])
    pt = _nusxa(et)
    _narxla(pt, "Асосий", 10)                      # 50% — varaqning o'zi «jiddiy»
    ok, band, narxli = D.jiddiy_toldirilganmi(et, pt)
    assert (band, narxli) == (260, 10)              # global sonlar qaytadi
    assert ok is False
    v_ok, _, _, nom, hisob = D.jiddiy_varaqli(et, pt)
    assert v_ok is False and nom is None and hisob == (1, 9)


def test_kopchilik_varaq_tegilgan_OTADI():
    """41201 naqshi: 13 varaqdan 7 tasi tegilgan — eng yaxshi varaq 10/50=20%."""
    et = _etalon([(f"рес {i}", 50) for i in range(1, 14)])
    pt = _nusxa(et)
    for i in range(1, 8):
        _narxla(pt, f"рес {i}", 10)
    assert D.narx_nisbati(et, pt) == (650, 70)     # global 10.8% < 15% — yiqiladi
    ok, band, narxli = D.jiddiy_toldirilganmi(et, pt)
    assert ok is True and (band, narxli) == (50, 10)
    v_ok, _, _, nom, hisob = D.jiddiy_varaqli(et, pt)
    assert v_ok and hisob == (7, 13) and nom.startswith("рес ")


def test_ozchilik_varaq_tegilgan_RAD():
    """Xuddi shu hujjat, 6/13 tegilgan — ko'pchilik emas, V3 o'chadi."""
    et = _etalon([(f"рес {i}", 50) for i in range(1, 14)])
    pt = _nusxa(et)
    for i in range(1, 7):
        _narxla(pt, f"рес {i}", 10)
    ok, band, narxli = D.jiddiy_toldirilganmi(et, pt)
    assert ok is False and (band, narxli) == (650, 60)
    assert D.jiddiy_varaqli(et, pt)[4] == (6, 13)


def test_etalondagi_sonlar_narx_emas_Q1():
    """Q1 himoyasi: narxlar etalonda allaqachon bor — varaq «tegilmagan»."""
    et = _etalon([("A", 20), ("B", 20)])
    et["A"].append(["", "Маълумот: нарх 1012345.0 ва 1024690.0", "", None])
    pt = _nusxa(et)
    _narxla(pt, "B", 2)                             # 1 012 345 / 1 024 690 — etalonda bor
    assert D.jiddiy_varaqli(et, pt)[0] is False
    assert D.jiddiy_toldirilganmi(et, pt)[0] is False


def test_nol_qiymat_emas():
    et = _etalon([("A", 20), ("B", 20)])
    pt = _nusxa(et)
    for i in range(20):
        pt["B"][i][3] = 0.0
    assert D.jiddiy_varaqli(et, pt)[0] is False
    assert D.jiddiy_toldirilganmi(et, pt)[0] is False


def test_hukm_review_izida_varaq_nomi():
    """Struktura yo'lida V3 ishlasa review_sabab qaysi jadval ekanini aytadi."""
    et = _etalon([(f"рес {i}", 50) for i in range(1, 14)])
    pt = _nusxa(et)
    for i in range(1, 8):
        _narxla(pt, f"рес {i}", 10)
    eski = {"file": "f.xlsx", "role": "resurs", "status": 2, "comment_uz": "x",
            "findings": [{"code": "SHEET_DELETED", "sheet": "рес 13",
                          "detail_uz": "«рес 13» jadvali o'chirilgan."}]}
    h = D.hukm("resurs", et, pt, "f.xlsx", eski_res=eski)
    assert h["tashqi"] == 1, h
    assert "jadvali bo'yicha" in (h.get("review_sabab") or ""), h.get("review_sabab")
    assert h["band"] == 50 and h["narxli_band"] == 10
