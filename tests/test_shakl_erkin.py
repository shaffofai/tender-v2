# -*- coding: utf-8 -*-
"""Shakl-erkin hukm + reader mustahkamligi (buyurtmachi ko'rsatmasi 2026-08-19).

Qoida: buyurtmachi shabloni bizga notanish bo'lsa ham, fayl juda keng
e'lon qilingan bo'lsa ham, `<definedNames>` buzuq bo'lsa ham — hujjat
VERDIKTSIZ QOLMASIN. Rad etish uchun yagona asos: narx yo'q yoki siyrak,
yoki hujjat etalonning aynan nusxasi.
"""
import os
import sys
import zipfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tender_engine import decision as d
from tender_engine import evidence as ev
from tender_engine import reader


# ═══════════════════════════════════════════════════════════════════════════
# Shakl-erkin hukm
# ═══════════════════════════════════════════════════════════════════════════

_ET = {"Лист1": [["№", "Ish nomi", "Narx"],
                 [1, "beton ish", None],
                 [2, "armatura", None],
                 [3, "g'isht terish", None]]}


def _pt(narxlar):
    """Etalon shaklidan BOSHQA ko'rinishdagi ishtirokchi hujjati."""
    qatorlar = [["T/r", "Ishlar", "Qiymat"]]
    for i, n in enumerate(narxlar, 1):
        qatorlar.append([i, f"ish turi {i}", n])
    return {"Оферта": qatorlar}


def test_notanish_shablon_narx_bor_qabul():
    """Shakl tanilmasa ham, narx kiritilgan bo'lsa QABUL."""
    h = d.hukm_shakl_erkin(_ET, _pt([150000, 240000.5, 99000, 12000, 78000, 5000]))
    assert h["ichki"] == ev.REVIEW_AMBIGUOUS
    assert h["tashqi"] == 1
    assert h["narxli_band"] == 6
    assert h["comment_uz"] == d.IZOH_QABUL


def test_notanish_shablon_narx_yoq_rad():
    """Narx umuman kiritilmagan → rad, izohi aniq."""
    h = d.hukm_shakl_erkin(_ET, _pt([None] * 6))
    assert h["ichki"] == ev.REJECT_NO_PRICE
    assert h["tashqi"] == 2
    assert h["kodlar"] == ["PRICE_EMPTY"]


def test_notanish_shablon_siyrak_rad():
    """100 banddan 6 tasi (6%) narxlangan → 15% chegaradan past, RAD."""
    h = d.hukm_shakl_erkin(_ET, _pt([150000] * 6 + [None] * 94))
    assert h["tashqi"] == 2
    assert h["kodlar"] == ["PRICE_SPARSE"]
    assert "6" in h["comment_uz"]


def test_kam_son_izohi_foizni_aytmaydi():
    """Yorliqsiz ustunda 4 ta qiymat — buyurtmachi chegarasi (≥5) ishlaydi.

    Izohda foiz aytilsa chalg'itadi («44% kiritilgan, lekin rad») — shuning
    uchun bu holatda SONI aytiladi.
    """
    pt = {"Оферта": [["T/r", "Ishlar", "Ustun"]]     # narx nomi YO'Q ustun
                    + [[i, f"ish turi {i}", (150000 + i) if i <= 4 else None]
                       for i in range(1, 10)]}
    h = d.hukm_shakl_erkin(_ET, pt)
    assert h["tashqi"] == 2
    assert h["kodlar"] == ["PRICE_SPARSE"]
    assert "4 ta banda" in h["comment_uz"]
    assert "%" not in h["comment_uz"]


def test_narx_nomli_ustunda_uchta_qiymat_qabul():
    """AUDIT ASOSIDA (2026-08-24): buyurtmachi shabloni umumiy resurs
    katalogi bo'lsa (Цемент, Қум, «va h.k.»), ishtirokchi FAQAT o'ziga
    tegishli 3 bandni narxlaydi. id=14506/14602/14754 — aynan shunday,
    taklif qiymati 472-565 mln so'm, lekin rad etilgan edi.

    Ustun NARX deb ATALGAN bo'lsa dalil kuchli: 3 ta qiymat va >=15%
    nisbat yetarli. Yorliqsiz ustunda esa chegara baribir 5 ta
    (yuqoridagi test)."""
    et = {"Лист1": [["№", "Наименование компонента", "Ед.изм", "Объем",
                     "на единицу измерения", "сумма"]]
                   + [[i, f"resurs {i}", "шт", 10, None, None]
                      for i in range(1, 13)]}
    pt = {"Лист1": [["№", "Наименование компонента", "Ед.изм", "Объем",
                     "на единицу измерения", "сумма"]]
                   + [[1, "SOLAR PANEL", "шт", 4047, 28380.34, 114855235.98],
                      [2, "INVERTOR", "компл", 20, 12500000, 250000000],
                      [3, "O'RNATISH", "компл", 50, 250000, 12500000]]}
    ha, toldi, band = d.narx_ustuni_toldirilganmi(et, pt)
    assert ha, f"3 ta narxlangan band qabul bo'lishi kerak ({toldi}/{band})"
    h = d.hukm_shakl_erkin(et, pt)
    assert h["tashqi"] == 1


