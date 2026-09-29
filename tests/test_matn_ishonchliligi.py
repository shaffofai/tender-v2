# -*- coding: utf-8 -*-
"""Tiklashning ISHONCHLILIGI: matni yulingan hujjatga mazmun da'vosi qilinmasin.

MUAMMO (2026-09-07 ko'rigi). Zaxira zanjiri `xl/sharedStrings.xml` yo'q
faylni ochadi, lekin MATNNI qaytara olmaydi — u zipda umuman yo'q. Sonlar
esa to'liq saqlanadi (`<v>` varaq XML ida yotadi). 92 buzuq fayldan 52 tasi
aynan shunday tiklanadi.

Belgisiz qolganda bu tiklashning O'Z MAQSADIGA zid ishlardi: hujjat ochilardi,
so'ng MATNGA tayangan qoidalar uni rad etardi — «sarlavha topilmadi»,
«bandlarga qiymat qo'yilmagan». Nazorat tajribasi: sog'lom, TO'G'RI
TO'LDIRILGAN va status=1 oladigan 5 ta hujjatdan lug'at olib tashlansa,
5/5 da verdikt 1 -> 2 ga o'tardi — dvigatelning O'ZI hujjatda 29 398 ta
yangi son ko'rib turganiga qaramay (band tavsiflari yo'qolgani uchun band=2).
Ustuvorlik qoidasi bo'yicha bu eng yomon xato turi: noo'rin rad.

Bu testlar uchta shartni qotiradi:
  1. BELGI UZATILADI — `read_file(yol, hisobot)` `matn_tiklanmadi` beradi,
     sog'lom faylda esa `hisobot` ga UMUMAN tegilmaydi.
  2. MAZMUN DA'VOSI QILINMAYDI — matn ishonchsiz bo'lsa hukm FAQAT sonlarga
     tayanadi; sonlar bo'lmasa rad emas, TEXNIK holat qaytadi.
  3. BO'SH HUJJAT QABUL BO'LMAYDI — matnni yo'qotish «hamma narsani qabul
     qilish» ga aylanmaydi.
"""
import os
import sys
import warnings
import zipfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

openpyxl = pytest.importorskip("openpyxl")

from tender_engine import decision, reader
from tender_engine import tiklash_nol_bayt as NB
from tender_engine import tiklash_xom_xml as XX

LOYIHA = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ── Yordamchilar ────────────────────────────────────────────────────────────
#
# Fikstura QO'LDA yig'iladi: openpyxl matnni `inlineStr` bilan yozadi va
# `sharedStrings.xml` ni UMUMAN yaratmaydi — ya'ni uning fayli bu buzilish
# sinfini takrorlamaydi. Excel esa matnni lug'atda saqlaydi, 44/46 buzuq
# fayl aynan shunday.
_SS_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_SS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_SS_PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
_SS_CT = "http://schemas.openxmlformats.org/package/2006/content-types"
_CT_OFF = "application/vnd.openxmlformats-officedocument.spreadsheetml"


