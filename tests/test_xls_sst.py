# -*- coding: utf-8 -*-
"""SST-chidamli `.xls` o'qish (ROADMAP M10 / 2026-09-02 jonli nuqson).

Platformadagi ba'zi `.xls` fayllar Excel bilan emas, boshqa generator bilan
yasalgan: SST (umumiy matn lug'ati) yozuvidagi «nechta satr bor» hisoblagichi
haqiqiy songa mos kelmaydi. Excel ularni MULOYIM ochadi, xlrd esa
`assert _unused_i == nstrings - 1` bilan yiqiladi — xato matnisiz.

Oqibati og'ir edi: to'ldirilgan HALOL hujjat «Faylni ochib bo'lmadi» deb
rad etilardi; buyurtmachi shabloni bo'lsa butun tender verdiktsiz qolardi
(2026-09-02: tender 279238 va 279700 — 9 ta fayl shu sababdan turgan edi).

Bu testlar ikki narsani qotiradi:
  1. buzuq SST li fayl O'QILADI (yiqilmaydi);
  2. o'qilgan mazmun SOG'LOM nusxadagidek — ya'ni yumshatish ma'lumotni
     buzmaydi (aks holda noto'g'ri verdikt chiqishi mumkin edi).
"""
import os
import struct
import sys
import warnings

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

xlrd = pytest.importorskip("xlrd")
xlwt = pytest.importorskip("xlwt", reason="fixture yasash uchun xlwt kerak")

from tender_engine import reader


def _xls_yasa(yol, qatorlar):
    wb = xlwt.Workbook()
    sh = wb.add_sheet("ЛРВ")
    for i, qator in enumerate(qatorlar):
        for j, katak in enumerate(qator):
            if katak is not None:
                sh.write(i, j, katak)
    wb.save(yol)
    return yol


def _sst_hisoblagichini_buz(yol, qoshimcha=4000):
    """SST yozuvidagi «unique strings» sonini oshirib yozadi.

    Aynan platformadagi nuqsonni taqlid qiladi: e'lon qilingan son
    haqiqiy satrlar sonidan katta → xlrd assert bilan yiqiladi.
    """
    with open(yol, "rb") as f:
        xom = bytearray(f.read())
    # SST yozuvi: 0x00FC + uzunlik(2) + jami(4) + noyob(4)
    idx = xom.find(b"\xfc\x00")
    assert idx != -1, "fikstura ichida SST yozuvi topilmadi"
    noyob_ofset = idx + 2 + 2 + 4
    noyob = struct.unpack("<i", xom[noyob_ofset:noyob_ofset + 4])[0]
    xom[noyob_ofset:noyob_ofset + 4] = struct.pack("<i", noyob + qoshimcha)
    with open(yol, "wb") as f:
        f.write(bytes(xom))
    return noyob, noyob + qoshimcha


@pytest.fixture
def juftlik(tmp_path):
    """(sog'lom fayl, buzuq SST li AYNAN o'sha fayl)."""
    qatorlar = [["№", "Ish nomi", "Birlik", "Narx"]]
    for i in range(1, 40):
        qatorlar.append([i, f"ish turi {i}", "m3", 150000 + i * 37])
    sogolom = _xls_yasa(str(tmp_path / "sog.xls"), qatorlar)
    buzuq = str(tmp_path / "buzuq.xls")
    with open(sogolom, "rb") as a, open(buzuq, "wb") as b:
        b.write(a.read())
    _sst_hisoblagichini_buz(buzuq)
    return sogolom, buzuq


def test_buzuq_sst_xlrd_ni_yiqitadi(juftlik):
    """Fikstura haqiqatan muammoni takrorlaydimi (test o'zini tekshiradi)."""
    _sog, buzuq = juftlik
    with pytest.raises(AssertionError):
        xlrd.open_workbook(buzuq)


def test_buzuq_sst_fayl_oqiladi(juftlik):
    """Bizning reader yiqilmasdan o'qiydi va ogohlantiradi."""
    _sog, buzuq = juftlik
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        varaqlar = reader.read_file(buzuq)
    assert varaqlar, "fayl o'qilmadi"
    assert any("SST" in str(x.message) for x in w), "ogohlantirish yo'q"