def test_aynan_nusxa_rad():
    """Q1: ishtirokchi buyurtmachi faylini o'zgartirmasdan qaytargan."""
    h = d.hukm_shakl_erkin(_ET, {"Лист1": [q[:] for q in _ET["Лист1"]]})
    assert h["tashqi"] == 2
    assert h["kodlar"] == ["IDENTICAL_COPY"]
    assert h["comment_uz"] == d.IZOH_NUSXA


def test_xesh_bir_xil_bolsa_ham_nusxa():
    """Fayl xeshi etalon xeshi bilan bir xil — mazmunga qaramay nusxa."""
    h = d.hukm_shakl_erkin(_ET, _pt([150000] * 9), bir_xil_fayl=True)
    assert h["tashqi"] == 2
    assert h["kodlar"] == ["IDENTICAL_COPY"]


def test_etalon_yoq_bolsa_ham_hukm_chiqadi():
    """Etalon umuman bo'lmasa ham verdikt beriladi (verdiktsiz qolmasin)."""
    h = d.hukm_shakl_erkin(None, _pt([150000, 240000, 99000, 12000, 78000]))
    assert h["tashqi"] == 1
    h2 = d.hukm_shakl_erkin({}, _pt([None] * 5))
    assert h2["tashqi"] == 2


def test_kun_ustuni_toldirilgan_qabul():
    """HAQIQIY holat (tender 265119/263954, 2026-08-20): buyurtmachi excel3
    slotiga MUDDAT jadvalini yuklagan, yagona bo'sh ustun «Kun».
    Ishtirokchi 15/60/15 kun deb to'ldirgan — bu TO'LIQ hujjat, QABUL."""
    et = {"Лист2": [["G'uzor tumani MFY", None, None],
                    ["№", "Ishlar va xarajatlar nomi", "Kun"],
                    [1, "ЭЛЕКТРОСИЛОВОЕ ОБОРУДОВАНИЕ", None],
                    [2, "ДОРОГИ", None],
                    [3, "СКВАЖИНЫ 3-ШТ", None]]}
    pt = {"Лист2": [["G'uzor tumani MFY", None, None],
                    ["№", "Ishlar va xarajatlar nomi", "Kun"],
                    [1, "ЭЛЕКТРОСИЛОВОЕ ОБОРУДОВАНИЕ", 15],
                    [2, "ДОРОГИ", 60],
                    [3, "СКВАЖИНЫ 3-ШТ", 15]]}
    ha, toldi, slot = d.bosh_ustun_toldirilganmi(et, pt)
    assert ha, f"to'ldirilgan {toldi}/{slot} — qabul bo'lishi kerak edi"
    h = d.hukm_shakl_erkin(et, pt)
    assert h["tashqi"] == 1
    assert h["comment_uz"] == d.IZOH_QABUL


def test_kun_ustuni_bosh_qolsa_rad():
    """Yagona bo'sh ustun to'ldirilmagan bo'lsa — baribir rad."""
    et = {"Л": [["№", "Ishlar", "Kun"]] + [[i, f"ish {i}", None] for i in range(1, 6)]}
    pt = {"Л": [["№", "Ishlar", "Kun"]] + [[i, f"ish {i}", None] for i in range(1, 6)]}
    ha, _, _ = d.bosh_ustun_toldirilganmi(et, pt)
    assert not ha


def test_raqamlash_toldirish_deb_sanalmaydi():
    """Ishtirokchi faqat qatorlarni raqamlab chiqsa (1,2,3…) — to'ldirish emas."""
    et = {"Л": [["№", "Ishlar", "Kun"]] + [[None, f"ish {i}", None]
                                           for i in range(1, 9)]}
    pt = {"Л": [["№", "Ishlar", "Kun"]] + [[i, f"ish {i}", None]
                                           for i in range(1, 9)]}
    ha, toldi, _ = d.bosh_ustun_toldirilganmi(et, pt)
    assert not ha, f"raqamlash to'ldirish deb sanaldi ({toldi} katak)"


