# -*- coding: utf-8 -*-
"""Xom XML zaxira yo'li — yetishmagan OOXML qismlarini sintez qilish.

2026-09-04 dagi tekshiruvda «Faylni ochib bo'lmadi» deb RAD etilgan 92 ta
hujjatdan 46 tasi shu sababdan yiqilgan edi: zip qobig'i SOG'LOM, varaq XML i
JOYIDA, lekin `xl/workbook.xml` (46/46), `xl/sharedStrings.xml` (44/46) va
bir faylda `xl/styles.xml` ham yo'q — openpyxl KeyError beradi.

Bu testlar to'rt narsani qotiradi:
  1. Yetishmagan qismlar sintez qilinsa fayl O'QILADI va narxlar ko'rinadi.
  2. Tiklash mazmunni BUZMAYDI — nazorat tajribasi: sog'lom fayldan qism
     o'chirib tiklaganda katak-katak AYNAN o'sha jadval qaytadi.
  3. Sog'lom fayl bu yo'lga UMUMAN kirmaydi (`kerakmi` False, `tikla`
     rad etadi) — ya'ni ishlaydigan oqim o'zgarmaydi.
  4. Tiklash yiqilsa ASL xato qaytadi va budjet/o'chirish kaliti ishlaydi —
     ishtirokchiga ko'rinadigan izoh hech qachon yomonlashmaydi.
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
from tender_engine import tiklash_xom_xml as T

# Haqiqiy buzuq fayllar to'plami. Repozitoriyda yo'q — bo'lmasa test
# o'tkazib yuboriladi (CI da yiqilmasin).
BUZUQ_DIR = os.environ.get(
    "XOM_XML_BUZUQ_DIR",
    r"C:\Users\User\AppData\Local\Temp\claude"
    r"\c--Users-User-Downloads-Telegram-Desktop-tender-reject"
    r"\8f27e0d5-5d9b-464e-8df2-f3df5ba17509\scratchpad\buzuq_fayllar")

# Katta fayllarni to'liq (merge ochib) o'qish sekin — butun to'plam
# sekinlashmasin uchun ular oqim rejimida, birinchi son topilishi bilan
# tekshiriladi. `XOM_XML_TEST_TOLIQ=1` bilan hammasi to'liq o'qiladi.
KATTA_BAYT = 400_000
TOLIQ = os.environ.get("XOM_XML_TEST_TOLIQ", "0") not in ("0", "", "no")


# ── Yordamchilar ────────────────────────────────────────────────────────────
def _sonlar(varaqlar):
    """Jadvaldagi barcha sonlar (mantiqiy qiymatlar son emas)."""
    return [v for q in varaqlar.values() for r in q for v in r
            if isinstance(v, (int, float)) and not isinstance(v, bool)]


def _birinchi_son_bormi(yol):
    """Faylda kamida bitta son bormi — oqim rejimida, topilishi bilan to'xtaydi."""
    wb = openpyxl.load_workbook(yol, data_only=True, read_only=True)
    try:
        for ws in wb.worksheets:
            for qator in ws.iter_rows(values_only=True):
                for v in qator:
                    if isinstance(v, (int, float)) and not isinstance(v, bool):
                        return True
        return False
    finally:
        wb.close()


def _sha256(yol):
    h = hashlib.sha256()
    with open(yol, "rb") as f:
        for blok in iter(lambda: f.read(1 << 16), b""):
            h.update(blok)
    return h.hexdigest()


def _qismsiz(manba, nishon, ochir):
    """Manbaning nusxasini yasaydi, `ochir` dagi qismlarsiz."""
    with zipfile.ZipFile(manba) as z, \
            zipfile.ZipFile(nishon, "w", zipfile.ZIP_DEFLATED) as o:
        for nom in z.namelist():
            if nom not in ochir:
                o.writestr(nom, z.read(nom))
    return nishon


