# -*- coding: utf-8 -*-
"""#10 (2026-09-08) — jamlanma/narxlar da TEKSHIRILADIGAN varaq tanlovi.

Ilgari `validate_jamlanma`/`validate_narxlar` doim BIRINCHI varaqni olardi:
ishtirokchi oldiga muqova qo'ysa, haqiqiy jadval ko'rilmay HEADER_NOT_FOUND
bilan rad etilardi. Endi `_tekshiriladigan_varaq`:
  1) etalon varag'iga `_varaq_moslash` bilan juftlangan varaq;
  2) bo'lmasa sarlavha kalitlari topilgan birinchi varaq;
  3) bo'lmasa birinchi varaq (eski xulq).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tender_engine import roles, validate
from tender_engine.rules import common

JAM_ET = {"Жамланма": [
    ["Т/Р", "Харажатлар номи", "Жами қиймати"],
    [1, "Қурилиш-монтаж ишлари", None],
    [2, "Ускуналар ва жиҳозлар", None],
    [3, "Бошқа харажатлар", None],
    ["", "Жами", None],
]}

NARX_ET = {"Нархлар": [
    ["Т/Р", "Компонент номи", "Ўлчов бирлиги", "Бирлик учун нарх"],
    [1, "Цемент М400", "тонна", None],
    [2, "Арматура А500", "тонна", None],
    [3, "Қум", "м3", None],
    [4, "Шағал", "м3", None],
    [5, "Ғишт", "дона", None],
]}

MUQOVA = [["ТИЖОРАТ ТАКЛИФИ"], ["Ташкилот: «Қурувчи» МЧЖ"], ["Сана: 01.09.2026"], []]


def _jam_toldirilgan():
    rows = [list(q) for q in JAM_ET["Жамланма"]]
    rows[1][2] = 12_500_000.0
    rows[2][2] = 8_300_000.0
    rows[3][2] = 1_200_000.0
    return rows


def _narx_toldirilgan():
    rows = [list(q) for q in NARX_ET["Нархлар"]]
    for i, v in enumerate((1_450_000.0, 9_800_000.0, 120_000.0, 150_000.0, 1_100.0), start=1):
        rows[i][3] = v
    return rows


def test_bitta_varaq_ozgarmaydi():
    pt = {"Лист1": _jam_toldirilgan()}
    assert common._tekshiriladigan_varaq(JAM_ET, pt, roles.JAMLANMA_HEADER_ANCHORS) == "Лист1"


def test_etalon_varagiga_juftlangan_varaq():
    """Muqova birinchi, jamlanma ikkinchi — etalon nomi bilan juftlanadi."""
    pt = {"Муқова": MUQOVA, "Жамланма": _jam_toldirilgan()}
    assert common._tekshiriladigan_varaq(JAM_ET, pt, roles.JAMLANMA_HEADER_ANCHORS) == "Жамланма"


def test_etalonsiz_sarlavha_skan():
    """Etalon yo'q (juftlash imkonsiz) — sarlavha kalitlari bor varaq olinadi."""
    pt = {"ЛОКАЛЬНЫЙ": [["ЛОКАЛЬНЫЙ СМЕТНЫЙ РАСЧЕТ"], ["№", "Наименование", "Кол-во"],
                        [1, "Земляные работы", 12.5]],
          "РЕСУРС": _jam_toldirilgan()}
    assert common._tekshiriladigan_varaq({}, pt, roles.JAMLANMA_HEADER_ANCHORS) == "РЕСУРС"


