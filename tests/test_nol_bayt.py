# -*- coding: utf-8 -*-
"""Nol-bayt buzilishidan tiklash (`tender_engine.tiklash_nol_bayt`).

MUAMMO. Platformaga yuklangan 24 ta hujjat yo'lda MATN deb ko'chirilgan:
har bir 0x00 bayt 0x20 (probel) ga aylangan. `zipfile` ularni ochmaydi va
to'ldirilgan HALOL hujjat «Faylni ochib bo'lmadi» deb RAD etilardi —
bu BIZNING o'quvchimizning cheklovi, hujjatning aybi emas.

Bu testlar to'rt narsani qotiradi:
  1. SOG'LOM fayl tiklashga UMUMAN kirmaydi (avvalgi yo'l aynan saqlanadi);
  2. buzilgan fayl tiklanadi va mazmuni asl bilan BIR XIL chiqadi
     (aks holda noto'g'ri verdikt chiqishi mumkin edi);
  3. ikkita hal qiluvchi noziklik — bag'rikeng CRC solishtiruvi va qo'shni
     bayt transpozitsiyasi — ishlayapti;
  4. budjet va o'chirish kaliti kuchda: qidiruv hech qachon osilib qolmaydi
     (2026-09-02 dagi O(qator^2) livelock takrorlanmasin).
"""
import hashlib
import os
import shutil
import sys
import zipfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

openpyxl = pytest.importorskip("openpyxl")

from tender_engine import tiklash_nol_bayt as T

LOYIHA = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
#: Haqiqiy buzuq fayllar (bo'lmasa testlar o'tkazib yuboriladi).
BUZUQ_PAPKA = os.environ.get("NOL_BAYT_KORPUS", os.path.join(
    os.environ.get("TEMP", "/tmp"), "claude",
    "c--Users-User-Downloads-Telegram-Desktop-tender-reject",
    "8f27e0d5-5d9b-464e-8df2-f3df5ba17509", "scratchpad", "buzuq_fayllar"))


