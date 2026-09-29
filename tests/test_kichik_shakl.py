# -*- coding: utf-8 -*-
"""#9 (2026-09-07) — kichik (1-4 bandli) shakl uchun mutlaq chegara.

`jiddiy_toldirilganmi`: talab = max(NARX_MIN_BAND=5, ceil(0.15*band)) —
2 bandli jadvalda 5 ga yetib bo'lmaydi. Yumshatish FAQAT uch shart birga:
band < 5  ∧  bandlarning >=50% i narxlangan  ∧  yangi narxsimon katak >= 5.
2026-08-04 teshigi («2 bandli 2 qiymat bilan 100%») yopiq qoladi.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tender_engine import decision as D

ETALON = {"Свод": [
    ["№", "Наименование объекта", "Стоимость", "НДС", "Всего", "Прочие", "Итого"],
    [1, "МФЙ Навбахор — ремонт", None, None, None, None, None],
    [2, "МФЙ Гулистон — ремонт", None, None, None, None, None],
    ["", "ИТОГО", None, None, None, None, None],
]}


def _n(v):
    return {k: [list(q) for q in rows] for k, rows in v.items()}


def test_ikki_band_ikki_qiymat_uch_katak_RAD():
    """Teshik yopiq: 2 band, 2 ta qator narxlangan, lekin jami 3 ta katak."""
    pt = _n(ETALON)
    pt["Свод"][1][2] = 471_841_805.0
    pt["Свод"][2][2] = 65_851_109.0
    pt["Свод"][2][3] = 7_902_133.0
    ok, band, narxli = D.jiddiy_toldirilganmi(ETALON, pt)
    assert band == 2 and narxli == 2
    assert D.yangi_narxsimon_soni(ETALON, pt) == 3 < D.NARX_MIN_BAND
    assert ok is False


def test_ikki_band_toliq_qatorlar_OTADI():
    """id 49512 naqshi: 2 band, har birida 5-6 ta xarajat ustuni to'ldirilgan."""
    pt = _n(ETALON)
    pt["Свод"][1][2:7] = [471_841_805.0, 56_620_016.0, 528_461_821.0, 12_000_000.0, 540_461_821.0]
    pt["Свод"][2][2:7] = [65_851_109.0, 7_902_133.0, 73_753_242.0, 1_500_000.0, 75_253_242.0]
    ok, band, narxli = D.jiddiy_toldirilganmi(ETALON, pt)
    assert band == 2 and narxli == 2
    assert D.yangi_narxsimon_soni(ETALON, pt) >= D.NARX_MIN_BAND
    assert ok is True


def test_yarmidan_kami_narxlangan_RAD():
    """3 band, faqat 1 tasi narxlangan (33% < 50%) — ko'p katak bo'lsa ham rad."""
    et = _n(ETALON)
    et["Свод"].insert(3, [3, "МФЙ Чилонзор — ремонт", None, None, None, None, None])
    pt = _n(et)
    pt["Свод"][1][2:7] = [1e9, 1.2e8, 1.12e9, 5e7, 1.17e9]
    ok, band, narxli = D.jiddiy_toldirilganmi(et, pt)
    assert band == 3 and narxli == 1
    assert ok is False


def test_katta_shakl_ozgarmaydi():
    """band >= 5 — eski qoida aynan: talab = max(5, ceil(0.15*band))."""
    et = {"Л": [["№", "Наименование", "Цена"]] + [[i, f"Band {i}", None] for i in range(1, 21)]}
    pt = _n(et)
    for i in (1, 2, 3, 4):
        pt["Л"][i][2] = 1_000_000.0 * i
    ok, band, narxli = D.jiddiy_toldirilganmi(et, pt)
    # `narx_nisbati` matnli sarlavha qatorini ham band deb sanaydi (mavjud
    # xulq) — 20 band + 1 sarlavha = 21; talab = max(5, ceil(0.15*21)=4) = 5.
    assert band == 21 and narxli == 4
    assert ok is False                       # 4 < 5
    pt["Л"][5][2] = 5_000_000.0
    assert D.jiddiy_toldirilganmi(et, pt)[0] is True
