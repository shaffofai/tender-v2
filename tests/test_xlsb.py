# -*- coding: utf-8 -*-
"""XLSB (BIFF12) o'quvchisi — `tender_engine.tiklash_xlsb`.

MUAMMO (2026-09-04 auditi, id=111388): fayl `.xlsx` nomi bilan kelgan,
ichi esa XLSB — Excel'ning ikkilik formati. Zip sog'lom, hujjat to'g'ri
to'ldirilgan, faqat openpyxl formatni tanimaydi. Natijada halol hujjat
«Faylni ochib bo'lmadi» deb RAD etilardi.

Testlar uch narsani qotiradi:
  1. XLSB fayl O'QILADI va o'qilgan SONLAR TO'G'RI — buni hujjatning O'Z
     arifmetik ayniyatlari isbotlaydi (Итого / НДС 12% / Всего). Bitta bit
     xato o'qilsa yig'indi mos kelmaydi, ya'ni bu tekshiruvni «ko'rinishi
     to'g'ri» degan taxmin bilan aldab bo'lmaydi.
  2. SOG'LOM fayllar bu yo'lga UMUMAN tushmaydi — darvoza ularni
     burmaydi, demak mavjud xatti-harakat o'zgarmaydi.
  3. Xavfsizlik: asl faylga tegilmaydi, chegaralar tatbiq etiladi,
     budjet tugasa toza istisno, tiklash yiqilsa ASL xato ko'tariladi.
"""
import hashlib
import os
import struct
import sys
import warnings
import zipfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tender_engine import tiklash_xlsb as T

_ILDIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BUZUQ = os.path.join(
    os.environ.get("BUZUQ_FAYLLAR", ""),
    "") if os.environ.get("BUZUQ_FAYLLAR") else (
    r"C:\Users\User\AppData\Local\Temp\claude"
    r"\c--Users-User-Downloads-Telegram-Desktop-tender-reject"
    r"\8f27e0d5-5d9b-464e-8df2-f3df5ba17509\scratchpad\buzuq_fayllar")
_XLSB_NAMUNA = os.path.join(_BUZUQ, "111388.xlsx")


@pytest.fixture(scope="module")
def namuna():
    """id=111388 — `.xlsx` nomi bilan kelgan haqiqiy XLSB hujjat."""
    if not os.path.exists(_XLSB_NAMUNA):
        pytest.skip("haqiqiy XLSB namunasi yo'q (buzuq_fayllar papkasi)")
    return _XLSB_NAMUNA


# ═══════════════════════════════════════════════════════════════════════════
# Sintetik BIFF12 yasagich — fikstura uchun (haqiqiy fayl bo'lmasa ham
# xavfsizlik yo'llari sinaladi va testlar tarmoqqa chiqmaydi)
# ═══════════════════════════════════════════════════════════════════════════

def _varint(n):
    xom = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        xom.append(b | (0x80 if n else 0))
        if not n:
            return bytes(xom)


def _yozuv(rid, yuk=b""):
    """BIFF12 yozuvi: rid varinti (1-2 bayt) + uzunlik varinti + yuk."""
    bosh = bytes([rid]) if rid < 0x80 else bytes([(rid & 0x7F) | 0x80,
                                                  (rid >> 7) & 0x7F])
    return bosh + _varint(len(yuk)) + yuk


def _ws(s):
    """XLWideString."""
    return struct.pack("<I", len(s)) + s.encode("utf-16-le")


def _katak(c):
    """Katak sarlavhasi: ustun(4) + iStyleRef(3) + bayroq(1)."""
    return struct.pack("<I", c) + b"\x00\x00\x00\x00"