# ── Yordamchilar ─────────────────────────────────────────────────────────────
def _xlsx_yasa(yol, qatorlar, varaq="Лист1"):
    """Kichik .xlsx fikstura (matn + sonlar aralash)."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = varaq
    for qator in qatorlar:
        ws.append(qator)
    wb.save(yol)
    return yol


def _nol_baytni_buz(yol):
    """Uzatish nuqsonini AYNAN taqlid qiladi: har 0x00 -> 0x20."""
    with open(yol, "rb") as fh:
        xom = fh.read()
    assert b"\x00" in xom, "fikstura ichida 0x00 bo'lishi shart"
    with open(yol, "wb") as fh:
        fh.write(xom.replace(b"\x00", b"\x20"))
    return yol


def _qiymatlar(yol):
    wb = openpyxl.load_workbook(yol, data_only=True)
    natija = {}
    for ws in wb.worksheets:
        natija[ws.title] = [list(q) for q in ws.iter_rows(values_only=True)]
    wb.close()
    return natija


QATORLAR = [
    ["Т/Р", "Харажатлар номи", "Жами қиймати"],
    [1, "Бетон ишлари", 1250000],
    [2, "Темир бетон", 87500.5],
    [3, "Пойдевор", 42000],
]


@pytest.fixture()
def juftlik(tmp_path):
    """(sog'lom_yo'l, buzilgan_yo'l) — mazmuni bir xil, biri buzilgan."""
    sogl = _xlsx_yasa(str(tmp_path / "sogl.xlsx"), QATORLAR)
    buzuq = str(tmp_path / "buzuq.xlsx")
    shutil.copyfile(sogl, buzuq)
    _nol_baytni_buz(buzuq)
    return sogl, buzuq


# ── 1. Sog'lom fayl tiklashga kirmaydi ───────────────────────────────────────
def test_soglom_fayl_shubha_uygotmaydi(juftlik):
    """Sog'lom .xlsx da 0x00 har doim bor — shubha darhol yopiladi.

    Bu ekvivalentlik kafolati: sog'lom hujjat uchun bitta ham qo'shimcha
    zip ochish yoki tekshiruv bajarilmaydi.
    """
    sogl, _ = juftlik
    with open(sogl, "rb") as fh:
        assert T.nol_bayt_shubhasi(fh.read()) is False


def test_soglom_fayl_tiklanmaydi(juftlik):
    sogl, _ = juftlik
    with pytest.raises(T.TiklashXatosi):
        T.tikla(sogl)


@pytest.mark.parametrize("xom", [
    b"",                       # bo'sh
    b"PK\x03\x04",             # juda kalta
    b"XX\x03\x04" + b"a" * 64,  # .xlsx emas
])
def test_notogri_kirish_shubha_uygotmaydi(xom):
    assert T.nol_bayt_shubhasi(xom) is False


def test_soglom_haqiqiy_fayllar(tmp_path):
    """`data/` va `data_new/` dagi nazorat fayllari — yolg'on shubha bo'lmasin."""
    yollar = []
    for papka in ("data", "data_new"):
        for dirp, _, fs in os.walk(os.path.join(LOYIHA, papka)):
            yollar += [os.path.join(dirp, f) for f in fs
                       if f.lower().endswith((".xlsx", ".xls")) and not f.startswith("~$")]
    if not yollar:
        pytest.skip("nazorat fayllari yo'q")
    for yol in yollar:
        with open(yol, "rb") as fh:
            assert T.nol_bayt_shubhasi(fh.read()) is False, yol


# ── 2. Buzilgan fayl tiklanadi va mazmuni o'zgarmaydi ────────────────────────
def test_buzilgan_fayl_shubha_uygotadi(juftlik):
    _, buzuq = juftlik
    with open(buzuq, "rb") as fh:
        assert T.nol_bayt_shubhasi(fh.read()) is True


def test_tiklangan_mazmun_asl_bilan_bir_xil(juftlik):
    """Eng muhim test: tiklash ma'lumotni BUZMAYDI.

    Aks holda narx kataklari o'zgarib, noto'g'ri verdikt chiqishi mumkin edi.
    """
    sogl, buzuq = juftlik
    hisobot = {}
    nusxa = T.tikla(buzuq, hisobot)
    try:
        assert _qiymatlar(nusxa) == _qiymatlar(sogl)
        assert hisobot["tiklandi"] == hisobot["yozuv_soni"]
        assert hisobot["ss_orinbosar"] is False
    finally:
        shutil.rmtree(os.path.dirname(nusxa), ignore_errors=True)


def test_asl_faylga_tegilmaydi(juftlik):
    """Talab: asl fayl bayt-ba-bayt o'zgarmasin, natija boshqa papkada bo'lsin."""
    _, buzuq = juftlik
    oldin = hashlib.sha256(open(buzuq, "rb").read()).hexdigest()
    nusxa = T.tikla(buzuq)
    try:
        keyin = hashlib.sha256(open(buzuq, "rb").read()).hexdigest()
        assert oldin == keyin
        assert os.path.dirname(os.path.abspath(nusxa)) != \
            os.path.dirname(os.path.abspath(buzuq))
    finally:
        shutil.rmtree(os.path.dirname(nusxa), ignore_errors=True)


def test_tiklangan_fayl_ochiladi_va_crc_mos(juftlik):
    """Har yozuvning CRC32 si zip maydoniga (bag'rikeng) mos kelishi shart."""
    import binascii
    _, buzuq = juftlik
    with open(buzuq, "rb") as fh:
        xom = fh.read()
    ents, _cdo = T._yozuvlar(xom)
    nusxa = T.tikla(buzuq)
    try:
        with zipfile.ZipFile(nusxa) as z:
            nomlar = set(z.namelist())
            tekshirildi = 0
            for e in ents:
                if e["nom"] not in nomlar:
                    continue
                mazmun = z.read(e["nom"])
                assert T._mos(binascii.crc32(mazmun), e["crc_xom"]), e["nom"]
                assert T._mos(len(mazmun), e["usz_xom"]), e["nom"]
                tekshirildi += 1
        assert tekshirildi == len(ents)
    finally:
        shutil.rmtree(os.path.dirname(nusxa), ignore_errors=True)


# ── 3. Ikki hal qiluvchi noziklik ────────────────────────────────────────────
def test_bagrikeng_crc_solishtiruvi():
    """Noziklik «a»: CRC maydonining O'ZI ham buzilgan bo'lishi mumkin.

    Haqiqiy misol — 113561 ning `sheet1.xml` CRC si 0x20f4be07: maydonning
    yuqori bayti HAQIQIY 0x20. Ko'r-ko'rona 0x20 -> 0x00 qilinsa TO'G'RI
    yechim rad etilardi (aynan shu sabab fayl tiklanmay qolgan edi).
    """
    xom = b"\x07\xbe\xf4\x20"                 # little-endian 0x20f4be07
    assert T._mos(0x20f4be07, xom) is True
    assert T._min(xom) == 0x00f4be07          # ko'r-ko'rona talqin — BOSHQA son
    assert T._mos(0x00f4be07, xom) is True    # bu talqin ham mumkin (bag'rikenglik)
    # Begona qiymat baribir o'tmaydi — 8 baytlik moslik yolg'on qabulni yopadi.
    assert T._mos(0x20f4be08, xom) is False
    assert T._mos(0x21f4be07, xom) is False


def test_qoshni_bayt_transpozitsiyasi_tamirlanadi(juftlik):
    """Noziklik «b»: ko'chirishda qo'shni ikki bayt o'rin almashishi mumkin.

    Haqiqiy misol — 113533 da 105/106 pozitsiya. Ta'mirsiz varaq umuman
    ochilmasdi; CRC32 baribir hakam bo'lgani uchun ta'mir xavfsiz.
    """
    sogl, buzuq = juftlik
    with open(buzuq, "rb") as fh:
        xom = bytearray(fh.read())
    ents, _cdo = T._yozuvlar(bytes(xom))
    varaq = [e for e in ents if e["nom"].startswith("xl/worksheets/")][0]
    # Varaq ma'lumotining ichida farqli qo'shni juftlikni almashtiramiz.
    j = varaq["doff"] + 40
    while xom[j] == xom[j + 1]:
        j += 1
    xom[j], xom[j + 1] = xom[j + 1], xom[j]
    with open(buzuq, "wb") as fh:
        fh.write(bytes(xom))

    hisobot = {}
    nusxa = T.tikla(buzuq, hisobot)
    try:
        assert _qiymatlar(nusxa) == _qiymatlar(sogl)
        tamir = dict(hisobot["tuzatish"])
        assert varaq["nom"] in tamir, hisobot["tuzatish"]
        assert tamir[varaq["nom"]] == [j - varaq["doff"]]
    finally:
        shutil.rmtree(os.path.dirname(nusxa), ignore_errors=True)


# ── 4. Budjet, o'chirish kaliti, xavfsizlik chegaralari ──────────────────────
def test_tugun_budjeti_toza_istisno(juftlik, monkeypatch):
    """Budjet tugasa OSILIB QOLMAYDI — toza `BudjetTugadi` qaytadi."""
    _, buzuq = juftlik
    monkeypatch.setattr(T, "NOL_BAYT_TUGUN", 1)
    with pytest.raises(T.BudjetTugadi):
        T.tikla(buzuq)


def test_vaqt_budjeti_toza_istisno(juftlik, monkeypatch):
    _, buzuq = juftlik
    monkeypatch.setattr(T, "NOL_BAYT_SONIYA", -1.0)
    with pytest.raises(T.BudjetTugadi):
        T.tikla(buzuq)


def test_budjet_tugashi_tiklash_xatosining_turi(juftlik, monkeypatch):
    """`BudjetTugadi` — `TiklashXatosi` ning bir turi: chaqiruvchi bitta
    `except` bilan ushlab, ASL o'qish xatosini qayta ko'tara oladi."""
    assert issubclass(T.BudjetTugadi, T.TiklashXatosi)
    _, buzuq = juftlik
    monkeypatch.setattr(T, "NOL_BAYT_TUGUN", 1)
    with pytest.raises(T.TiklashXatosi):
        T.tikla(buzuq)


def test_ochirish_kaliti(juftlik, monkeypatch):
    """`TIKLASH_YOQ=0` — modul butunlay o'chadi."""
    _, buzuq = juftlik
    monkeypatch.setattr(T, "TIKLASH_YOQ", False)
    with pytest.raises(T.TiklashXatosi) as xato:
        T.tikla(buzuq)
    assert "TIKLASH_YOQ" in str(xato.value)


def test_zip_bomba_chegarasi(juftlik, monkeypatch):
    """Reader'ning `MAX_XLSX_PART_BYTES` chegarasi tiklangan mazmunga HAM
    tatbiq etiladi — ta'mir orqali zip-bomba kirib kelmasin."""
    _, buzuq = juftlik
    monkeypatch.setattr(T, "MAX_XLSX_PART_BYTES", 32)
    with pytest.raises(T.TiklashXatosi) as xato:
        T.tikla(buzuq)
    assert "bomba" in str(xato.value)


def test_yozuv_soni_chegarasi(juftlik, monkeypatch):
    _, buzuq = juftlik
    monkeypatch.setattr(T, "NOL_BAYT_MAX_YOZUV", 2)
    with pytest.raises(T.TiklashXatosi):
        T.tikla(buzuq)


def test_katta_fayl_qabul_qilinmaydi(juftlik, monkeypatch):
    _, buzuq = juftlik
    monkeypatch.setattr(T, "NOL_BAYT_MAX_FAYL", 100)
    with pytest.raises(T.TiklashXatosi):
        T.tikla(buzuq)


@pytest.mark.parametrize("nom,kutilgan", [
    ("xl/worksheets/sheet1.xml", True),
    ("../evil.xml", False),
    ("/etc/passwd", False),
    ("C:/evil.xml", False),
    ("xl/", False),
])
def test_xavfsiz_nom(nom, kutilgan):
    """Yozuv nomi yo'ldan chiqib ketmasin (yig'ilayotgan arxivga ham)."""
    assert T._xavfsiz_nom(nom) is kutilgan


# ── 5. Haqiqiy korpus (bo'lmasa o'tkazib yuboriladi) ─────────────────────────
def _korpus_noyoblari():
    if not os.path.isdir(BUZUQ_PAPKA):
        return {}
    noyob = {}
    for f in sorted(os.listdir(BUZUQ_PAPKA)):
        yol = os.path.join(BUZUQ_PAPKA, f)
        if not os.path.isfile(yol):
            continue
        with open(yol, "rb") as fh:
            xom = fh.read()
        if T.nol_bayt_shubhasi(xom):
            noyob.setdefault(hashlib.sha256(xom).hexdigest(), []).append(yol)
    return noyob


def test_haqiqiy_buzuq_fayllar_tiklanadi():
    """24 ta haqiqiy rad etilgan hujjat (3 noyob mazmun) — hammasi tiklanadi.

    Har yozuv CRC32 bilan tasdiqlanadi, so'ng fayl `openpyxl` bilan
    o'qilib RAQAMLI kataklar borligi tekshiriladi (narx tekshiruvi
    shularga tayanadi).
    """
    import binascii
    noyob = _korpus_noyoblari()
    if not noyob:
        pytest.skip("haqiqiy buzuq fayllar to'plami yo'q: %s" % BUZUQ_PAPKA)
    jami = 0
    for _h, yollar in noyob.items():
        yol = yollar[0]
        with open(yol, "rb") as fh:
            xom = fh.read()
        ents, _cdo = T._yozuvlar(xom)
        hisobot = {}
        nusxa = T.tikla(yol, hisobot)
        try:
            with zipfile.ZipFile(nusxa) as z:
                nomlar = set(z.namelist())
                for e in ents:
                    if e["nom"] not in nomlar or (hisobot["ss_orinbosar"]
                                                  and e["nom"] == "xl/sharedStrings.xml"):
                        continue
                    mazmun = z.read(e["nom"])
                    assert T._mos(binascii.crc32(mazmun), e["crc_xom"]), (yol, e["nom"])
                    assert T._mos(len(mazmun), e["usz_xom"]), (yol, e["nom"])
            qiymatlar = _qiymatlar(nusxa)
            sonlar = sum(1 for rows in qiymatlar.values() for r in rows
                         for c in r if isinstance(c, (int, float)))
            assert sonlar > 0, (yol, "raqamli katak topilmadi")
            assert hisobot["tugun"] < T.NOL_BAYT_TUGUN
        finally:
            shutil.rmtree(os.path.dirname(nusxa), ignore_errors=True)
        jami += len(yollar)
    assert jami >= 3


def test_haqiqiy_boshqa_sinf_fayllar_davo_qilinmaydi():
    """Boshqa sinf buzuq fayllar (nol-bayt EMAS) da modul jim turadi."""
    if not os.path.isdir(BUZUQ_PAPKA):
        pytest.skip("haqiqiy buzuq fayllar to'plami yo'q")
    noyob = _korpus_noyoblari()
    nolbayt = {y for yollar in noyob.values() for y in yollar}
    boshqa = [os.path.join(BUZUQ_PAPKA, f) for f in os.listdir(BUZUQ_PAPKA)]
    boshqa = [y for y in boshqa if os.path.isfile(y) and y not in nolbayt]
    if not boshqa:
        pytest.skip("boshqa sinf fayllar yo'q")
    for yol in boshqa:
        with pytest.raises(T.TiklashXatosi):
            T.tikla(yol)


# ── 6. Ichki kesish qoidalari (regressiya) ───────────────────────────────────
def test_qatiy_feed_katak_manzili_monoton():
    """Katak manzili invarianti — qidiruvni o'n baravar qisqartiradigan qoida."""
    ok = T._qatiy_feed(T.QATIY_BOSH, b'<row r="1"><c r="A1"><v>5</v></c>'
                                    b'<c r="B1"><v>6</v></c></row>')
    assert ok is not None
    # ustun orqaga ketdi -> shox o'ladi
    assert T._qatiy_feed(T.QATIY_BOSH,
                         b'<row r="1"><c r="B1"/><c r="A1"/></row>') is None
    # qator orqaga ketdi -> shox o'ladi
    assert T._qatiy_feed(T.QATIY_BOSH, b'<row r="5"/><row r="2"/>') is None
    # <v> ichida son bo'lmasa -> shox o'ladi
    assert T._qatiy_feed(T.QATIY_BOSH,
                         b'<row r="1"><c r="A1"><v>abc</v></c></row>') is None


def test_kutilmagan_xato_ham_tiklash_xatosi(tmp_path):
    """Har qanday ichki nosozlik BITTA turga o'raladi (5-talab).

    Chaqiruvchi faqat `TiklashXatosi` ni ushlaydi va ASL o'qish xatosini
    qayta ko'taradi — ishtirokchiga ko'rinadigan izoh o'zgarmaydi.
    """
    yoq = str(tmp_path / "yoq.xlsx")
    with pytest.raises(T.TiklashXatosi):
        T.tikla(yoq)                       # fayl umuman yo'q -> OSError o'ralади
    # EOCD bor, lekin skelet ma'nosiz — struct/index xatolari ham o'raladi.
    shubhali = str(tmp_path / "shubhali.xlsx")
    with open(shubhali, "wb") as fh:
        fh.write(b"PK\x03\x04" + b"\x11" * 40 + b"PK\x05\x06" + b"\xff" * 18)
    with pytest.raises(T.TiklashXatosi):
        T.tikla(shubhali)


def test_erkin_feed_xml_tuzilmasi():
    assert T._erkin_feed(0, b'<a b="c"/><d/>') is not None
    assert T._erkin_feed(0, b'<a b="c"<>') is None
    assert T._erkin_feed(0, b"<\x01>") is None