def _app_xml_qoy(yol, varaq_nomlari):
    """Faylga Excel uslubidagi `docProps/app.xml` qo'yadi.

    openpyxl `TitlesOfParts` yozmaydi, haqiqiy Excel esa yozadi — varaq
    nomlarini tiklash aynan shu ro'yxatga tayanadi. Fikstura haqiqiy
    fayllarga o'xshashi uchun qo'lda qo'yamiz. Oxiriga «nomlangan
    diapazon» ham qo'shiladi: kod faqat BIRINCHI N tasini olishi shart.
    """
    from xml.sax.saxutils import escape
    lp = "".join(f"<vt:lpstr>{escape(n)}</vt:lpstr>" for n in varaq_nomlari)
    app = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument'
        '/2006/extended-properties" xmlns:vt="http://schemas.openxmlformats.org'
        '/officeDocument/2006/docPropsVTypes"><Application>Microsoft Excel'
        '</Application><HeadingPairs><vt:vector size="2" baseType="variant">'
        '<vt:variant><vt:lpstr>Листы</vt:lpstr></vt:variant><vt:variant>'
        f'<vt:i4>{len(varaq_nomlari)}</vt:i4></vt:variant></vt:vector>'
        '</HeadingPairs><TitlesOfParts>'
        f'<vt:vector size="{len(varaq_nomlari) + 1}" baseType="lpstr">{lp}'
        "<vt:lpstr>Область_печати</vt:lpstr></vt:vector></TitlesOfParts>"
        "</Properties>").encode("utf-8")
    vaqtinchalik = yol + ".tmp"
    with zipfile.ZipFile(yol) as z, \
            zipfile.ZipFile(vaqtinchalik, "w", zipfile.ZIP_DEFLATED) as o:
        for nom in z.namelist():
            o.writestr(nom, app if nom == "docProps/app.xml" else z.read(nom))
        if "docProps/app.xml" not in z.namelist():
            o.writestr("docProps/app.xml", app)
    os.replace(vaqtinchalik, yol)
    return yol


@pytest.fixture
def soglom(tmp_path):
    """Ko'p varaqli, merge kataklari va shartli formatlashi bor sog'lom .xlsx.

    Shartli formatlash ATAYLAB qo'shilgan: u `styles.xml` dagi `dxfs`
    ro'yxatiga `dxfId` bilan murojaat qiladi va nazorat tajribasida
    aynan shu `IndexError: list index out of range` bilan yiqilgan edi
    (sintez qilingan uslublarda `dxfs` bo'lmagani uchun).
    """
    from openpyxl.formatting.rule import CellIsRule
    from openpyxl.styles import PatternFill

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Смета "                      # oxirida PROBEL — ataylab
    ws["A1"] = "Наименование работ и затрат"
    ws["B1"] = "Цена"
    for i in range(2, 40):
        ws.cell(i, 1, f"иш тури {i}")
        ws.cell(i, 2, 150000 + i * 37)
    ws.merge_cells("A41:B41")
    ws["A41"] = "ИТОГО"
    # Bo'yoqli qoida `styles.xml` da `<dxfs>` yozuvini tug'diradi va varaqda
    # `dxfId` bo'lib qoladi — sintez qilingan uslublarda `dxfs` bo'lmasa
    # openpyxl `IndexError: list index out of range` bilan yiqiladi.
    ws.conditional_formatting.add(
        "B2:B40", CellIsRule(operator="greaterThan", formula=["1000"],
                             fill=PatternFill(start_color="FFC7CE",
                                              end_color="FFC7CE",
                                              fill_type="solid")))
    ikki = wb.create_sheet("Жами")
    ikki["A1"] = "Жами қиймати"
    ikki["B1"] = 987654.5
    yol = str(tmp_path / "soglom.xlsx")
    wb.save(yol)
    wb.close()
    return _app_xml_qoy(yol, ["Смета ", "Жами"])


