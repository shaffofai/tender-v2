# -*- coding: utf-8 -*-
"""#14-son (2026-09-10) — shakl tanilmagan yo'lda «ko'p yangi narx qiymati».

Buyurtmachi ko'rsatmasi: buyurtmachi faylida nima bo'lsa ham ishtirokchi
fayliga solishtirilsin. Smeta shaklida narx tavsif qatoridan PASTDAGI
qatorlarda turadi — band-nisbat (matnli qator + narx) noto'g'ri kam chiqadi.
Etalonda yo'q narxsimon sonlar >= SHAKL_ERKIN_KOP_SON (500) bo'lsa — qabul
(REVIEW izi bilan). Q1 (nusxa) va nol-qiymat himoyalari saqlanadi.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tender_engine import decision as D

# Buyurtmachining «tanilmagan» shabloni — kalendar jadvali (20k: 3470 naqshi)
ETALON = {"Лист2": [
    ["ОБЪЕКТ: КОСОН ТУМАНИ"],
    ["№", "Ishlar va xarajatlar nomi", "Kun"],
    [1, "Qurilish montaj ishlari", 30],
    [2, "Elektr tarmoqlari", 15],
]}


def _smeta(narx_soni, boshl=1_000_000.0):
    """Ishtirokchi smetasi: tavsif qatori, uning OSTIDA narxli qatorlar (matnsiz)."""
    rows = [["ЛОКАЛЬНЫЙ РЕСУРСНЫЙ РАСЧЕТ"], ["№", "Наименование", "Ед.", "Кол-во", "Цена", "Сумма"]]
    for i in range(narx_soni):
        if i % 10 == 0:
            rows.append([i // 10 + 1, f"Ish turi {i // 10 + 1}", "м3", 12.5, None, None])
        rows.append([None, None, None, 1.0, boshl + 111.0 * i, boshl * 2 + 333.0 * i])
    return {"РЕСУРС": rows}


def test_kop_yangi_son_qabul_review_izi_bilan():
    pt = _smeta(300)                                    # 600 ta yangi narxsimon son
    h = D.hukm_shakl_erkin(ETALON, pt)
    assert D.yangi_narxsimon_soni(ETALON, pt) >= D.SHAKL_ERKIN_KOP_SON
    assert h["tashqi"] == 1 and h["ichki"] == D._ev.REVIEW_AMBIGUOUS
    assert "yangi narx qiymati" in h["review_sabab"] and h["shakl_erkin"]
    band, narxli = D.narx_nisbati(ETALON, pt)
    assert narxli < max(D.NARX_MIN_BAND, 0.15 * band) or band < D.NARX_MIN_BAND  # eski yo'l rad qilardi


def test_chegaradan_kam_bolsa_eski_qoida():
    pt = _smeta(200)                                    # 400 < 500
    assert D.yangi_narxsimon_soni(ETALON, pt) < D.SHAKL_ERKIN_KOP_SON
    h = D.hukm_shakl_erkin(ETALON, pt)
    # band-nisbat bu shaklda 0 chiqadi (narx tavsif qatorida emas) — eski yo'l
    # PRICE_EMPTY/PRICE_SPARSE bilan RAD qiladi; #14-son chegaradan pastda aralashmaydi
    assert h["tashqi"] == 2 and h["kodlar"][0] in ("PRICE_SPARSE", "PRICE_EMPTY")


def test_etalondagi_sonlar_sanalmaydi_Q1():
    """Sonlar etalonda allaqachon bor — «yangi» emas (Q1 himoyasi)."""
    et = {"Лист2": [["№", "Nomi", "Narx"]] + [[i, f"Band {i}", 1_000_000.0 + 111.0 * i] for i in range(600)]}
    pt = {"РЕСУРС": [["№", "Nomi", "Narx"]] + [[None, None, 1_000_000.0 + 111.0 * i] for i in range(600)]}
    assert D.yangi_narxsimon_soni(et, pt) == 0
    assert D.hukm_shakl_erkin(et, pt)["tashqi"] == 2


def test_nollar_va_kichik_sonlar_sanalmaydi():
    pt = {"РЕСУРС": [["№", "Nomi", "Narx"]] + [[None, None, 0.0] for _ in range(700)]
                    + [[None, None, 3.5] for _ in range(700)]}
    assert D.yangi_narxsimon_soni(ETALON, pt) == 0
    assert D.hukm_shakl_erkin(ETALON, pt)["tashqi"] == 2


def test_aynan_nusxa_baribir_rad():
    pt = {k: [list(r) for r in v] for k, v in ETALON.items()}
    h = D.hukm_shakl_erkin(ETALON, pt)
    assert h["tashqi"] == 2 and h["kodlar"] == ["IDENTICAL_COPY"]


def test_chegara_barmoq_izida():
    # s2026-09-10b: #14-h qo'shildi (`HUKM_KOP_SON`) — SHAKL_ERKIN_KOP_SON
    # O'ZGARMADI, faqat siyosat versiyasi ko'tarildi.
    # Versiya har qoida o'zgarishida ko'tariladi (s2026-09-15: nol siyosati);
    # bu test chegara QIYMATINI qotiradi, versiya esa kamida shu bo'lsin.
    assert D.SHAKL_ERKIN_KOP_SON == 500 and D.SIYOSAT_VERSIYA >= "s2026-09-10"