def test_mazmun_sogolom_nusxa_bilan_AYNAN(juftlik):
    """ENG MUHIM: yumshatish ma'lumotni buzmasin — katakma-katak tenglik."""
    sogolom, buzuq = juftlik
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        a = reader.read_file(sogolom)
        b = reader.read_file(buzuq)
    assert list(a) == list(b)
    for nom in a:
        assert len(a[nom]) == len(b[nom]), f"«{nom}» qator soni farq qildi"
        for i, (qa, qb) in enumerate(zip(a[nom], b[nom])):
            assert qa == qb, f"«{nom}» {i}-qatorda farq: {qa} != {qb}"


def test_sogolom_fayl_yumshoq_yolga_tushmaydi(juftlik):
    """Oddiy fayllar o'zgarishsiz, tez yo'ldan o'qiladi (ogohlantirishsiz)."""
    sogolom, _buzuq = juftlik
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        reader.read_file(sogolom)
    assert not [x for x in w if "SST" in str(x.message)]


# ═══════════════════════════════════════════════════════════════════════════
# `dimension` da 1 mln qator e'lon qilingan, ma'lumot esa kichik
# (2026-09-02: id=47880 — 104 KB fayl «Итог» varag'ida 1 048 508 qator deb
#  e'lon qilgan; oddiy yo'l 10 daqiqada ham o'qiy olmay «juda ko'p qator»
#  deb RAD etardi, aslida 1 365 banddan 1 012 tasi narxlangan HALOL hujjat)
# ═══════════════════════════════════════════════════════════════════════════

def _keng_qator_xlsx(yol, elon_qator=1048508, haqiqiy_qator=40):
    """`dimension` yolg'on, ma'lumot esa kichik bo'lgan .xlsx yasaydi."""
    import zipfile
    kataklar = []
    for r in range(1, haqiqiy_qator + 1):
        kataklar.append(
            f'<row r="{r}"><c r="A{r}" t="inlineStr"><is><t>ish {r}</t></is></c>'
            f'<c r="B{r}"><v>{150000 + r}</v></c></row>')
    sheet = ('<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats'
             '.org/spreadsheetml/2006/main">'
             f'<dimension ref="A1:IO{elon_qator}"/><sheetData>'
             + "".join(kataklar) + "</sheetData></worksheet>")
    wb = ('<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/'
          'spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/'
          'officeDocument/2006/relationships"><sheets><sheet name="Итог" '
          'sheetId="1" r:id="rId1"/></sheets></workbook>')
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
        z.writestr("xl/worksheets/sheet1.xml", sheet)
    return yol


def test_preflight_haqiqiy_qatorni_topadi(tmp_path):
    yol = _keng_qator_xlsx(str(tmp_path / "keng.xlsx"))
    haqiqiy, elon, haqiqiy_qator, elon_qator = reader._xlsx_preflight(yol)
    assert elon_qator == 1048508, "e'lon qilingan qator o'qilmadi"
    assert haqiqiy_qator == 40, f"haqiqiy qator noto'g'ri: {haqiqiy_qator}"
    assert haqiqiy == 2


def test_yolgon_dimension_rad_ETILMAYDI(tmp_path):
    """ENG MUHIMI: bunday fayl rad etilmasin va TEZ o'qilsin."""
    import time
    yol = _keng_qator_xlsx(str(tmp_path / "keng.xlsx"))
    t0 = time.time()
    varaqlar = reader.read_file(yol)          # ExcelTooLargeError BO'LMASIN
    davomiylik = time.time() - t0
    assert davomiylik < 10, f"juda sekin: {davomiylik:.1f}s"
    qatorlar = varaqlar["Итог"]
    tola = [q for q in qatorlar if any(c is not None for c in q)]
    assert len(tola) == 40, f"ma'lumotli qator soni: {len(tola)}"
    assert tola[0][0] == "ish 1" and tola[0][1] == 150001
