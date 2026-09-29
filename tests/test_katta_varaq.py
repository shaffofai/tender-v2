# -*- coding: utf-8 -*-
"""Ulkan varaq XML — «butun varaqqa format» (2026-09-08).

Jonli navbatda 2 ta halol smeta (76032: 328 MB XML, 138285: 369 MB) «zip bomba
bo'lishi mumkin» bilan o'lik edi: Excel millionlab BO'SH uslubli katakni faylga
yozgan. Endi `_read_xlsx` chegaradan oshgan varaqni oqimli qirqib (qiymatsiz
kataklar va bo'sh qatorlar tashlanadi) qayta o'qiydi. Haqiqiy bomba himoyasi
SAQLANADI: qirqilgach ham katta bo'lsa yoki oqim `MAX_XLSX_QIRQISH_BYTES` dan
oshsa — ExcelTooLargeError.
"""
import io
import os
import sys
import time
import zipfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tender_engine import reader  # noqa: E402

_NS = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'


def _xlsx_yasa(yol, sheet_xml, varaq_nomi="Смета"):
    wb = (f'<?xml version="1.0"?><workbook {_NS} xmlns:r="http://schemas.openxmlformats.org/'
          f'officeDocument/2006/relationships"><sheets><sheet name="{varaq_nomi}" '
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
          'officedocument.spreadsheetml.worksheet+xml"/><Override PartName='
          '"/xl/styles.xml" ContentType="application/vnd.openxmlformats-'
          'officedocument.spreadsheetml.styles+xml"/></Types>')
    # `s="1"`/`s="2"` uslub indekslari uchun minimal styles.xml — usiz openpyxl
    # uslubli katakda IndexError beradi (haqiqiy fayllarda styles doim bor).
    st = (f'<?xml version="1.0"?><styleSheet {_NS}><fonts count="1"><font/></fonts>'
          '<fills count="1"><fill><patternFill patternType="none"/></fill></fills>'
          '<borders count="1"><border/></borders>'
          '<cellStyleXfs count="1"><xf/></cellStyleXfs>'
          '<cellXfs count="3"><xf/><xf/><xf/></cellXfs>'
          '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
          '</styleSheet>')
    with zipfile.ZipFile(yol, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("_rels/.rels", rl)
        z.writestr("xl/workbook.xml", wb)
        z.writestr("xl/_rels/workbook.xml.rels", wr)
        z.writestr("xl/styles.xml", st)
        z.writestr("xl/worksheets/sheet1.xml", sheet_xml)
    return yol


def _formatlangan_varaq(haqiqiy=40, bosh_qator=3000, bosh_ustun=30, dimension="A1:J1048576",
                        merge=""):
    """Boshida haqiqiy ma'lumot, keyin minglab qator × o'nlab BO'SH uslubli katak."""
    q = []
    for r in range(1, haqiqiy + 1):
        q.append(f'<row r="{r}" spans="1:10"><c r="A{r}" t="inlineStr"><is><t>ish {r}</t></is></c>'
                 f'<c r="B{r}" s="2"><v>{150000 + r}</v></c>'
                 f'<c r="C{r}" s="1"/><c r="D{r}" s="1"></c>'
                 f'<c r="E{r}"><f>B{r}*2</f></c></row>')
    ustunlar = "".join(f'<c r="{chr(65 + j % 26)}{{r}}" s="1"/>' for j in range(bosh_ustun))
    for r in range(haqiqiy + 1, haqiqiy + 1 + bosh_qator):
        q.append(f'<row r="{r}" spans="1:30">' + ustunlar.replace("{r}", str(r)) + "</row>")
    return (f'<?xml version="1.0"?><worksheet {_NS}><dimension ref="{dimension}"/>'
            '<cols><col min="1" max="1" width="40"/></cols><sheetData>'
            + "".join(q) + "</sheetData>" + merge + "</worksheet>")


def test_qirq_oqim_faqat_qiymatsiz_kataklarni_tashlaydi():
    xml = (b'<worksheet><cols><col min="1" max="2" width="9"/></cols><sheetData>'
           b'<row r="1"><c r="A1" s="1"/><c r="B1"><v>5</v></c><c r="C1" s="2"></c>'
           b'<c r="D1" t="inlineStr"><is><t>x</t></is></c><c r="E1"><f>B1*2</f></c></row>'
           b'<row r="2" spans="1:5"><c r="A2" s="1"/><c r="B2" s="1"/></row>'
           b'<row r="3"/>'
           b'<row r="4"><c r="A4"><v>7</v></c></row></sheetData></worksheet>')
    dst = io.BytesIO()
    oqildi, yozildi = reader._qirq_oqim(io.BytesIO(xml), dst)
    n = dst.getvalue()
    assert oqildi == len(xml) and yozildi == len(n)
    assert b'<c r="B1"><v>5</v></c>' in n and b'<is><t>x</t></is>' in n and b"<f>B1*2</f>" in n
    assert b'r="A1"' not in n and b'r="C1"' not in n           # qiymatsiz kataklar yo'q
    assert b'<row r="2"' not in n and b'<row r="3"' not in n   # bo'sh qatorlar yo'q
    assert b'<row r="4"><c r="A4"><v>7</v></c></row>' in n
    assert b'<col min="1" max="2" width="9"/>' in n            # <col> ga tegilmagan


def test_formatlangan_bosh_kataklar_RAD_ETILMAYDI(tmp_path, monkeypatch):
    """76032/138285 naqshi: chegaradan oshgan varaq qirqilib, TEZ va TO'LIQ o'qiladi."""
    monkeypatch.setattr(reader, "MAX_XLSX_PART_BYTES", 200_000)
    yol = _xlsx_yasa(str(tmp_path / "format.xlsx"), _formatlangan_varaq())
    with zipfile.ZipFile(yol) as z:
        assert z.getinfo("xl/worksheets/sheet1.xml").file_size > 200_000
    with pytest.raises(reader.XlsxPartTooLargeError):
        reader._xlsx_preflight(yol)                       # chegara hamon ishlaydi
    t0 = time.time()
    with pytest.warns(UserWarning, match="qirqildi"):
        varaqlar = reader.read_file(yol)
    assert time.time() - t0 < 15
    qatorlar = varaqlar["Смета"]
    tola = [q for q in qatorlar if any(c is not None for c in q)]
    assert len(tola) == 40
    assert tola[0][0] == "ish 1" and tola[0][1] == 150001 and tola[39][1] == 150040


def test_haqiqiy_katta_malumot_baribir_RAD(tmp_path, monkeypatch):
    """Qirqilgach ham chegaradan katta (haqiqiy ma'lumot shuncha) — himoya saqlanadi."""
    monkeypatch.setattr(reader, "MAX_XLSX_PART_BYTES", 20_000)
    yol = _xlsx_yasa(str(tmp_path / "katta.xlsx"), _formatlangan_varaq(haqiqiy=2000, bosh_qator=0))
    with pytest.raises(reader.ExcelTooLargeError):
        reader.read_file(yol)


def test_oqim_chegarasi_haqiqiy_bomba(tmp_path, monkeypatch):
    """Oqim `MAX_XLSX_QIRQISH_BYTES` dan oshsa — darhol ExcelTooLargeError («bomba»)."""
    monkeypatch.setattr(reader, "MAX_XLSX_PART_BYTES", 50_000)
    monkeypatch.setattr(reader, "MAX_XLSX_QIRQISH_BYTES", 100_000)
    monkeypatch.setattr(reader, "_QIRQISH_BOLAK", 64 * 1024)
    yol = _xlsx_yasa(str(tmp_path / "bomba.xlsx"), _formatlangan_varaq(bosh_qator=3000))
    t0 = time.time()
    with pytest.raises(reader.ExcelTooLargeError, match="bomba"):
        reader.read_file(yol)
    assert time.time() - t0 < 5


def test_merge_va_oddiy_yol_saqlanadi(tmp_path, monkeypatch):
    """Dimension kichik bo'lsa oddiy yo'l (merge ochiladi) — qirqish unga xalaqit bermaydi."""
    monkeypatch.setattr(reader, "MAX_XLSX_PART_BYTES", 200_000)
    merge = '<mergeCells count="1"><mergeCell ref="A1:C1"/></mergeCells>'
    yol = _xlsx_yasa(str(tmp_path / "merge.xlsx"),
                     _formatlangan_varaq(dimension="A1:J3040", merge=merge))
    with pytest.warns(UserWarning, match="qirqildi"):
        varaqlar = reader.read_file(yol)
    q1 = varaqlar["Смета"][0]
    assert q1[0] == q1[1] == q1[2] == "ish 1"          # merge A1:C1 kengaytirilgan (oddiy yo'l)
    assert varaqlar["Смета"][1][1] == 150002           # merge'siz qator — qiymat joyida


def test_chegaradan_kichik_fayl_TEGILMAYDI(tmp_path):
    """Regressiya himoyasi: oddiy fayl uchun qirqish umuman chaqirilmaydi."""
    yol = _xlsx_yasa(str(tmp_path / "oddiy.xlsx"), _formatlangan_varaq(bosh_qator=5, dimension="A1:J45"))
    import warnings
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        varaqlar = reader.read_file(yol)
    assert not [x for x in w if "qirqildi" in str(x.message)], "qirqish chaqirilmasligi kerak edi"
    assert varaqlar["Смета"][0][0] == "ish 1"
