# -*- coding: utf-8 -*-
"""P2 hukm (REVIEW tasnifi) invariantlari — baza kerak emas."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tender_engine import decision as d
from tender_engine import evidence as ev


def _res(status, kodlar=()):
    return {"status": status, "role": "resurs",
            "findings": [{"code": k} for k in kodlar],
            "comment_uz": ""}


# 40 ta narxsimon YANGI son bor varaq (kasrli qiymatlar) — hamma band narxlangan
_PT_NARXLI = {"Оферта": [[f"ish {i}", 1234.5 + i] for i in range(40)]}
_ET = {"Лист1": [["№", "nomi", "narx"], [1, "ish", None], [2, "ish2", None]]}

# SIYRAK: 400 band, atigi 35 tasi narxlangan (~9%) — chegara 15%, RAD bo'lsin
_PT_SIYRAK = {"Оферта": [[f"ish {i}", (1234.5 + i) if i < 35 else None]
                         for i in range(400)]}


def test_review_qayta_tuzilgan():
    """Strukturaviy rad + jiddiy yangi narxlar -> REVIEW (582/588 sinfi)."""
    h = d.hukm("resurs", _ET, _PT_NARXLI, "x",
               eski_res=_res(2, ["SHEET_DELETED"]))
    assert h["ichki"] == ev.REVIEW_AMBIGUOUS
    assert h["tashqi"] == 1
    assert h["yangi_raqamlar"] >= 40
    assert "narxlangan" in h["review_sabab"]


def test_review_emas_aynan_nusxa():
    """Q1: nusxada YANGI son yo'q -> REVIEW chegaraga yetmaydi, rad qoladi."""
    pt = {"Оферта": [q[:] for q in _ET["Лист1"]]}
    h = d.hukm("resurs", _ET, pt, "x", eski_res=_res(2, ["SHEET_UNFILLED"]))
    assert h["ichki"] == ev.STRUCTURE_VIOLATION
    assert h["tashqi"] == 2


def test_review_emas_q3_siyrak():
    """Q3 belgisi bor va ishtirokchining O'Z hujjati ham SIYRAK (400 banddan 35
    tasi, ~9%) faylga REVIEW berilmaydi — bo'sh/siyrak-hujjat invarianti.

    2026-09-11 gacha bu test 40/40 narxlangan hujjat bilan yozilgan edi
    (PRICE_SPARSE bor — rad). P1 (buyurtmachi qarori, 2026-09-11): PRICE_SPARSE
    etalon qatorlariga moslashtirilgan nisbatdan kelib chiqqan bo'lsa,
    ishtirokchining o'z hujjatidagi Q3 so'raladi — 40/40 endi REVIEW→1
    (`test_review_q3_sparse_oz_hujjat_toldirilgan`). Siyrak hujjat RAD ligicha.
    """
    h = d.hukm("resurs", _ET, _PT_SIYRAK, "x",
               eski_res=_res(2, ["SHEET_DELETED", "PRICE_SPARSE"]))
    assert h["ichki"] != ev.REVIEW_AMBIGUOUS
    assert h["tashqi"] == 2


def test_review_q3_sparse_oz_hujjat_toldirilgan():
    """P1 (2026-09-11): PRICE_SPARSE + o'z hujjatida 40/40 narxlangan → REVIEW→1.
    SHEET_UNFILLED bilan kelsa esa tegilmaydi (jadval shablon holatida)."""
    h = d.hukm("resurs", _ET, _PT_NARXLI, "x",
               eski_res=_res(2, ["SHEET_DELETED", "PRICE_SPARSE"]))
    assert h["ichki"] == ev.REVIEW_AMBIGUOUS
    assert h["tashqi"] == 1
    assert h["narxli_band"] == 40 and "PRICE_SPARSE" in h["review_sabab"]
    h2 = d.hukm("resurs", _ET, _PT_NARXLI, "x",
                eski_res=_res(2, ["SHEET_UNFILLED", "PRICE_SPARSE"]))
    assert h2["tashqi"] == 2


def test_narx_bosh_lekin_hujjat_toldirilgan():
    """«Narx bor — qabul» (2026-08-12): PRICE_EMPTY etalon maxrajidan kelib
    chiqqan bo'lishi mumkin. Ishtirokchi hujjatining O'ZI to'ldirilgan bo'lsa
    — qabul (buyurtmachi slotga noto'g'ri shablon yuklagan holat)."""
    h = d.hukm("resurs", _ET, _PT_NARXLI, "x", eski_res=_res(2, ["PRICE_EMPTY"]))
    assert h["ichki"] == ev.REVIEW_AMBIGUOUS
    assert h["tashqi"] == 1
    assert h["narxli_band"] == 40


def test_siyrak_toldirilgan_baribir_rad():
    """Q3 SAQLANADI (buyurtmachi tanlovi): 400 banddan 35 tasi narxlangan
    (~9%) — tuzilma farqidan qat'i nazar RAD."""
    for kod in ("PRICE_EMPTY", "SHEET_DELETED", "PRICE_ONLY_TOTAL"):
        h = d.hukm("resurs", _ET, _PT_SIYRAK, "x", eski_res=_res(2, [kod]))
        assert h["ichki"] != ev.REVIEW_AMBIGUOUS, kod
        assert h["tashqi"] == 2, kod