@pytest.fixture(autouse=True)
def _ogohlantirishlarni_yut():
    """Tiklash muvaffaqiyatda `warnings.warn` chiqaradi — test loglarini bosmasin."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        yield


# ── 1. Haqiqiy buzuq fayllar ────────────────────────────────────────────────
def _haqiqiy_fayllar():
    if not os.path.isdir(BUZUQ_DIR):
        return []
    natija = []
    for nom in sorted(os.listdir(BUZUQ_DIR)):
        if nom.startswith("~$") or os.path.splitext(nom)[1].lower() not in (
                ".xlsx", ".xls"):
            continue
        yol = os.path.join(BUZUQ_DIR, nom)
        # Manifest KERAK EMAS: modulning o'zi «menga tegishli» deganlarini
        # olamiz — zip sog'lom, varaq bor, muhim qism yo'q.
        if T.yetishmagan_qismlar(yol):
            natija.append(yol)
    return natija


HAQIQIY = _haqiqiy_fayllar()


@pytest.mark.skipif(not HAQIQIY, reason=f"buzuq fayllar to'plami yo'q: {BUZUQ_DIR}")
@pytest.mark.parametrize("yol", HAQIQIY, ids=lambda p: os.path.basename(p))
def test_haqiqiy_fayl_tiklanadi(yol, monkeypatch):
    """Har bir haqiqiy fayl: tiklashsiz KeyError → zanjir bilan o'qiladi.

    `TIKLASH_YOQ=0` zaxira zanjirini o'chiradi, ya'ni asl nuqsonni ko'rsatadi;
    kalit qaytarilgach o'sha fayl `read_file` orqali ochilishi kerak.
    """
    monkeypatch.setenv("TIKLASH_YOQ", "0")
    with pytest.raises(KeyError):
        reader.read_file(yol)
    monkeypatch.delenv("TIKLASH_YOQ")
    assert T.kerakmi(yol, KeyError("There is no item named 'xl/workbook.xml'"))

    nusxa = T.tikla(yol)
    try:
        assert os.path.exists(nusxa)
        with zipfile.ZipFile(nusxa) as z:
            nomlar = set(z.namelist())
        assert "xl/workbook.xml" in nomlar

        if TOLIQ or os.path.getsize(yol) <= KATTA_BAYT:
            varaqlar = reader.read_file(nusxa)
            assert varaqlar, "tiklangan faylda varaq yo'q"
            # Narxlar sheetN.xml ichida yotadi — sharedStrings yo'qligi
            # ularga TA'SIR QILMAYDI (nazorat tajribasi bilan isbotlangan).
            assert len(_sonlar(varaqlar)) > 0
        else:
            assert _birinchi_son_bormi(nusxa)
    finally:
        T.tozala(nusxa)


@pytest.mark.skipif(not HAQIQIY, reason="buzuq fayllar to'plami yo'q")
def test_haqiqiy_toplam_hajmi():
    """To'plamda kutilgan 46 ta fayl bor — sinov qamrovi jimgina qisqarmasin."""
    assert len(HAQIQIY) >= 46, f"faqat {len(HAQIQIY)} ta fayl topildi"


# ── 2. Nazorat tajribasi: tiklash mazmunni buzmaydi ─────────────────────────
def test_workbook_ochirilsa_mazmun_aynan_qaytadi(soglom, tmp_path, monkeypatch):
    """`workbook.xml` yo'q → tiklangach katak-katak AYNAN o'sha jadval."""
    asl = reader.read_file(soglom)
    buzuq = _qismsiz(soglom, str(tmp_path / "b.xlsx"), {"xl/workbook.xml"})

    monkeypatch.setenv("TIKLASH_YOQ", "0")     # zanjirsiz — asl nuqson ko'rinadi
    with pytest.raises(KeyError):
        reader.read_file(buzuq)
    monkeypatch.delenv("TIKLASH_YOQ")

    tiklangan = T.tiklab_oq(buzuq, reader.read_file)
    # Varaq NOMLARI ham, TARTIBI ham, mazmuni ham teng bo'lishi shart:
    # nom etalon bilan varaq juftlashda va izoh matnida ishlatiladi.
    assert list(tiklangan) == list(asl)
    assert tiklangan == asl


def test_varaq_nomi_oxiridagi_probel_saqlanadi(soglom, tmp_path):
    """«Смета » — oxiridagi probel KESILMASIN (juftlash nom tengligiga tayanadi)."""
    buzuq = _qismsiz(soglom, str(tmp_path / "b.xlsx"), {"xl/workbook.xml"})
    tiklangan = T.tiklab_oq(buzuq, reader.read_file)
    assert "Смета " in tiklangan


def _sst_li_fayllar():
    """Loyihaning `data/` papkasidagi HAQIQIY, sharedStrings ishlatadigan fayllar.

    Fikstura bu yerda yaramaydi: openpyxl matnni `inlineStr` bilan yozadi,
    ya'ni `sharedStrings.xml` umuman bo'lmaydi. Lug'atning yo'qolishi
    narxlarga ta'sir qilmasligini FAQAT haqiqiy Excel fayllarida sinash
    mumkin.
    """
    kok = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    topildi = []
    for papka in ("data", "data_new"):
        for r, _, fs in os.walk(os.path.join(kok, papka)):
            for f in sorted(fs):
                if not f.endswith(".xlsx") or f.startswith("~$"):
                    continue
                yol = os.path.join(r, f)
                try:
                    with zipfile.ZipFile(yol) as z:
                        if "xl/sharedStrings.xml" in z.namelist():
                            topildi.append(yol)
                except (zipfile.BadZipFile, OSError):
                    continue
        if len(topildi) >= 3:
            break
    return topildi[:3]