def test_kun_siyrak_toldirilgan_rad():
    """100 bandli shaklda atigi 3 tasi to'ldirilgan (3%) — RAD."""
    et = {"Л": [["№", "Ishlar", "Kun"]] + [[i, f"ish {i}", None] for i in range(1, 101)]}
    pt_qatorlar = [["№", "Ishlar", "Kun"]] + [
        [i, f"ish {i}", 15 if i <= 3 else None] for i in range(1, 101)]
    ha, toldi, slot = d.bosh_ustun_toldirilganmi(et, {"Л": pt_qatorlar})
    assert not ha, f"{toldi}/{slot} — siyrak, rad bo'lishi kerak"


def test_kod_ustuni_narx_deb_sanalmaydi():
    """HAQIQIY holat (id=16878/16283, 2026-08-20 auditi): smetada
    «Шифр номера нормативов» ustunidagi kodlar (45059, 36052…) 1000 dan
    katta butun son — narx deb sanalgan, «Стоимость» esa bo'sh edi."""
    et = {"Свод": [["№ п/п", "Шифр номера нормативов", "Тип работы",
                    "Количество", "Стоимость"]]}
    pt = {"LRV": [["№ п/п", "Шифр номера нормативов", "Тип работы",
                   "Количество", "Стоимость"]]
                  + [[i, 45059 + i, f"ish {i}", 0.14, None]
                     for i in range(1, 15)]}
    band, narxli = d.narx_nisbati(et, pt)
    assert narxli == 0, f"kod ustuni narx deb sanaldi ({narxli}/{band})"
    h = d.hukm_shakl_erkin(et, pt)
    assert h["tashqi"] == 2

    # Haqiqiy narx qo'shilsa — QABUL
    pt2 = {"LRV": [["№ п/п", "Шифр номера нормативов", "Тип работы",
                    "Количество", "Стоимость"]]
                   + [[i, 45059 + i, f"ish {i}", 0.14, 150000 + i]
                      for i in range(1, 15)]}
    band2, narxli2 = d.narx_nisbati(et, pt2)
    assert narxli2 == 14, f"haqiqiy narx sanalmadi ({narxli2}/{band2})"


def test_kasrli_ustun_kod_deb_chetlanmaydi():
    """Himoya: sarlavha birlashtirilgan katakdan xato o'qilsa ham, KASRLI
    qiymatli ustun kod deb chetlanmasin (narx ustuni yo'qolib qolmasin)."""
    qatorlar = [["Шифр номера нормативов", "Шифр номера нормативов"]] + [
        [45059 + i, 150000.75 + i] for i in range(1, 15)]
    kod = d._kod_ustunlari(qatorlar)
    assert 0 in kod, "butun sonli kod ustuni chetlanishi kerak"
    assert 1 not in kod, "kasrli narx ustuni chetlanmasligi kerak"


def test_etalon_narx_ustuni_toldirilgan_qabul():
    """2026-08-24 auditi (31 noo'rin rad): etalonning «Стоимость» ustuni
    to'ldirilgan bo'lsa — qatorlar surilgan yoki varaq qayta nomlangan
    bo'lsa ham — QABUL."""
    et = {"ЛОК": [["№", "Шифр номера нормативов", "Наименование",
                   "Количество", "Стоимость"]]
                 + [[i, 45059 + i, f"ish {i}", 2.5, None] for i in range(1, 12)]}
    # ishtirokchi varaqni qayta nomlagan VA qatorlarni surgan
    pt = {"ОФЕРТА": [["sarlavha", None, None, None, None],
                     ["№", "Шифр номера нормативов", "Наименование",
                      "Количество", "Стоимость"]]
                    + [[i, 45059 + i, f"ish {i}", 2.5, 115151 + i]
                       for i in range(1, 12)]}
    ha, toldi, slot = d.narx_ustuni_toldirilganmi(et, pt)
    assert ha, f"narx ustuni to'ldirilgani ko'rilmadi ({toldi}/{slot})"
    h = d.hukm_shakl_erkin(et, pt)
    assert h["tashqi"] == 1


