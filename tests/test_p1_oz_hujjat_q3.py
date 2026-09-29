# -*- coding: utf-8 -*-
"""P1 (2026-09-11) — PRICE_SPARSE va shakl-erkin yo'llarida ishtirokchining O'Z
hujjatidagi Q3 (`jiddiy_toldirilganmi`) so'raladi.

Backtest 2026-09-11: 183117 (Форма 4, etalon narx ustuni buyurtmachi sonlari
bilan to'la → `narx_ustuni_toldirilganmi` False; ishtirokchi 6/16 narxlagan),
186026 (sarlavhasiz narx ustuni, 84/128), 181665 (shakl-erkin, 6 varaq, eng
yaxshi varaq 5/22, 4/5 varaq tegilgan). Himoyalar: SHEET_UNFILLED bilan kelsa
tegilmaydi; 1-2 narx baribir rad; aynan nusxa rad.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tender_engine import decision as D

# Buyurtmachi Форма 4 shabloni — narx ustunida O'Z sonlari qolgan (183117 naqshi)
ETALON = {"Sheet1": [
    ["", "", "Форма 4"], ["№ п/п", "Наименование расходов", "Стоимость в текущих ценах"],
    [1, "Затраты на эксплуатацию машин", 167.42], [2, "Затраты на материалы", 21342.57],
    [3, "Затраты на конструкции", 21243.37], [4, "Затраты на оборудование", 512.0],
    [5, "Основная заработная плата", 9803.1], [6, "Затраты на перевозки", 1204.5],
    [7, "Отчисление на страхование", 1176.4], [8, "Итого прямых затрат", 55449.4],
    [9, "Прочие затраты 14,88%", 8250.9], [10, "Итого затрат", 63700.3],
    [11, "Страхование 0,4%", None], [12, "Коэффициент риска", None],
    [13, "Итого без НДС", None], [14, "НДС 12%", None], [15, "Итого с НДС", None],
]}


def _pt(qiymatlar):
    rows = [list(r) for r in ETALON["Sheet1"]]
    for i, v in qiymatlar.items():
        rows[i][2] = v
    return {"Sheet1": rows}


def _eski(kodlar):
    return {"file": "f.xlsx", "role": "jamlanma", "status": 2, "comment_uz": "x",
            "findings": [{"code": k, "sheet": "Sheet1", "detail_uz": k} for k in kodlar]}


TOLDIRILGAN = {2: 21_621_473.0, 3: 214_370_909.0, 4: 0.0, 5: 0.0, 6: 7_627_345.0, 7: 32_223_905.0, 8: 914_925.0,
               9: 276_758_557.0, 10: 41_181_673.0, 11: 317_940_230.0, 12: 0.0, 13: 0.0,
               14: 317_940_230.45, 15: 38_152_827.65, 16: 356_093_058.1}


def test_forma4_toldirilgan_PRICE_SPARSE_QABUL():
    pt = _pt(TOLDIRILGAN)
    assert D.narx_ustuni_toldirilganmi(ETALON, pt)[0] is False     # etalon narx ustuni to'la — eski qutqaruvchi ishlamaydi
    ok, band, narxli = D.jiddiy_toldirilganmi(ETALON, pt)
    assert ok and narxli >= D.NARX_MIN_BAND
    h = D.hukm("jamlanma", ETALON, pt, "f.xlsx", eski_res=_eski(["PRICE_SPARSE"]))
    assert h["tashqi"] == 1 and h["ichki"] == D._ev.REVIEW_AMBIGUOUS
    assert "narxlangan" in h["review_sabab"] and "PRICE_SPARSE" in h["review_sabab"]


def test_forma4_ikki_narx_baribir_RAD():
    pt = _pt({2: 21_621_473.0, 16: 356_093_058.1})               # 2 ta qiymat — Q3 saqlanadi
    assert D.jiddiy_toldirilganmi(ETALON, pt)[0] is False
    h = D.hukm("jamlanma", ETALON, pt, "f.xlsx", eski_res=_eski(["PRICE_SPARSE"]))
    assert h["tashqi"] == 2


def test_sheet_unfilled_bilan_TEGILMAYDI():
    """PRICE_SPARSE + SHEET_UNFILLED: jadval shablon holatida qoldirilgan — P1 aralashmaydi.
    (Ikkinchi varaqda narx sarlavhasi yo'q — `narx_ustuni_toldirilganmi` ham ishlamaydi.)"""
    pt = _pt(TOLDIRILGAN)
    pt["Лист2"] = [["№", "Nomi", "Miqdor"]] + [[i, f"Band {i}", None] for i in range(1, 30)]
    et = dict(ETALON); et["Лист2"] = [list(r) for r in pt["Лист2"]]
    assert D.narx_ustuni_toldirilganmi(et, pt)[0] is False
    assert D.jiddiy_toldirilganmi(et, pt)[0] is True            # o'z hujjatida jiddiy — lekin SHEET_UNFILLED bor
    h = D.hukm("jamlanma", et, pt, "f.xlsx", eski_res=_eski(["PRICE_SPARSE", "SHEET_UNFILLED"]))
    assert h["tashqi"] == 2                                       # tashlab ketilgan jadval — 19239 naqshi


def test_sarlavhasiz_narx_ustuni_resurs_QABUL():
    """186026 naqshi: narx ustunlari sarlavhasiz (faqat 7, 8) — 84/128 narxlangan."""
    et = {"bv_abc4": [["N", "Шифр", "Наименование работ", "Ед.", "на ед.", "по проекту", 7, 8]]
                     + [[i, f"Е{i:04d}", f"РАБОТА {i}", "М3", 1.5, 3.0, None, None] for i in range(1, 129)]}
    pt = {"bv_abc4": [list(r) for r in et["bv_abc4"]]}
    for i in range(1, 85):
        pt["bv_abc4"][i][6] = 25_106.0 + 11.0 * i; pt["bv_abc4"][i][7] = 2_283_883.0 + 17.0 * i
    assert D.narx_ustuni_toldirilganmi(et, pt)[0] is False
    h = D.hukm("resurs", et, pt, "f.xlsx", eski_res=_eski(["PRICE_SPARSE"]))
    # sarlavha qatori ham «band» sanaladi (mavjud xulq) — 84/129
    assert h["tashqi"] == 1 and "84/129" in h["review_sabab"]


def test_shakl_erkin_varaqli_QABUL():
    """181665 naqshi: shablon tanilmagan, 5 varaq, 4 tasi tegilgan, eng yaxshisi 6/22 (27%), global 7%."""
    et = {"Об": [["№", "Наименование смет", "Трудозатраты", "Всего"]] + [[i, f"Смета {i}", None, None] for i in range(1, 6)]}
    pt = {}
    for k in range(1, 6):
        rows = [["N", "Наименование", "Ед.", "Кол-во", "Сумма"]] + [[i, f"Ish {k}-{i}", "м3", 2.0 + i, None] for i in range(1, 60)]
        if k <= 4:
            for i in range(1, 7):
                rows[i][4] = 1_000_000.0 * k + 333.0 * i
        pt[f"Л{k}"] = rows
    pt["С2"] = [["N", "Наименование", "Сумма"]] + [[i, f"Sv {i}", 5_000_000.0 + 111.0 * i if i <= 6 else None] for i in range(1, 23)]
    band, narxli = D.narx_nisbati(et, pt)
    assert narxli / band < D.NARX_NISBATI                       # global nisbat past — eski shakl-erkin rad qilardi
    assert D.jiddiy_toldirilganmi(et, pt)[0] is True             # V3: tegilgan varaqlar ko'pchilik, eng yaxshisi >= 15%
    h = D.hukm_shakl_erkin(et, pt)
    assert h["tashqi"] == 1 and "narxlangan" in h["review_sabab"]


def test_shakl_erkin_siyrak_baribir_RAD():
    """Shakl-erkin: 20 banddan 2 tasi narxlangan, narx sarlavhasi yo'q — P1 ham qutqarmaydi (Q3)."""
    et = {"A": [["№", "Nomi", "X"]] + [[i, f"Band {i}", None] for i in range(1, 21)]}
    pt = {"A": [list(r) for r in et["A"]]}
    pt["A"][1][2] = 1_000_000.0; pt["A"][2][2] = 2_000_000.0
    assert D.jiddiy_toldirilganmi(et, pt)[0] is False
    h = D.hukm_shakl_erkin(et, pt)
    assert h["tashqi"] == 2 and h["kodlar"] == ["PRICE_SPARSE"]


def test_siyosat_versiyasi():
    # P1 s2026-09-11 da kirdi; keyingi qoida o'zgarishlari versiyani ko'taradi
    # (s2026-09-15: nol siyosati) — shuning uchun «kamida» tekshiriladi.
    assert D.SIYOSAT_VERSIYA >= "s2026-09-11"