SST_FAYLLAR = _sst_li_fayllar()


@pytest.mark.skipif(not SST_FAYLLAR, reason="data/ da sharedStrings li .xlsx yo'q")
@pytest.mark.parametrize("yol", SST_FAYLLAR, ids=lambda p: os.path.basename(p))
def test_sharedstrings_ochirilsa_sonlar_saqlanadi(yol, tmp_path, monkeypatch):
    """Matn lug'ati yo'qolsa ham NARXLAR to'liq qaytadi — modulning ASOSIY da'vosi.

    Nazorat tajribasi: haqiqiy fayldan `sharedStrings.xml` (va `workbook.xml`)
    o'chiriladi, tiklanadi va chiqarilgan SONLAR to'plami aslidagi bilan
    AYNAN solishtiriladi. Sonlar `sheetN.xml` ichida `<v>` bo'lib yotadi,
    lug'at esa faqat MATN uchun.
    """
    asl = reader.read_file(yol)
    buzuq = _qismsiz(yol, str(tmp_path / "b.xlsx"),
                     {"xl/workbook.xml", "xl/sharedStrings.xml"})
    monkeypatch.setenv("TIKLASH_YOQ", "0")     # zanjirsiz — asl nuqson ko'rinadi
    with pytest.raises(KeyError):
        reader.read_file(buzuq)
    monkeypatch.delenv("TIKLASH_YOQ")

    tiklangan = T.tiklab_oq(buzuq, reader.read_file)
    assert len(_sonlar(asl)) > 0
    assert sorted(_sonlar(tiklangan)) == sorted(_sonlar(asl))
    # Varaq nomlari ham `app.xml` dan qaytadi (haqiqiy Excel fayli).
    assert list(tiklangan) == list(asl)


def test_styles_ochirilsa_ham_ochiladi(soglom, tmp_path):
    """`styles.xml` + `dxfs` yo'q bo'lsa ham o'qiladi (IndexError regressiyasi)."""
    asl = reader.read_file(soglom)
    buzuq = _qismsiz(soglom, str(tmp_path / "b.xlsx"),
                     {"xl/workbook.xml", "xl/sharedStrings.xml", "xl/styles.xml"})
    tiklangan = T.tiklab_oq(buzuq, reader.read_file)
    assert sorted(_sonlar(tiklangan)) == sorted(_sonlar(asl))


def test_asl_fayl_ozgarmaydi(soglom, tmp_path):
    """Tiklash asl faylga TEGMAYDI — bayt-ba-bayt o'sha fayl qoladi."""
    buzuq = _qismsiz(soglom, str(tmp_path / "b.xlsx"), {"xl/workbook.xml"})
    oldin = _sha256(buzuq)
    nusxa = T.tikla(buzuq)
    try:
        assert os.path.dirname(os.path.abspath(nusxa)) != str(tmp_path)
        assert _sha256(buzuq) == oldin
    finally:
        T.tozala(nusxa)
    assert _sha256(buzuq) == oldin


# ── 3. Sog'lom fayl bu yo'lga kirmaydi ──────────────────────────────────────
def test_soglom_faylga_tegilmaydi(soglom):
    """1-xavfsizlik sharti: ishlaydigan fayl uchun tiklash ISHGA TUSHMAYDI."""
    assert T.yetishmagan_qismlar(soglom) == []
    assert T.kerakmi(soglom, KeyError("x")) is False
    with pytest.raises(T.TiklashKerakEmas):
        T.tikla(soglom)


def test_boshqa_xatolar_bu_modulniki_emas(soglom, tmp_path):
    """KeyError bo'lmagan xatoda modul aralashmaydi (boshqa tiklash yo'llari bor)."""
    buzuq = _qismsiz(soglom, str(tmp_path / "b.xlsx"), {"xl/workbook.xml"})
    assert T.kerakmi(buzuq, KeyError("yo'q")) is True
    assert T.kerakmi(buzuq, zipfile.BadZipFile("buzuq")) is False
    assert T.kerakmi(buzuq, ValueError("boshqa")) is False