def _xlsx_yasa(yol, matnlar, sonlar):
    """Matni `sharedStrings.xml` da saqlanadigan minimal .xlsx.

    `matnlar` — lug'at satrlari (1-qatorga sarlavha bo'lib tushadi),
    `sonlar` — har biri alohida qatorning B ustuniga.
    """
    kataklar = "".join(
        f'<c r="{chr(65 + i)}1" t="s"><v>{i}</v></c>'
        for i in range(len(matnlar)))
    qatorlar = [f'<row r="1">{kataklar}</row>']
    for k, son in enumerate(sonlar, start=2):
        qatorlar.append(f'<row r="{k}"><c r="A{k}" t="s"><v>0</v></c>'
                        f'<c r="B{k}"><v>{son}</v></c></row>')
    with zipfile.ZipFile(yol, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",
                   f'<Types xmlns="{_SS_CT}">'
                   '<Default Extension="rels" ContentType="application/vnd.'
                   'openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   f'<Override PartName="/xl/workbook.xml" ContentType='
                   f'"{_CT_OFF}.sheet.main+xml"/>'
                   f'<Override PartName="/xl/worksheets/sheet1.xml" '
                   f'ContentType="{_CT_OFF}.worksheet+xml"/>'
                   f'<Override PartName="/xl/sharedStrings.xml" ContentType='
                   f'"{_CT_OFF}.sharedStrings+xml"/></Types>')
        z.writestr("_rels/.rels",
                   f'<Relationships xmlns="{_SS_PKG}"><Relationship Id="rId1" '
                   f'Type="{_SS_REL}/officeDocument" Target="xl/workbook.xml"/>'
                   '</Relationships>')
        z.writestr("xl/workbook.xml",
                   f'<workbook xmlns="{_SS_NS}" xmlns:r="{_SS_REL}"><sheets>'
                   '<sheet name="Смета" sheetId="1" r:id="rId1"/></sheets>'
                   '</workbook>')
        z.writestr("xl/_rels/workbook.xml.rels",
                   f'<Relationships xmlns="{_SS_PKG}">'
                   f'<Relationship Id="rId1" Type="{_SS_REL}/worksheet" '
                   'Target="worksheets/sheet1.xml"/>'
                   f'<Relationship Id="rId2" Type="{_SS_REL}/sharedStrings" '
                   'Target="sharedStrings.xml"/></Relationships>')
        z.writestr("xl/worksheets/sheet1.xml",
                   f'<worksheet xmlns="{_SS_NS}"><sheetData>'
                   + "".join(qatorlar) + '</sheetData></worksheet>')
        z.writestr("xl/sharedStrings.xml",
                   f'<sst xmlns="{_SS_NS}" count="{len(matnlar)}" '
                   f'uniqueCount="{len(matnlar)}">'
                   + "".join(f"<si><t>{m}</t></si>" for m in matnlar)
                   + "</sst>")
    return yol


def _ss_siz(manba, nishon):
    """`xl/sharedStrings.xml` siz nusxa — 44/46 buzuq faylning aynan sinfi."""
    with zipfile.ZipFile(manba) as z, \
            zipfile.ZipFile(nishon, "w", zipfile.ZIP_DEFLATED) as o:
        for i in z.infolist():
            if i.filename != "xl/sharedStrings.xml":
                o.writestr(i, z.read(i))
    return nishon


def _oq(yol):
    hisobot = {}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        varaqlar = reader.read_file(yol, hisobot)
    return varaqlar, hisobot


# ── 1. Belgi uzatiladi ──────────────────────────────────────────────────────
def test_soglom_faylda_hisobot_bosh_qoladi(tmp_path):
    """Sog'lom hujjat oddiy yo'ldan o'tadi — belgi qo'yilmaydi.

    Bo'sh lug'at = «tiklash bo'lmadi». Aks holda har fayl «ishonchsiz» deb
    belgilanib, butun qoidalar zanjiri o'chib qolardi.
    """
    yol = _xlsx_yasa(str(tmp_path / "soglom.xlsx"),
                     ["Бетон ишлари", "Ғишт терими"], [1250000, 3400000])
    varaqlar, hisobot = _oq(yol)
    assert hisobot == {}
    assert varaqlar["Смета"][0][0] == "Бетон ишлари"


def test_sharedstrings_yoq_fayl_belgilanadi(tmp_path):
    """Lug'at zipda yo'q -> `matn_tiklanmadi`, va fayl BARIBIR ochiladi."""
    asl = _xlsx_yasa(str(tmp_path / "asl.xlsx"),
                     ["Бетон ишлари", "Ғишт терими"], [1250000, 3400000])
    buzuq = _ss_siz(asl, str(tmp_path / "buzuq.xlsx"))

    varaqlar, hisobot = _oq(buzuq)
    assert hisobot.get("matn_tiklanmadi") is True
    assert hisobot.get("usul") == "_zaxira_xom_xml"
    # SONLAR buzilmaydi — verdikt aynan shularga tayanadi.
    sonlar = {v for q in varaqlar.values() for r in q for v in r
              if isinstance(v, (int, float)) and not isinstance(v, bool)}
    assert {1250000, 3400000} <= sonlar