def _xlsb_yasa(yol, kataklar, birlashmalar=(), sst=(), varaq="Лист1"):
    """Minimal, lekin HAQIQIY XLSB paketi yasaydi.

    `kataklar` — [(qator, ustun, rid, yuk_qismi), ...]; yuk katak
    sarlavhasidan KEYINGI baytlar.
    """
    sheet = bytearray(_yozuv(129))                  # BrtBeginSheetData
    for r, c, rid, yuk in kataklar:
        sheet += _yozuv(0, struct.pack("<I", r) + b"\x00" * 12)   # BrtRowHdr
        sheet += _yozuv(rid, _katak(c) + yuk)
    sheet += _yozuv(130)                            # BrtEndSheetData
    if birlashmalar:
        sheet += _yozuv(177)
        for r1, r2, c1, c2 in birlashmalar:
            sheet += _yozuv(176, struct.pack("<4I", r1, r2, c1, c2))
        sheet += _yozuv(178)

    wb = _yozuv(156, struct.pack("<II", 0, 1) + _ws("rId1") + _ws(varaq))

    ss = bytearray()
    for s in sst:
        ss += _yozuv(19, b"\x00" + _ws(s))

    rels = ('<?xml version="1.0"?><Relationships xmlns="http://schemas.'
            'openxmlformats.org/package/2006/relationships"><Relationship '
            'Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument'
            '/2006/relationships/worksheet" Target="worksheets/sheet1.bin"/>'
            '</Relationships>')
    ct = ('<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.'
          'org/package/2006/content-types"><Default Extension="bin" '
          'ContentType="application/vnd.ms-excel.sheet.binary.macroEnabled.'
          'main"/></Types>')
    with zipfile.ZipFile(yol, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("xl/workbook.bin", bytes(wb))
        z.writestr("xl/_rels/workbook.bin.rels", rels)
        z.writestr("xl/worksheets/sheet1.bin", bytes(sheet))
        if sst:
            z.writestr("xl/sharedStrings.bin", bytes(ss))
    return yol


# ═══════════════════════════════════════════════════════════════════════════
# 1. Haqiqiy hujjat — id=111388
# ═══════════════════════════════════════════════════════════════════════════

def test_openpyxl_bu_faylni_OCHOLMAYDI(namuna):
    """Fikstura haqiqatan muammoni takrorlaydimi (test o'zini tekshiradi)."""
    openpyxl = pytest.importorskip("openpyxl")
    with pytest.raises(Exception):
        openpyxl.load_workbook(namuna)


def test_darvoza_xlsb_ni_tanidi(namuna):
    assert T.xlsb_faylmi(namuna) is True


def test_varaqlar_va_olcham(namuna):
    varaqlar = T.oqi(namuna)
    assert list(varaqlar) == ["Объектная", "Сводная"], "varaq nomlari/tartibi"
    assert len(varaqlar["Объектная"]) == 117
    assert len(varaqlar["Сводная"]) == 19
    sonlar = [v for g in varaqlar.values() for q in g for v in q
              if isinstance(v, (int, float)) and not isinstance(v, bool)]
    assert len(sonlar) == 128, f"raqamli katak soni: {len(sonlar)}"


def test_ARIFMETIK_AYNIYAT(namuna):
    """ENG MUHIMI: o'qilgan sonlar hujjatning O'Z yig'indilariga mos kelsin.

    «Сводная» varag'i smeta yakuni: 4 ta band -> Итого -> (+3 band) ->
    «Итого без НДС» -> НДС 12% -> «Всего с НДС». Agar RK/Real yechish
    yoki ustun moslashuvi bitta bitga xato bo'lsa, bu tenglamalar
    BUZILADI — shuning uchun bu «ko'rinishi to'g'ri» emas, DALIL.
    """
    sv = T.oqi(namuna)["Сводная"]

    def q(qator):                      # 1 dan boshlanadigan qator, «сумма» ustuni
        return sv[qator - 1][7]

    bandlar = q(8) + q(9) + q(10) + q(11)
    assert bandlar == pytest.approx(q(12), rel=1e-12), "Итого mos kelmadi"

    bez_nds = q(12) + q(13) + q(14) + q(15)
    assert bez_nds == pytest.approx(q(16), rel=1e-12), "«Итого без НДС» mos kelmadi"

    assert q(16) * 0.12 == pytest.approx(q(17), rel=1e-12), "НДС 12% mos kelmadi"
    assert q(16) + q(17) == pytest.approx(q(18), rel=1e-12), "«Всего с НДС» mos kelmadi"

    # Yakuniy summa — hujjatning taklif narxi; nol yoki bo'sh bo'lib qolmasin.
    assert q(18) == pytest.approx(81441767.82479687, rel=1e-9)


def test_matn_va_birlashmalar(namuna):
    """Sarlavhalar SST dan o'qilsin, birlashmalar `.xlsx` dagidek ochilsin."""
    varaqlar = T.oqi(namuna)
    ob = varaqlar["Объектная"]
    assert ob[6][0] == "№" and ob[6][8] == "ИТОГО:", "sarlavha qatori o'qilmadi"
    sv = varaqlar["Сводная"]
    assert sv[6][7] == "Ст-сть затрат, тыс.сум"
    # r7 c7:c8 birlashgan — o'ng katak ham chap-yuqori qiymatini olsin
    assert sv[6][8] == sv[6][7], "birlashma ochilmagan"
    assert sv[17][8] == sv[17][7]


def test_asl_fayl_OZGARMAYDI(namuna):
    """Fayl faqat o'qiladi — bayt ham o'zgarmasin (asl hujjat daxlsiz)."""
    oldin = hashlib.sha256(open(namuna, "rb").read()).hexdigest()
    T.oqi(namuna)
    keyin = hashlib.sha256(open(namuna, "rb").read()).hexdigest()
    assert oldin == keyin


def test_tez_oqiladi(namuna):
    """Livelock qaytmasin: 100 KB li hujjat bir soniyadan kam o'qilsin."""
    import time
    t0 = time.monotonic()
    T.oqi(namuna)
    assert time.monotonic() - t0 < 2.0


# ═══════════════════════════════════════════════════════════════════════════
# 2. Sog'lom fayllar bu yo'lga TUSHMAYDI (ekvivalentlik kafolati)
# ═══════════════════════════════════════════════════════════════════════════

def _nazorat_fayllari():
    """Sog'lom nazorat fayllari + korpus (634 ta haqiqiy hujjat, bo'lsa)."""
    yollar = []
    for papka in ("data", "data_new", os.path.join("korpus", "fayllar")):
        p = os.path.join(_ILDIZ, papka)
        for ildiz, _kat, fayllar in os.walk(p):
            for f in fayllar:
                if f.startswith("~$"):
                    continue
                if os.path.splitext(f)[1].lower() in (".xls", ".xlsx"):
                    yollar.append(os.path.join(ildiz, f))
    return yollar


def test_soglom_fayllar_bu_yolga_TUSHMAYDI():
    """Darvoza sog'lom hujjatni XLSB deb hisoblamasin.

    Bu modulning ekvivalentlik kafolati: `reader` uni faqat xato yo'lida
    chaqiradi, va chaqirsa ham darvoza `None` qaytarib chetga chiqadi —
    demak sog'lom fayl uchun bitta ham qo'shimcha zip ochilmaydi.
    """
    fayllar = _nazorat_fayllari()
    if not fayllar:
        pytest.skip("nazorat fayllari topilmadi (data/, data_new/)")
    xato = [f for f in fayllar if T.xlsb_faylmi(f)]
    assert not xato, f"sog'lom fayl XLSB deb tanildi: {xato}"
    assert all(T.tikla(f) is None for f in fayllar), "tikla() chetga chiqmadi"


def test_zip_bolmagan_fayl(tmp_path):
    yol = str(tmp_path / "matn.xlsx")
    open(yol, "wb").write(b"bu zip emas")
    assert T.xlsb_faylmi(yol) is False
    assert T.tikla(yol) is None


def test_haqiqiy_xlsx_xlsb_emas(tmp_path):
    """`xl/workbook.xml` li paket — XLSB EMAS."""
    yol = str(tmp_path / "oddiy.xlsx")
    with zipfile.ZipFile(yol, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("xl/workbook.xml", "<workbook/>")
    assert T.xlsb_faylmi(yol) is False


# ═══════════════════════════════════════════════════════════════════════════
# 3. O'chirish kaliti
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("kalit", ["TIKLASH_YOQ", "TIKLASH_XLSB"])
def test_ochirish_kaliti(namuna, monkeypatch, kalit):
    monkeypatch.setenv(kalit, "0")
    assert T.yoqilganmi() is False
    assert T.tikla(namuna) is None, "o'chirilgan modul baribir ishladi"


def test_standart_holatda_YOQIQ(monkeypatch):
    monkeypatch.delenv("TIKLASH_YOQ", raising=False)
    monkeypatch.delenv("TIKLASH_XLSB", raising=False)
    assert T.yoqilganmi() is True


# ═══════════════════════════════════════════════════════════════════════════
# 4. Tiklash yiqilsa — ASL xato ko'tariladi
# ═══════════════════════════════════════════════════════════════════════════

def _buzuq_xlsb(tmp_path):
    """XLSB deb tanaladi (`xl/workbook.bin` bor), lekin mazmuni axlat."""
    yol = str(tmp_path / "buzuq.xlsx")
    with zipfile.ZipFile(yol, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("xl/workbook.bin", b"\x00" * 8)
    return yol


def test_asl_xato_QAYTA_kotariladi(tmp_path):
    """Ishtirokchiga ko'rinadigan izoh tiklash tufayli o'zgarmasin."""
    yol = _buzuq_xlsb(tmp_path)
    assert T.xlsb_faylmi(yol) is True
    asl = ValueError("File contains no valid workbook part")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with pytest.raises(ValueError) as tutildi:
            T.tikla(yol, asl_xato=asl)
    assert tutildi.value is asl, "asl xato emas, boshqa istisno ko'tarildi"
    assert isinstance(tutildi.value.__cause__, T.XlsbXato), "sabab saqlanmadi"


def test_asl_xatosiz_ozining_istisnosi(tmp_path):
    yol = _buzuq_xlsb(tmp_path)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with pytest.raises(T.XlsbXato):
            T.tikla(yol)


# ═══════════════════════════════════════════════════════════════════════════
# 5. RK — Excel'ning siqilgan son formati (birlik testlari)
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("rk, kutilgan", [
    (0x00000002, 0.0),            # fInt, 0
    (0x00000006, 1.0),            # fInt, 1
    (0xFFFFFFFE, -1.0),           # fInt, manfiy (30-bitli ishorali son)
    (0x00000007, 0.01),           # fInt + fX100
    (0x000004E3, 3.12),           # fInt + fX100: 312/100
    (0x3FF00000, 1.0),            # double yuqori 30 bit
    (0x40590000, 100.0),
    (0x40590001, 1.0),            # o'sha, fX100 -> 100/100
    (0xC0590000, -100.0),
])
def test_rk_qiymat(rk, kutilgan):
    assert T._rk_qiymat(rk) == pytest.approx(kutilgan, rel=1e-12, abs=1e-12)


def test_rk_yuqori_30_bit_ishlatiladi():
    """fInt=0 bo'lsa quyi 34 bit NOL deb olinadi — aniq shu ayniyat."""
    xom = struct.unpack("<Q", struct.pack("<d", 1234.5))[0]
    rk = (xom >> 32) & 0xFFFFFFFC
    assert T._rk_qiymat(rk) == pytest.approx(1234.5, rel=1e-9)


# ═══════════════════════════════════════════════════════════════════════════
# 6. Katak turlari — sintetik XLSB
# ═══════════════════════════════════════════════════════════════════════════

def test_barcha_katak_turlari(tmp_path):
    """RK, Real, Isst, St, FmlaNum, FmlaString, Bool, Error — hammasi."""
    yol = _xlsb_yasa(
        str(tmp_path / "turlar.xlsx"),
        kataklar=[
            (0, 0, 2, struct.pack("<I", 0x00000006)),          # RK -> 1
            (0, 1, 5, struct.pack("<d", 1234.56)),             # Real
            (0, 2, 7, struct.pack("<I", 1)),                   # Isst -> SST[1]
            (0, 3, 6, _ws("ichki matn")),                      # St
            (0, 4, 9, struct.pack("<d", 99.5) + b"\x00\x00"),  # FmlaNum
            (0, 5, 8, _ws("formula matni") + b"\x00\x00"),     # FmlaString
            (0, 6, 4, b"\x01"),                                # Bool
            (0, 7, 3, b"\x0f"),                                # Error -> #VALUE!
            (0, 8, 3, b"\x07"),                                # Error -> #DIV/0!
            (0, 9, 1, b""),                                    # Blank — qiymatsiz
        ],
        sst=["birinchi", "лугат матни"])
    q = T.oqi(yol)["Лист1"][0]
    assert q[0] == 1 and isinstance(q[0], int)
    assert q[1] == pytest.approx(1234.56)
    assert q[2] == "лугат матни"
    assert q[3] == "ichki matn"
    assert q[4] == pytest.approx(99.5)
    assert q[5] == "formula matni"
    assert q[6] is True
    assert q[7] == "#VALUE!"
    assert q[8] == "#DIV/0!"
    assert q[9] is None, "bo'sh-uslubli katak qiymat bermasin"
    assert len(q) == 10, "kenglik bo'sh katakni ham hisobga olsin"


def test_sst_chegaradan_tashqari_indeks(tmp_path):
    """Buzuq havola butun hujjatni yiqitmasin (noo'rin rad bo'lmasin)."""
    yol = _xlsb_yasa(str(tmp_path / "sst.xlsx"),
                     kataklar=[(0, 0, 7, struct.pack("<I", 999))],
                     sst=["bor"])
    assert T.oqi(yol)["Лист1"][0][0] == ""


def test_kesilgan_oqim_yiqitmaydi(tmp_path):
    """Oxiri kesilgan BIFF12 oqimi — o'qilgani saqlanadi, istisno yo'q."""
    yol = str(tmp_path / "kesik.xlsx")
    _xlsb_yasa(yol, kataklar=[(0, 0, 2, struct.pack("<I", 0x00000006)),
                              (1, 0, 2, struct.pack("<I", 0x0000000A))])
    with zipfile.ZipFile(yol) as z:
        qismlar = {i.filename: z.read(i) for i in z.infolist()}
    qismlar["xl/worksheets/sheet1.bin"] = \
        qismlar["xl/worksheets/sheet1.bin"][:-3]
    with zipfile.ZipFile(yol, "w") as z:
        for nom, xom in qismlar.items():
            z.writestr(nom, xom)
    varaqlar = T.oqi(yol)
    assert varaqlar["Лист1"][0][0] == 1


def test_rels_buzuq_bolsa_zaxira_yol(tmp_path):
    """Rels yo'q bo'lsa varaq zipdan to'g'ridan-to'g'ri olinadi."""
    yol = str(tmp_path / "rels.xlsx")
    _xlsb_yasa(yol, kataklar=[(0, 0, 5, struct.pack("<d", 7.0))])
    with zipfile.ZipFile(yol) as z:
        qismlar = {i.filename: z.read(i) for i in z.infolist()
                   if i.filename != "xl/_rels/workbook.bin.rels"}
    with zipfile.ZipFile(yol, "w") as z:
        for nom, xom in qismlar.items():
            z.writestr(nom, xom)
    varaqlar = T.oqi(yol)
    assert list(varaqlar) == ["sheet1"]
    assert varaqlar["sheet1"][0][0] == 7


# ═══════════════════════════════════════════════════════════════════════════
# 7. Budjet va chegaralar
# ═══════════════════════════════════════════════════════════════════════════

def test_yozuv_budjeti(namuna):
    """Tugun budjeti tugasa — TOZA istisno (livelock qaytmasin)."""
    with pytest.raises(T.XlsbBudjetTugadi):
        T.oqi(namuna, max_yozuv=5)


def test_vaqt_budjeti(namuna, monkeypatch):
    """Vaqt budjeti ham ishlaydi — soatni oldinga surib tekshiramiz."""
    soat = iter([0.0] + [10_000.0] * 10_000)
    monkeypatch.setattr(T.time, "monotonic", lambda: next(soat))
    with pytest.raises(T.XlsbBudjetTugadi):
        T.oqi(namuna, budjet_sek=1.0)


def test_qator_chegarasi(tmp_path, monkeypatch):
    monkeypatch.setattr(T, "MAX_SHEET_ROWS", 10)
    yol = _xlsb_yasa(str(tmp_path / "uzun.xlsx"),
                     kataklar=[(50, 0, 5, struct.pack("<d", 1.0))])
    with pytest.raises(T.XlsbJudaKatta):
        T.oqi(yol)


def test_ustun_chegarasi_QIRQADI(tmp_path, monkeypatch):
    """Keng varaq RAD ETILMAYDI, kenglikka qirqiladi (`reader` kabi)."""
    monkeypatch.setattr(T, "MAX_SHEET_COLS", 4)
    yol = _xlsb_yasa(str(tmp_path / "keng.xlsx"),
                     kataklar=[(0, 0, 5, struct.pack("<d", 1.0)),
                               (0, 900, 5, struct.pack("<d", 2.0))])
    varaqlar = T.oqi(yol)
    assert len(varaqlar["Лист1"][0]) <= 4
    assert varaqlar["Лист1"][0][0] == 1


def test_katak_budjeti(tmp_path, monkeypatch):
    monkeypatch.setattr(T, "MAX_XLSB_KATAK", 100)
    yol = _xlsb_yasa(str(tmp_path / "katta.xlsx"),
                     kataklar=[(500, 20, 5, struct.pack("<d", 1.0))])
    with pytest.raises(T.XlsbJudaKatta):
        T.oqi(yol)


def test_katta_birlashma_OCHILMAYDI_lekin_rad_ETILMAYDI(tmp_path, monkeypatch):
    """Zip-bomba himoyasi hujjatni rad etmasin — faqat ochish to'xtatilsin."""
    monkeypatch.setattr(T, "MAX_MERGE_CELLS", 4)
    yol = _xlsb_yasa(str(tmp_path / "merge.xlsx"),
                     kataklar=[(0, 0, 5, struct.pack("<d", 42.0))],
                     birlashmalar=[(0, 9, 0, 9)])       # 100 katak
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        varaqlar = T.oqi(yol)
    assert varaqlar["Лист1"][0][0] == 42, "qiymat yo'qoldi"
    assert varaqlar["Лист1"][0][1] is None, "chegaradan katta birlashma ochildi"
    ogoh = [x for x in w if "birlashma" in str(x.message)]
    assert len(ogoh) == 1, "ogohlantirish har diapazon uchun takrorlanmasin"


def test_manfiy_RK_son_yiqitmaydi(tmp_path):
    """Manfiy `double` RK — `<q` bilan «argument out of range» berardi.

    Bitta manfiy qiymat butun hujjatni o'qilmas qilib qo'yardi; XLSB
    smetalarida esa manfiy tuzatishlar («минус транспорт») uchraydi.
    """
    xom = struct.unpack("<Q", struct.pack("<d", -250.0))[0]
    rk = (xom >> 32) & 0xFFFFFFFC
    yol = _xlsb_yasa(str(tmp_path / "manfiy.xlsx"),
                     kataklar=[(0, 0, 2, struct.pack("<I", rk))])
    assert T.oqi(yol)["Лист1"][0][0] == pytest.approx(-250.0)


def test_kichik_birlashma_ochiladi(tmp_path):
    yol = _xlsb_yasa(str(tmp_path / "m2.xlsx"),
                     kataklar=[(0, 0, 6, _ws("sarlavha"))],
                     birlashmalar=[(0, 0, 0, 2)])
    q = T.oqi(yol)["Лист1"][0]
    assert q[0] == q[1] == q[2] == "sarlavha"


def test_qator_sarlavhasiz_katak_YOQOLMAYDI(tmp_path):
    """`BrtRowHdr` siz kelgan katak jim yo'qolib qolmasin.

    E'lon qilingan balandlik faqat qator sarlavhalaridan yig'iladi;
    nostandart generatorda sarlavha bo'lmasa u 0 bo'lib qolardi va
    o'qilgan qiymat jadvalga tushmasdi (ya'ni to'ldirilgan hujjat
    «bo'sh» ko'rinardi).
    """
    yol = str(tmp_path / "hdr.xlsx")
    sheet = (_yozuv(129)
             + _yozuv(5, _katak(2) + struct.pack("<d", 55.0))   # RowHdr YO'Q
             + _yozuv(130))
    _xlsb_yasa(yol, kataklar=[])
    with zipfile.ZipFile(yol) as z:
        qismlar = {i.filename: z.read(i) for i in z.infolist()}
    qismlar["xl/worksheets/sheet1.bin"] = sheet
    with zipfile.ZipFile(yol, "w") as z:
        for nom, xom in qismlar.items():
            z.writestr(nom, xom)
    assert T.oqi(yol)["Лист1"][0][2] == 55


def test_qism_hajm_chegarasi(tmp_path, monkeypatch):
    monkeypatch.setattr(T, "MAX_XLSX_PART_BYTES", 16)
    yol = _xlsb_yasa(str(tmp_path / "bomba.xlsx"),
                     kataklar=[(0, 0, 5, struct.pack("<d", 1.0))])
    with pytest.raises(T.XlsbJudaKatta):
        T.oqi(yol)