def test_zip_bolmagan_fayl(tmp_path):
    """Zip emas (yoki .xls) — bo'sh ro'yxat, hech qanday urinish yo'q."""
    yol = str(tmp_path / "matn.xlsx")
    with open(yol, "wb") as f:
        f.write(b"\xd0\xcf\x11\xe0 bu OLE2, zip emas")
    assert T.yetishmagan_qismlar(yol) == []
    assert T.kerakmi(yol, KeyError("x")) is False


# ── 4. Xato yo'llari, budjet, o'chirish kaliti ──────────────────────────────
def test_asl_xato_qayta_kotariladi(soglom, tmp_path):
    """5-xavfsizlik sharti: tiklash yiqilsa ISHTIROKCHI ko'radigan xato o'zgarmaydi."""
    buzuq = _qismsiz(soglom, str(tmp_path / "b.xlsx"), {"xl/workbook.xml"})
    asl = KeyError("There is no item named 'xl/workbook.xml' in the archive")

    def _yiqiladigan_oquvchi(_yol):
        raise RuntimeError("tiklangan faylda ham boshqa muammo")

    with pytest.raises(KeyError) as x:
        T.tiklab_oq(buzuq, _yiqiladigan_oquvchi, asl)
    assert x.value is asl

    # Tiklashning O'ZI yiqilsa ham (bu yerda: fayl mos emas) xuddi shunday.
    with pytest.raises(KeyError) as x2:
        T.tiklab_oq(soglom, reader.read_file, asl)
    assert x2.value is asl


def test_tiklab_oq_vaqtinchalik_faylni_tozalaydi(soglom, tmp_path):
    """Nusxa har holda o'chadi — diskda axlat qolmasin."""
    buzuq = _qismsiz(soglom, str(tmp_path / "b.xlsx"), {"xl/workbook.xml"})
    korilgan = {}

    def _oquvchi(yol):
        korilgan["yol"] = yol
        return reader.read_file(yol)

    T.tiklab_oq(buzuq, _oquvchi)
    assert not os.path.exists(korilgan["yol"])
    assert not os.path.exists(os.path.dirname(korilgan["yol"]))


def test_ochirish_kaliti(soglom, tmp_path, monkeypatch):
    """7-xavfsizlik sharti: TIKLASH_YOQ=0 / TIKLASH_XOM_XML_YOQ=0."""
    buzuq = _qismsiz(soglom, str(tmp_path / "b.xlsx"), {"xl/workbook.xml"})
    assert T.kerakmi(buzuq, KeyError("x")) is True        # standart — YOQIQ

    for kalit in ("TIKLASH_YOQ", "TIKLASH_XOM_XML_YOQ"):
        monkeypatch.setenv(kalit, "0")
        assert T.kerakmi(buzuq, KeyError("x")) is False
        with pytest.raises(T.TiklashOchirilgan):
            T.tikla(buzuq)
        monkeypatch.delenv(kalit)


def test_vaqt_budjeti(soglom, tmp_path):
    """4-xavfsizlik sharti: vaqt budjeti tugasa TOZA istisno (livelock bo'lmasin)."""
    buzuq = _qismsiz(soglom, str(tmp_path / "b.xlsx"), {"xl/workbook.xml"})
    with pytest.raises(T.TiklashBudjeti):
        T.tikla(buzuq, budjet=T._Budjet(sek=-1))


def test_hajm_budjeti(soglom, tmp_path):
    """Ochilgan bayt budjeti — ta'mir orqali zip bomba o'tmasin."""
    buzuq = _qismsiz(soglom, str(tmp_path / "b.xlsx"), {"xl/workbook.xml"})
    with pytest.raises(T.TiklashBudjeti):
        T.tikla(buzuq, budjet=T._Budjet(jami_bayt=64))


def test_qism_soni_budjeti(soglom, tmp_path, monkeypatch):
    """Zip a'zolari soni chegarasi."""
    buzuq = _qismsiz(soglom, str(tmp_path / "b.xlsx"), {"xl/workbook.xml"})
    monkeypatch.setattr(T, "MAX_QISM", 2)
    with pytest.raises(T.TiklashBudjeti):
        T.tikla(buzuq)