def test_faqat_jami_va_soliq_qatori_rad():
    """2026-08-24 auditi: ИТОГО + НДС 12% + ИТОГО с НДС — bu UCHTA son
    emas, BITTA mustaqil qiymat. Bunday hujjat RAD bo'lishi kerak."""
    et = {"Свод": [["№", "Наименование расходов", "Стоимость в текущих ценах"]]
                  + [[i, f"harajat {i}", None] for i in range(1, 8)]
                  + [[None, "ИТОГО", None], [None, "НДС 12%", None],
                     [None, "ИТОГО с НДС", None]]}
    pt = {"Свод": [["№", "Наименование расходов", "Стоимость в текущих ценах"]]
                  + [[i, f"harajat {i}", None] for i in range(1, 8)]
                  + [[None, "ИТОГО", 2463621114.73],
                     [None, "НДС 12%", 295634533.77],
                     [None, "ИТОГО с НДС", 2759255648.50]]}
    ha, toldi, slot = d.narx_ustuni_toldirilganmi(et, pt)
    assert not ha, f"faqat jami qatorlari qabul qilindi ({toldi}/{slot})"
    h = d.hukm_shakl_erkin(et, pt)
    assert h["tashqi"] == 2


def test_price_sparse_bekor_qilinadi_narx_bolsa():
    """2026-08-24 auditi: Q3 (PRICE_SPARSE) noto'g'ri MAXRAJdan kelib
    chiqqan bo'lsa bekor qilinadi — narx ustuni to'g'ri o'lchovda
    to'ldirilgan bo'lsa hujjat QABUL."""
    et = {"Лист1": [["№", "Компонент", "Ед.изм", "Объем",
                     "на единицу измерения", "сумма"]]
                   + [[i, f"resurs {i}", "шт", 10, None, None]
                      for i in range(1, 13)]}
    pt = {"Лист1": [["№", "Компонент", "Ед.изм", "Объем",
                     "на единицу измерения", "сумма"]]
                   + [[i, f"resurs {i}", "шт", 10, 28380.34, 283803.4]
                      for i in range(1, 6)]}
    eski = {"status": 2, "role": "narxlar", "comment_uz": "siyrak",
            "findings": [{"code": "PRICE_SPARSE"}]}
    h = d.hukm("narxlar", et, pt, "x", eski_res=eski)
    assert h["tashqi"] == 1
    assert "noto'g'ri maxraj" in (h["review_sabab"] or "")


def test_price_only_total_bekor_qilinmaydi():
    """Faqat jami yozilgan hujjat bu yo'ldan O'TMAYDI (Q3 saqlanadi)."""
    et = {"Свод": [["№", "Наименование", "Стоимость в текущих ценах"]]
                  + [[i, f"harajat {i}", None] for i in range(1, 9)]}
    pt = {"Свод": [["№", "Наименование", "Стоимость в текущих ценах"]]
                  + [[i, f"harajat {i}", None] for i in range(1, 9)]
                  + [[None, "ИТОГО", 2463621114.73]]}
    eski = {"status": 2, "role": "jamlanma", "comment_uz": "faqat jami",
            "findings": [{"code": "PRICE_SPARSE"}, {"code": "PRICE_ONLY_TOTAL"}]}
    h = d.hukm("jamlanma", et, pt, "x", eski_res=eski)
    assert h["tashqi"] == 2


def test_hajm_sonlari_narx_deb_sanalmaydi():
    """Smetada narx ustuni bo'sh, faqat hajm bor → RAD."""
    pt = {"Смета": [["№", "ish", "hajm", "narx"]]
                   + [[i, f"ish {i}", 0.105 * i, None] for i in range(1, 12)]}
    h = d.hukm_shakl_erkin(_ET, pt)
    assert h["tashqi"] == 2
    assert h["narxli_band"] == 0


# ═══════════════════════════════════════════════════════════════════════════
# Reader mustahkamligi
# ═══════════════════════════════════════════════════════════════════════════