def test_uch_tort_qiymat_yetarli_emas():
    """Buyurtmachi chegarasi (2026-08-12): «shunchaki 3-4 ta qiymat
    bo'lmasligi kerak» — 100% to'ldirilgan bo'lsa ham QABUL EMAS."""
    for n in (2, 3, 4):
        kichik = {"Оферта": [[f"ish {i}", 5000.5 + i] for i in range(n)]}
        h = d.hukm("resurs", _ET, kichik, "x", eski_res=_res(2, ["SHEET_DELETED"]))
        assert h["ichki"] != ev.REVIEW_AMBIGUOUS, n
        assert h["tashqi"] == 2, n


def test_chegara_15_foiz():
    """Chegara 15%: 14% RAD, 16% QABUL (band soni yetarli bo'lsa)."""
    # 100 band, 14 tasi narxlangan -> 14% -> RAD
    kam = {"Оферта": [[f"ish {i}", (9000.5 + i) if i < 14 else None]
                      for i in range(100)]}
    h = d.hukm("resurs", _ET, kam, "x", eski_res=_res(2, ["PRICE_EMPTY"]))
    assert h["tashqi"] == 2
    # 100 band, 16 tasi narxlangan -> 16% -> QABUL
    yetarli = {"Оферта": [[f"ish {i}", (9000.5 + i) if i < 16 else None]
                          for i in range(100)]}
    h = d.hukm("resurs", _ET, yetarli, "x", eski_res=_res(2, ["PRICE_EMPTY"]))
    assert h["ichki"] == ev.REVIEW_AMBIGUOUS
    assert h["tashqi"] == 1


def test_nisbat_hisobi():
    """JAMI qatori va etalonda bor sonlar hisobga olinmaydi."""
    et = {"L": [["ish", 111.5]]}
    pt = {"L": [["ish bir", 111.5],        # etalonda bor — narx emas
                ["ish ikki", 5000.75],     # yangi narx ✓
                ["ish uch", None],         # narxsiz band
                ["JAMI", 999999.5]]}       # jami qatori — band emas
    band, narxli = d.narx_nisbati(et, pt)
    assert (band, narxli) == (3, 1)


def test_review_emas_faqat_raqamlash():
    """Faqat tartib raqamlari (1..3000) narxsimon EMAS -> REVIEW yo'q."""
    pt = {"Оферта": [[i, f"ish {i}"] for i in range(1, 500)]}
    h = d.hukm("resurs", _ET, pt, "x", eski_res=_res(2, ["SHEET_DELETED"]))
    assert h["ichki"] == ev.STRUCTURE_VIOLATION
    assert (h["yangi_raqamlar"] or 0) < d.REVIEW_YANGI_RAQAM


def test_toldirilgan_va_texnik():
    assert d.hukm("r", _ET, _PT_NARXLI, "x",
                  eski_res=_res(1))["ichki"] == ev.ACCEPT_FILLED
    assert d.hukm("r", _ET, _PT_NARXLI, "x",
                  eski_res=_res(0))["ichki"] == ev.TECHNICAL_ERROR


def test_hajm_va_koeffitsiyent_narx_emas():
    """Haqiqiy fayldan (id=262): narx ustunlari BO'SH, lekin qatorlarda
    hajm (0.105, 0.6, 3.3, 60) va koeffitsiyent (0.03, 0.17) turadi —
    bular narx deb sanalmasin, aks holda bo'sh smeta qabul bo'lardi."""
    et = {"Лист1": [["№", "ish turi", "hajm", "narx"]]}
    pt = {"Лист1": [["1", "beton ish", 0.105, None],
                    ["2", "projektor", 0.6, None],
                    ["3", "quyosh paneli", 60, None],
                    ["4", "metall ustun", 3.3, None],
                    ["5", "transport", 0.03, None],
                    ["6", "boshqa xarajat", 0.17, None]]}
    band, narxli = d.narx_nisbati(et, pt)
    assert (band, narxli) == (6, 0)
    h = d.hukm("resurs", et, pt, "x", eski_res=_res(2, ["PRICE_EMPTY"]))
    assert h["tashqi"] == 2

    # Haqiqiy narxlar (ming so'mda ham) SANALADI
    narxli_pt = {"Лист1": [["1", "beton ish", 0.105, 4766559.58],
                           ["2", "projektor", 0.6, 250.75],
                           ["3", "panel", 60, 1500]]}
    band2, n2 = d.narx_nisbati(et, narxli_pt)
    assert (band2, n2) == (3, 3)


def test_yangi_narxsimon_soni():
    et = {"L": [[100.5, 2000, "1 234,56"]]}
    pt = {"L": [
        [100.5, 2000, "1 234,56"],     # etalonda bor — sanalmaydi (Q1)
        [999, 5, True, None, "matn"],  # kichik butunlar/bool/matn — sanalmaydi
        [7500, 0.25, "9 876,54"],      # 0.25 hajm — sanalmaydi; 2 ta narx
    ]}
    assert d.yangi_narxsimon_soni(et, pt) == 2
