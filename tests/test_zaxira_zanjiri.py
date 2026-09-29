# -*- coding: utf-8 -*-
"""Zaxira zanjiri — `reader.read_file` ga ulangan 4 ta tiklash moduli.

92 ta hujjat «Faylni ochib bo'lmadi» deb RAD etilgan edi. Sabablari beshta
va HAMMASI bizning o'quvchimizning cheklovi bo'lib chiqdi:

    46 KeyError (zipda `workbook.xml`/`sharedStrings.xml` yo'q) -> xom XML
    24 BadZipFile («Bad offset», har 0x00 bayt 0x20 ga aylangan) -> nol-bayt
     4 XLRDError «Workbook is encrypted» (standart parol)        -> RC4
     1 OSError «no valid workbook part» (aslida XLSB)            -> XLSB
     1 AttributeError «Chartsheet has no max_row»                -> asosiy yo'l

Bu testlar zanjirning UCHTA xavfsizlik shartini qotiradi:

  1. EKVIVALENTLIK — sog'lom fayl zanjirga UMUMAN kirmaydi, ya'ni avvalgi
     yo'ldan aynan o'tadi (`test_soglom_fayl_zanjirga_kirmaydi`).
  2. ASL XATO — hech bir usul yordam bermasa ishtirokchi ko'radigan xato
     AYNAN o'sha obyekt bo'lib qaytadi (`test_asl_xato_aynan_qaytadi`).
  3. ASL FAYL — tiklash faqat vaqtinchalik nusxada bo'ladi, manba fayl
     bayt-ba-bayt o'zgarmaydi (`test_asl_fayllarga_tegilmaydi`).

Va o'chirish kaliti: `TIKLASH_YOQ=0` butun zanjirni o'chiradi.
"""
import hashlib
import os
import sys
import warnings
import zipfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

openpyxl = pytest.importorskip("openpyxl")

from tender_engine import reader

LOYIHA = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Haqiqiy buzuq fayllar to'plami (bo'lmasa shu testlar o'tkazib yuboriladi).
BUZUQ_PAPKA = os.environ.get("BUZUQ_KORPUS", os.path.join(
    os.environ.get("TEMP", "/tmp"), "claude",
    "c--Users-User-Downloads-Telegram-Desktop-tender-reject",
    "8f27e0d5-5d9b-464e-8df2-f3df5ba17509", "scratchpad", "buzuq_fayllar"))


def _buzuq_fayllar():
    if not os.path.isdir(BUZUQ_PAPKA):
        return []
    return [os.path.join(BUZUQ_PAPKA, n) for n in sorted(os.listdir(BUZUQ_PAPKA))
            if n.lower().endswith((".xls", ".xlsx")) and not n.startswith("~$")]


BUZUQ = _buzuq_fayllar()
buzuq_kerak = pytest.mark.skipif(
    not BUZUQ, reason=f"buzuq fayllar to'plami yo'q: {BUZUQ_PAPKA}")


def _soglom_fayllar():
    """`data/` va `data_new/` — nazorat (sog'lom) hujjatlari."""
    yollar = []
    for papka in ("data", "data_new"):
        for dirp, _, fayllar in os.walk(os.path.join(LOYIHA, papka)):
            for f in fayllar:
                if f.lower().endswith((".xls", ".xlsx")) and not f.startswith("~$"):
                    yollar.append(os.path.join(dirp, f))
    return sorted(yollar)


SOGLOM = _soglom_fayllar()


def _sonlar(varaqlar):
    """Chiqarilgan barcha SON kataklar (narxlar shu yerda)."""
    return [q for qatorlar in varaqlar.values() for qator in qatorlar
            for q in qator if isinstance(q, (int, float)) and not isinstance(q, bool)]