def test_yiqilgan_usul_belgisi_yopishib_qolmaydi(tmp_path, monkeypatch):
    """Zanjirda oldingi usul yozgan belgi keyingisining natijasiga o'tmaydi.

    Har usul O'Z lug'atiga yozadi va u faqat MUVAFFAQIYATDA ko'chiriladi —
    aks holda yiqilgan urinishning `matn_tiklanmadi` si sog'lom tiklashga
    yopishib, hujjat noo'rin «ishonchsiz» bo'lib qolardi.
    """
    def _belgilab_yiqiladi(filepath, asl_xato, hisobot=None):
        if hisobot is not None:
            hisobot["matn_tiklanmadi"] = True
        raise RuntimeError("bu usul ishlamadi")

    def _muvaffaqiyat(filepath, asl_xato, hisobot=None):
        return {"Sheet1": [[1, 2, 3]]}

    monkeypatch.setattr(reader, "_ZAXIRA_ZANJIRI",
                        (_belgilab_yiqiladi, _muvaffaqiyat))
    hisobot = {}
    natija = reader._zaxira_zanjiri(str(tmp_path / "yoq.xlsx"),
                                    zipfile.BadZipFile("x"), hisobot)
    assert natija == {"Sheet1": [[1, 2, 3]]}
    assert "matn_tiklanmadi" not in hisobot
    assert hisobot["usul"] == "_muvaffaqiyat"


# ── 2. Mazmun da'vosi qilinmaydi ────────────────────────────────────────────
def test_matnsiz_hujjat_narx_bilan_qabul_qilinadi():
    """Sonlar bor — QABUL (REVIEW izi bilan), «bandlarga qiymat yo'q» EMAS."""
    et = {"S": [["Т/Р", "Иш номи", "Нархи"],
                [1, "Бетон", None], [2, "Ғишт", None], [3, "Темир", None],
                [4, "Бўёқ", None], [5, "Ёғоч", None], [6, "Шиша", None]]}
    # Ishtirokchi to'ldirgan, LEKIN matni yulingan (bo'sh joy o'rinbosari).
    pt = {"S": [[" ", " ", " "],
                [1, " ", 1250000], [2, " ", 3400000], [3, " ", 5600000],
                [4, " ", 7800000], [5, " ", 9100000], [6, " ", 4300000]]}

    yaxshi = decision.hukm_shakl_erkin(et, pt, matn_ishonchsiz=True)

    assert yaxshi["tashqi"] == 1
    assert yaxshi["ichki"] == "REVIEW_AMBIGUOUS"      # sinf='review' izi
    assert yaxshi["matn_ishonchsiz"] is True


def test_matnsiz_hujjat_sonsiz_bolsa_rad_emas_texnik():
    """Sonlar ham yo'q — RAD EMAS, texnik holat (status 0).

    Narx MATN sifatida kiritilgan bo'lishi mumkin («1 234 567» satr) va u
    lug'at bilan birga yo'qolgan — ya'ni rad uchun asosimiz yo'q.
    Ustuvorlik qoidasi: noo'rin rad noo'rin qabuldan yomonroq.
    """
    et = {"S": [["Т/Р", "Иш номи", "Нархи"], [1, "Бетон", None]]}
    pt = {"S": [[" ", " ", " "], [1, " ", None]]}

    h = decision.hukm_shakl_erkin(et, pt, matn_ishonchsiz=True)
    assert h["tashqi"] == 0
    assert h["ichki"] == "TECHNICAL_ERROR"
    assert h["kodlar"] == [decision.KOD_MATN_TIKLANMADI]
    # Ishtirokchini ayblaydigan matn CHIQMASIN.
    assert "bandlarga qiymat" not in h["comment_uz"]


def test_matnsiz_hujjat_aynan_nusxa_baribir_rad():
    """Q1 saqlanadi: bayt-ba-bayt nusxa matn belgisidan qat'i nazar rad."""
    et = {"S": [["a", 1]]}
    h = decision.hukm_shakl_erkin(et, et, bir_xil_fayl=True,
                                  matn_ishonchsiz=True)
    assert h["tashqi"] == 2
    assert h["kodlar"] == ["IDENTICAL_COPY"]


# ── 3. Haqiqiy nazorat tajribasi (1 -> 2 flip qaytmasin) ────────────────────
NAZORAT = [
    (r"data_new\default_buyurtmachi_data\1649137032-1. Жамланма жадвал.xlsx",
     r"data_new\ishtirokchi_1\1650516748-1649137032-1.Жамланмажадвал.xlsx"),
    (r"data_new\default_buyurtmachi_data_2\1648641446-КАП_РЕМОНТ_№7_ШКОЛЫ_СЕРГЕЛИЙ_28,3,2022.xlsx",
     r"data_new\ishtirokchi_2.3\1649954212-1648641446_КАП_РЕМОНТ_№7_ШКОЛЫ_СЕРГЕЛИЙ_28,3,2022(2).xlsx"),
]