def test_katta_qism_rad_etiladi(soglom, tmp_path, monkeypatch):
    """Bitta qism ochilganda chegaradan katta bo'lsa — to'xtaymiz."""
    buzuq = _qismsiz(soglom, str(tmp_path / "b.xlsx"), {"xl/workbook.xml"})
    monkeypatch.setattr(T, "MAX_QISM_BAYT", 16)
    with pytest.raises(T.TiklashBudjeti):
        T.tikla(buzuq)


def test_sst_va_xf_chegaralari(monkeypatch):
    """Sintez qilinadigan jadvallar cheksiz o'smaydi."""
    monkeypatch.setattr(T, "MAX_SST", 10)
    with pytest.raises(T.TiklashBudjeti):
        T._sst_yasa(11)
    monkeypatch.setattr(T, "MAX_XF", 10)
    with pytest.raises(T.TiklashBudjeti):
        T._styles_yasa(11)


# ── 5. Sintez tafsilotlari ──────────────────────────────────────────────────
def test_varaqlar_raqam_boyicha_tartiblanadi():
    """`sheet10` `sheet2` dan KEYIN kelsin — alifbo tartibi varaqlarni almashtirardi."""
    nomlar = ["xl/worksheets/sheet10.xml", "xl/worksheets/sheet2.xml",
              "xl/worksheets/sheet1.xml", "xl/worksheets/_rels/sheet1.xml.rels"]
    assert T._varaq_qismlari(nomlar) == ["xl/worksheets/sheet1.xml",
                                         "xl/worksheets/sheet2.xml",
                                         "xl/worksheets/sheet10.xml"]


def test_varaq_tartibi_rid_boyicha():
    """Varaqlar `rId` raqami bo'yicha tartiblanadi, `.rels` YOZILISH tartibida emas.

    Haqiqiy fayllarda munosabatlar aralash yoziladi (`rId3`, `rId2`, `rId1`).
    Yozilish tartibiga tayansak varaqlar joyini almashtirardi — `app.xml`
    dagi nomlar esa yorliq (rId) tartibida keladi, ya'ni nom boshqa
    jadvalga yopishib qolardi.
    """
    rels = (b'<?xml version="1.0"?><Relationships xmlns="http://schemas.'
            b'openxmlformats.org/package/2006/relationships">'
            b'<Relationship Id="rId3" Type="http://schemas.openxmlformats.org'
            b'/officeDocument/2006/relationships/worksheet" '
            b'Target="worksheets/sheet3.xml"/>'
            b'<Relationship Id="rId1" Type="http://schemas.openxmlformats.org'
            b'/officeDocument/2006/relationships/worksheet" '
            b'Target="worksheets/sheet1.xml"/>'
            b'<Relationship Id="rId2" Type="http://schemas.openxmlformats.org'
            b'/officeDocument/2006/relationships/worksheet" '
            b'Target="/xl/worksheets/sheet2.xml"/>'
            b"</Relationships>")
    varaqlar = ["xl/worksheets/sheet1.xml", "xl/worksheets/sheet2.xml",
                "xl/worksheets/sheet3.xml"]
    _yangi, juftlar = T._rels_yangila(rels, varaqlar, False, False)
    assert [rid for rid, _ in juftlar] == ["rId1", "rId2", "rId3"]
    assert [v for _, v in juftlar] == varaqlar
    # Hamma varaq allaqachon ro'yxatda — rels QAYTA YOZILMAYDI.
    assert _yangi is None

    # Ro'yxatdan tushib qolgan varaq qo'shiladi (yangi rId bilan).
    yangi_rels, juftlar2 = T._rels_yangila(
        rels, varaqlar + ["xl/worksheets/sheet4.xml"], False, False)
    assert yangi_rels is not None
    assert b"worksheets/sheet4.xml" in yangi_rels
    assert len(juftlar2) == 4