def test_juftlangan_varaq_bosh_bolsa_ham_u_tanlanadi_142790():
    """#10-b RAD ETILGAN variantga qarshi qotirilgan (2026-09-08, 20k sinovi):
    etalon nomli «Лист1» bo'sh izoh, «Sheet0» esa sarlavhasiz katta ro'yxat
    (142790 da 23 030 qatorli tovar katalogi) — eski kod 1-varaqni pozitsion
    zaxira bilan NOO'RIN qabul qilardi. Juftlangan varaq tanlanadi → status 2;
    haqiqiy narxlar bo'lsa keyingi review qatlami (varaqqa bog'liq emas) ochadi."""
    katalog = [["Код", "Признак", "Наименование товара", "Состояние", "Дата"]] + \
              [[f"20.59.{i:05d}", "Y", f"Товар {i}", "A", "19.06.2020"] for i in range(1, 40)]
    pt = {"Sheet0": katalog, "Нархлар": [["Изоҳ"], ["Бу варақда жадвал йўқ"]]}
    assert common._find_row_with_any(pt["Sheet0"], roles.NARXLAR_NAME_KEYWORDS) is None   # fikstura sharti
    assert common._tekshiriladigan_varaq(NARX_ET, pt, roles.NARXLAR_NAME_KEYWORDS) == "Нархлар"
    r = validate.validate_one("narxlar", NARX_ET, pt, "f.xlsx")
    assert r["status"] == 2 and r["findings"][0]["code"] == "HEADER_NOT_FOUND"


def test_juftlik_yoq_sarlavhali_varaq_tanlanadi():
    """Etalon nomi bilan juftlik yo'q — sarlavha topilgan varaq (muqova emas)."""
    pt = {"Титул": MUQOVA, "Sheet0": _narx_toldirilgan()}
    assert common._tekshiriladigan_varaq(NARX_ET, pt, roles.NARXLAR_NAME_KEYWORDS) == "Sheet0"
    assert validate.validate_one("narxlar", NARX_ET, pt, "f.xlsx")["status"] == 1


def test_juftlangan_varaq_sarlavhali_bolsa_u_ustun():
    """Ikkala varaqda ham sarlavha bor — etalonga juftlangani (nomi mos) ustun."""
    boshqa = _narx_toldirilgan()
    pt = {"Эски": boshqa, "Нархлар": _narx_toldirilgan()}
    assert common._tekshiriladigan_varaq(NARX_ET, pt, roles.NARXLAR_NAME_KEYWORDS) == "Нархлар"


def test_hech_narsa_topilmasa_birinchi_varaq():
    """Eski xulq saqlanadi: na juftlik, na sarlavha — birinchi varaq."""
    pt = {"A": MUQOVA, "B": [["x", "y"], [1, 2]]}
    assert common._tekshiriladigan_varaq({}, pt, roles.JAMLANMA_HEADER_ANCHORS) == "A"


def test_jamlanma_muqova_bilan_QABUL():
    """Ilgari HEADER_NOT_FOUND (muqova varag'i tekshirilardi) — endi status 1."""
    pt = {"Муқова": MUQOVA, "Жамланма": _jam_toldirilgan()}
    faqat = validate.validate_one("jamlanma", JAM_ET, {"Жамланма": _jam_toldirilgan()}, "f.xlsx")
    assert faqat["status"] == 1, faqat["findings"]          # nazorat: bitta varaq qabul
    r = validate.validate_one("jamlanma", JAM_ET, pt, "f.xlsx")
    assert r["status"] == 1, r["findings"]
    assert all(f.get("sheet") in (None, "Жамланма") for f in r["findings"])


def test_narxlar_muqova_bilan_QABUL():
    pt = {"Титул": MUQOVA, "Нархлар": _narx_toldirilgan()}
    faqat = validate.validate_one("narxlar", NARX_ET, {"Нархлар": _narx_toldirilgan()}, "f.xlsx")
    assert faqat["status"] == 1, faqat["findings"]
    r = validate.validate_one("narxlar", NARX_ET, pt, "f.xlsx")
    assert r["status"] == 1, r["findings"]


def test_bosh_jadval_muqova_bilan_baribir_RAD():
    """Tanlov faqat QAYSI varaq tekshirilishini o'zgartiradi — bo'sh jadval rad."""
    pt = {"Муқова": MUQOVA, "Жамланма": [list(q) for q in JAM_ET["Жамланма"]]}
    r = validate.validate_one("jamlanma", JAM_ET, pt, "f.xlsx")
    assert r["status"] == 2