def _xlsx_yasa(yol, sheet_xml, workbook_qosh=""):
    """Minimal, lekin haqiqiy .xlsx yasaydi."""
    wb = ('<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/'
          'spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/'
          'officeDocument/2006/relationships"><sheets><sheet name="Лист1" '
          f'sheetId="1" r:id="rId1"/></sheets>{workbook_qosh}</workbook>')
    wr = ('<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats'
          '.org/package/2006/relationships"><Relationship Id="rId1" Type="http://'
          'schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"'
          ' Target="worksheets/sheet1.xml"/></Relationships>')
    rl = ('<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats'
          '.org/package/2006/relationships"><Relationship Id="rId1" Type="http://'
          'schemas.openxmlformats.org/officeDocument/2006/relationships/'
          'officeDocument" Target="xl/workbook.xml"/></Relationships>')
    ct = ('<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/'
          'package/2006/content-types"><Default Extension="xml" ContentType='
          '"application/xml"/><Default Extension="rels" ContentType="application/'
          'vnd.openxmlformats-package.relationships+xml"/><Override PartName='
          '"/xl/workbook.xml" ContentType="application/vnd.openxmlformats-'
          'officedocument.spreadsheetml.sheet.main+xml"/><Override PartName='
          '"/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-'
          'officedocument.spreadsheetml.worksheet+xml"/></Types>')
    with zipfile.ZipFile(yol, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("_rels/.rels", rl)
        z.writestr("xl/workbook.xml", wb)
        z.writestr("xl/_rels/workbook.xml.rels", wr)
        z.writestr("xl/worksheets/sheet1.xml", sheet_xml)
    return yol


def _keng_varaq_xml():
    """A1..C1 da qiymat bor, XFD1 da esa faqat USLUB (qiymatsiz katak).

    Amalda uchraydigan holat: fayl 16 384 ustun deb e'lon qilinadi, lekin
    ma'lumot faqat dastlabki ustunlarda.
    """
    return ('<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats'
            '.org/spreadsheetml/2006/main"><dimension ref="A1:XFD2"/><sheetData>'
            '<row r="1"><c r="A1" t="inlineStr"><is><t>Ish</t></is></c>'
            '<c r="B1" t="inlineStr"><is><t>Narx</t></is></c>'
            '<c r="XFD1" s="1"/></row>'
            '<row r="2"><c r="A2" t="inlineStr"><is><t>beton</t></is></c>'
            '<c r="B2"><v>150000</v></c><c r="XFD2" s="1"/></row>'
            '</sheetData></worksheet>')


def test_keng_elon_qilingan_varaq_rad_etilmaydi(tmp_path):
    """16 384 ustun deb e'lon qilingan, lekin ma'lumoti tor fayl O'QILSIN."""
    yol = _xlsx_yasa(str(tmp_path / "keng.xlsx"), _keng_varaq_xml())
    haqiqiy, elon, _haqiqiy_qator, _elon_qator = reader._xlsx_preflight(yol)
    assert haqiqiy <= 3, f"haqiqiy kenglik noto'g'ri: {haqiqiy}"
    assert elon > reader.MAX_SHEET_COLS, "e'lon qilingan kenglik katta bo'lishi kerak"

    varaqlar = reader.read_file(yol)          # rad ETILMASIN
    assert len(varaqlar) == 1
    qatorlar = next(iter(varaqlar.values()))
    assert qatorlar[0][0] == "Ish"
    assert qatorlar[1][1] == 150000


def test_buzuq_nomlar_tuzatiladi(tmp_path):
    """Buzuq `<definedNames>` butun hujjatni o'qilmas qilib qo'ymasin."""
    oddiy = ('<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats'
             '.org/spreadsheetml/2006/main"><dimension ref="A1:B2"/><sheetData>'
             '<row r="1"><c r="A1" t="inlineStr"><is><t>Ish</t></is></c>'
             '<c r="B1"><v>5000</v></c></row></sheetData></worksheet>')
    # openpyxl «could not assign names» beradigan yaroqsiz havola
    nomlar = ('<definedNames><definedName name="_xlnm.Print_Area" '
              'localSheetId="7">#REF!</definedName></definedNames>')
    yol = _xlsx_yasa(str(tmp_path / "nom.xlsx"), oddiy, workbook_qosh=nomlar)

    varaqlar = reader.read_file(yol)          # tuzatilib o'qilsin
    assert varaqlar
    assert next(iter(varaqlar.values()))[0][0] == "Ish"


def test_merge_bomba_hali_ham_rad_etiladi(tmp_path):
    """Yumshatishlar himoyani buzmasin: merge-bomba baribir to'xtatilsin."""
    sh = ('<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats'
          '.org/spreadsheetml/2006/main"><dimension ref="A1:D10"/><sheetData>'
          '<row r="1"><c r="A1" t="inlineStr"><is><t>x</t></is></c></row>'
          '</sheetData><mergeCells count="1">'
          '<mergeCell ref="A1:XFD1048576"/></mergeCells></worksheet>')
    yol = _xlsx_yasa(str(tmp_path / "bomba.xlsx"), sh)
    with pytest.raises(reader.ExcelTooLargeError):
        reader.read_file(yol)