def _nazorat_bor():
    return all(os.path.isfile(os.path.join(LOYIHA, p))
               for juft in NAZORAT for p in juft)


@pytest.mark.skipif(not _nazorat_bor(), reason="data_new nazorat fayllari yo'q")
@pytest.mark.parametrize("et_r,pt_r", NAZORAT,
                         ids=lambda p: os.path.basename(p)[:24])
def test_haqiqiy_toldirilgan_hujjat_matnsiz_ham_rad_etilmaydi(et_r, pt_r, tmp_path):
    """TO'LDIRILGAN hujjatdan lug'at olib tashlansa ham rad chiqmasin.

    Aynan shu tajriba tuzatishdan oldin 5/5 da verdiktni 1 dan 2 ga
    o'tkazardi.
    """
    et_yol = os.path.join(LOYIHA, et_r)
    pt_yol = os.path.join(LOYIHA, pt_r)
    buzuq = _ss_siz(pt_yol, str(tmp_path / "b.xlsx"))

    et, _ = _oq(et_yol)
    pt, hisobot = _oq(buzuq)
    assert hisobot.get("matn_tiklanmadi") is True

    h = decision.hukm_shakl_erkin(et, pt, matn_ishonchsiz=True)
    assert h["tashqi"] == 1, h["comment_uz"]


@pytest.mark.skipif(not _nazorat_bor(), reason="data_new nazorat fayllari yo'q")
@pytest.mark.parametrize("et_r,pt_r", NAZORAT,
                         ids=lambda p: os.path.basename(p)[:24])
def test_etalonning_ozi_matnsiz_qabul_bolmaydi(et_r, pt_r, tmp_path):
    """Qarshi tekshiruv: hech narsa kiritilmagan hujjat QABUL bo'lmasin."""
    et_yol = os.path.join(LOYIHA, et_r)
    buzuq = _ss_siz(et_yol, str(tmp_path / "b.xlsx"))

    et, _ = _oq(et_yol)
    pt, hisobot = _oq(buzuq)
    assert hisobot.get("matn_tiklanmadi") is True

    h = decision.hukm_shakl_erkin(et, pt, matn_ishonchsiz=True)
    assert h["tashqi"] != 1, h["comment_uz"]


# ── 4. Sintez qilingan qism BOMBA bo'lib qolmasin ───────────────────────────
#
# Lug'at o'lchami HUJJATDAN keladi (`<c t="s"><v>N</v></c>` dagi N ni
# ishtirokchi to'liq nazorat qiladi). Chegarasiz 1.8 KB li fayl 80 MB lik
# qism yasardi va uni o'qish daqiqalar olardi — ya'ni bombani tashqi fayl
# emas, TA'MIRNING O'ZI yasardi (o'lchangan: indeks 5 000 000 -> 487 s).
def test_nol_bayt_sst_chegarasi():
    varaq = {"xl/worksheets/sheet1.xml":
             b'<c r="A1" t="s"><v>%d</v></c>' % (NB.NOL_BAYT_MAX_SST + 1)}
    with pytest.raises(NB.TiklashXatosi):
        NB._ss_orinbosar(varaq)


def test_nol_bayt_sst_chegara_ichida_ishlaydi():
    varaq = {"xl/worksheets/sheet1.xml": b'<c r="A1" t="s"><v>3</v></c>'}
    assert NB._ss_orinbosar(varaq).count(b"<si>") == 4


def test_xom_xml_sst_chegarasi():
    with pytest.raises(XX.TiklashBudjeti):
        XX._sst_yasa(XX.MAX_SST + 1)


def test_xom_xml_sintez_qism_budjetdan_otadi(tmp_path):
    """Sintez qilingan qismlar ham hajm budjetiga kiradi.

    Ilgari `_nusxa_yoz` faqat KO'CHIRILAYOTGAN a'zolarni hisoblardi —
    yasalgan qism budjetni umuman ko'rmasdi.
    """
    manba = str(tmp_path / "m.zip")
    with zipfile.ZipFile(manba, "w") as z:
        z.writestr("kichik.xml", b"<a/>")

    with zipfile.ZipFile(manba) as z:
        with pytest.raises(XX.TiklashBudjeti):
            XX._nusxa_yoz(z, str(tmp_path / "chiq.xlsx"),
                          {"katta.xml": b"x" * 5000},
                          XX._Budjet(jami_bayt=1000))