def _sha(yol):
    with open(yol, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


# ── 1. Ekvivalentlik: sog'lom fayl zanjirga kirmaydi ────────────────────────
@pytest.mark.skipif(not SOGLOM, reason="nazorat fayllari yo'q")
def test_soglom_fayl_zanjirga_kirmaydi(monkeypatch):
    """ENG MUHIM KAFOLAT: sog'lom hujjat uchun bitta ham qo'shimcha ish yo'q.

    Zanjirni «portlaydigan» funksiya bilan almashtiramiz — u chaqirilsa
    test yiqiladi. Ya'ni sog'lom fayl uchun qo'shimcha zip ochish ham,
    tekshiruv ham bo'lmagani ISBOTLANADI, taxmin qilinmaydi.
    """
    def _portlaydi(*a, **k):
        raise AssertionError("sog'lom fayl uchun zaxira zanjiri chaqirildi")

    monkeypatch.setattr(reader, "_zaxira_zanjiri", _portlaydi)
    for yol in SOGLOM:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            varaqlar = reader.read_file(yol)
        assert varaqlar, f"nazorat fayli bo'sh o'qildi: {yol}"


@pytest.mark.skipif(not SOGLOM, reason="nazorat fayllari yo'q")
def test_soglom_natija_zanjirdan_qatiy_nazar_bir_xil(monkeypatch):
    """Zanjir yoqilgan va o'chirilgan holatda sog'lom fayl AYNAN bir xil o'qiladi."""
    for yol in SOGLOM:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            yoqiq = reader.read_file(yol)
            monkeypatch.setenv("TIKLASH_YOQ", "0")
            ochiq = reader.read_file(yol)
            monkeypatch.delenv("TIKLASH_YOQ")
        assert yoqiq == ochiq, f"zanjir natijani o'zgartirdi: {yol}"


# ── 2. Asl xato saqlanadi ──────────────────────────────────────────────────
def test_asl_xato_aynan_qaytadi(tmp_path):
    """Hech bir usul yordam bermasa — AYNAN o'sha xato obyekti qaytadi.

    5-xavfsizlik sharti: ishtirokchiga ko'rinadigan izoh tiklash urinishi
    tufayli o'zgarmasin. Bu yerda fayl umuman Excel emas.
    """
    yol = str(tmp_path / "yolgon.xlsx")
    with open(yol, "wb") as fh:
        fh.write(b"bu umuman Excel emas, oddiy matn" * 8)

    with pytest.raises(Exception) as birinchi:
        reader.read_file(yol)
    # Zanjir o'chirilgandagi xato bilan AYNAN bir xil turda bo'lsin.
    os.environ["TIKLASH_YOQ"] = "0"
    try:
        with pytest.raises(Exception) as ikkinchi:
            reader.read_file(yol)
    finally:
        del os.environ["TIKLASH_YOQ"]
    assert type(birinchi.value) is type(ikkinchi.value)
    assert str(birinchi.value) == str(ikkinchi.value)


def test_qollab_quvvatlanmagan_kengaytma():
    """Zanjir avvalgi `ValueError` ni yutib yubormaydi."""
    with pytest.raises(ValueError, match="Unsupported file format"):
        reader.read_file("hech-qanday.csv")


def test_zanjir_hech_qachon_istisno_bermaydi(tmp_path, monkeypatch):
    """`_zaxira_zanjiri` yiqilgan modul bilan ham `None` qaytaradi.

    Deploy manifestida modul yetishmasa (bu loyihada ALLAQACHON bo'lgan
    nosozlik) zanjir jim o'tib ketishi va ASL xato chiqishi kerak — worker
    `ModuleNotFoundError` bilan yiqilmasin.
    """
    def _yiqiladi(filepath, asl_xato):
        raise ModuleNotFoundError("tender_engine.tiklash_yoq")

    monkeypatch.setattr(reader, "_ZAXIRA_ZANJIRI", (_yiqiladi,))
    yol = str(tmp_path / "b.xlsx")
    with open(yol, "wb") as fh:
        fh.write(b"PK\x03\x04buzuq")
    assert reader._zaxira_zanjiri(yol, zipfile.BadZipFile("x")) is None


# ── 3. O'chirish kaliti ────────────────────────────────────────────────────
@buzuq_kerak
def test_ochirish_kaliti_butun_zanjirni_ochiradi(monkeypatch):
    """`TIKLASH_YOQ=0` — hech bir usul chaqirilmaydi, avvalgi xato qaytadi."""
    monkeypatch.setenv("TIKLASH_YOQ", "0")

    def _portlaydi(*a, **k):
        raise AssertionError("o'chirilgan zanjir chaqirildi")

    monkeypatch.setattr(reader, "_ZAXIRA_ZANJIRI", (_portlaydi,))
    yiqildi = 0
    for yol in BUZUQ:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                reader.read_file(yol)
        except AssertionError:
            raise
        except Exception:
            yiqildi += 1
    # Kalit o'chirilganda to'plamning KO'PCHILIGI baribir ochilmaydi —
    # ya'ni zanjir haqiqatan ham o'chgan (aks holda hammasi ochilardi).
    assert yiqildi >= 70, f"faqat {yiqildi} ta fayl yiqildi — kalit ishlamadi?"


# ── 4. Haqiqiy to'plam: 92/92 ochiladi ─────────────────────────────────────
@buzuq_kerak
def test_haqiqiy_toplam_hajmi():
    """Sinov qamrovi jimgina qisqarmasin."""
    assert len(BUZUQ) >= 92, f"faqat {len(BUZUQ)} ta fayl topildi"


@buzuq_kerak
@pytest.mark.parametrize("yol", BUZUQ, ids=lambda p: os.path.basename(p))
def test_buzuq_fayl_ochiladi_va_sonlari_bor(yol):
    """Har bir fayl ochiladi VA ichidan haqiqiy sonlar chiqadi.

    Faqat «ochildi» yetarli emas: tiklash matnni yo'qotishi mumkin, lekin
    NARXLAR to'liq qaytishi shart — verdikt aynan shularga tayanadi.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        varaqlar = reader.read_file(yol)
    assert varaqlar, "varaq qaytmadi"
    assert _sonlar(varaqlar), "bironta ham son katak chiqmadi"


@buzuq_kerak
def test_asl_fayllarga_tegilmaydi():
    """2-xavfsizlik sharti: manba fayl bayt-ba-bayt o'zgarmasin."""
    oldin = {y: _sha(y) for y in BUZUQ}
    for yol in BUZUQ:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                reader.read_file(yol)
        except Exception:
            pass
    for yol in BUZUQ:
        assert _sha(yol) == oldin[yol], f"asl fayl o'zgardi: {yol}"


@buzuq_kerak
def test_natija_determinik():
    """Bir xil fayl -> bir xil natija (ikki marta o'qish teng bo'lsin)."""
    for yol in BUZUQ[:12]:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            assert reader.read_file(yol) == reader.read_file(yol), yol


# ── 5. Diagramma varag'i (5-tuzatish, asosiy yo'ldagi nuqson) ──────────────
def test_diagramma_varagi_hujjatni_yiqitmaydi(tmp_path):
    """Chartsheet bo'lgan fayl OCHILADI — `wb.sheetnames` da u ham bor edi.

    Ilgari `wb[nom]` `Chartsheet` qaytarib, `ws.max_row` da `AttributeError`
    bo'lardi va SOG'LOM hujjat butunlay rad etilardi (id=122844).
    """
    from openpyxl.chart import BarChart, Reference

    yol = str(tmp_path / "diagramma.xlsx")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Смета"
    ws.append(["T/R", "Xarajat", "Jami"])
    ws.append([1, "Beton", 1250000])
    cs = wb.create_chartsheet("Diagramma")     # aynan shu varaq yiqitardi
    # Diagramma haqiqiy bo'lishi kerak: bo'sh chartsheet ni openpyxl ning
    # O'ZI qayta o'qiy olmaydi (rels yo'q) — bu bizning nuqsonimiz emas.
    diag = BarChart()
    diag.add_data(Reference(ws, min_col=3, min_row=1, max_row=2), titles_from_data=True)
    cs.add_chart(diag)
    wb.save(yol)

    varaqlar = reader.read_file(yol)
    # Diagrammada katak yo'q — u natijaga KIRMAYDI, lekin hujjat ochiladi.
    assert list(varaqlar) == ["Смета"]
    assert 1250000 in _sonlar(varaqlar)