def test_nomlar_ishonchsiz_bolsa_sheetN(soglom, tmp_path):
    """`app.xml` nomlari takrorlansa — barqaror «Sheet1…» ga qaytamiz.

    Yarmi haqiqiy, yarmi sintetik nom eng yomon holat: izohda ham,
    varaq juftlashda ham chalg'itadi.
    """
    nishon = str(tmp_path / "b.xlsx")
    with zipfile.ZipFile(soglom) as z, \
            zipfile.ZipFile(nishon, "w", zipfile.ZIP_DEFLATED) as o:
        for nom in z.namelist():
            if nom == "xl/workbook.xml":
                continue
            mazmun = z.read(nom)
            if nom == "docProps/app.xml":
                mazmun = mazmun.replace(b"<vt:lpstr>\xd0\x96\xd0\xb0\xd0\xbc\xd0\xb8"
                                        b"</vt:lpstr>",
                                        b"<vt:lpstr>[yaroqsiz]</vt:lpstr>")
            o.writestr(nom, mazmun)
    tiklangan = T.tiklab_oq(nishon, reader.read_file)
    assert list(tiklangan) == ["Sheet1", "Sheet2"]


def test_styles_kengaytiriladi_almashtirilmaydi():
    """Mavjud styles.xml SAQLANADI, faqat yetishmagan `xf` lar qo'shiladi.

    Almashtirilsa son formatlari (sana, valyuta) yo'qolib, sanalar xom
    songa aylanardi va to'ldirilganlik hisobi buzilardi.
    """
    xom = (b'<styleSheet><numFmts count="1"><numFmt numFmtId="164" '
           b'formatCode="dd.mm.yyyy"/></numFmts>'
           b'<cellXfs count="2"><xf numFmtId="164"/><xf numFmtId="0"/></cellXfs>'
           b'</styleSheet>')
    keng = T._styles_kengaytir(xom, 5)
    assert b'formatCode="dd.mm.yyyy"' in keng     # asl format joyida
    assert b'<cellXfs count="5">' in keng
    assert keng.count(b"<xf ") == 5
    # Yetarli bo'lsa TEGILMAYDI.
    assert T._styles_kengaytir(xom, 2) is None


def test_nusxada_zipinfo_qayta_ishlatilmaydi(soglom, tmp_path):
    """A'zolar YANGIDAN yoziladi — asl `ZipInfo` (flag_bits) ko'chirilmaydi.

    Prototipda ilk urinish AYNAN shundan yiqilgan edi: ma'lumot
    deskriptori biti ko'chirilganda o'qishda «Bad magic number for file
    header» chiqadi. Shuning uchun nusxada shu bit bo'lmasligi va
    arxivning butunligi tekshiriladi.
    """
    buzuq = _qismsiz(soglom, str(tmp_path / "b.xlsx"), {"xl/workbook.xml"})
    nusxa = T.tikla(buzuq)
    try:
        with zipfile.ZipFile(nusxa) as z:
            assert z.testzip() is None
            for info in z.infolist():
                assert not info.flag_bits & 0x08, info.filename
                assert z.read(info.filename) is not None
    finally:
        T.tozala(nusxa)


def test_tozala_xatoga_chidamli():
    """`tozala` yo'q papkada ham yiqilmaydi (finally blokida chaqiriladi)."""
    T.tozala(None)
    T.tozala(os.path.join(os.sep, "yoq-papka", "yoq.xlsx"))


def test_tikla_xatoda_papka_qoldirmaydi(soglom, tmp_path, monkeypatch):
    """Sintez o'rtasida yiqilsa vaqtinchalik papka o'chadi."""
    import tempfile
    buzuq = _qismsiz(soglom, str(tmp_path / "b.xlsx"), {"xl/workbook.xml"})
    yasalgan = []
    asl_mkdtemp = tempfile.mkdtemp

    def _kuzatuvchi(*a, **kw):
        yol = asl_mkdtemp(*a, **kw)
        yasalgan.append(yol)
        return yol

    monkeypatch.setattr(T.tempfile, "mkdtemp", _kuzatuvchi)
    monkeypatch.setattr(T, "MAX_QISM", 1)
    with pytest.raises(T.TiklashBudjeti):
        T.tikla(buzuq)
    assert yasalgan and not os.path.exists(yasalgan[0])


def test_reader_import_qilinmaydi():
    """Modul MUSTAQIL: `reader` ni import qilmaydi (aylanma import bo'lmasin).

    `reader` bu modulni chaqiradi — teskari bog'liqlik paydo bo'lsa
    ikkalasi ham yuklanmay qolardi.
    """
    yol = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "tender_engine", "tiklash_xom_xml.py")
    with open(yol, encoding="utf-8") as f:
        matn = f.read()
    assert "import reader" not in matn
    assert "from tender_engine" not in matn
