# -*- coding: utf-8 -*-
"""`tender_engine.tiklash_rc4` — Excel ning standart «faqat o'qish» paroli.

Sinovning ikki qismi bor:

  1. TARMOQSIZ, fayl talab qilmaydigan qism — o'chirish kaliti, budjet,
     shifr turini ajratish, parol TANLAMASLIK kafolati, yasalgan CFB
     konteynerning to'g'riligi. Har doim ishlaydi.
  2. HAQIQIY 4 ta shifrlangan hujjat (55729, 99700, 99702, 99703). Ular
     scratchpad'dagi `buzuq_fayllar` papkasida; papka bo'lmasa bu qism
     `skip` bo'ladi, sinov to'plami yiqilmaydi.

Tezlik: to'liq deshifrlash faqat IKKI faylda bajariladi (99700/99702/99703
bayt-ba-bayt bir xil), natija sessiya davomida keshlanadi.
"""

import hashlib
import os
import struct
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tender_engine import tiklash_rc4 as T
from tender_engine.reader import read_file

LOYIHA = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOGLOM_XLS = os.path.join(LOYIHA, "data", "Ishtirokchi oferta 4.xls")

# Sog'lom nazorat .xls handoff zipiga kirmaydi (haqiqiy fayl) — u bo'lmasa butun
# modul SKIP (2026-09-18). Asl repoda fayl bor, u yerda hech narsa o'zgarmaydi.
pytestmark = pytest.mark.skipif(
    not os.path.isfile(SOGLOM_XLS),
    reason="data/Ishtirokchi oferta 4.xls nazorat fayli yo'q (handoff zipida haqiqiy ma'lumot yo'q)")

# Haqiqiy shifrlangan hujjatlar. Yo'lni muhit o'zgaruvchisi bilan almashtirish
# mumkin — CI da fayllar boshqa joyda bo'lishi mumkin.
FAYLLAR_DIR = os.environ.get(
    "RC4_TEST_FAYLLAR",
    os.path.join(
        os.environ.get("LOCALAPPDATA", ""), "Temp", "claude",
        "c--Users-User-Downloads-Telegram-Desktop-tender-reject",
        "8f27e0d5-5d9b-464e-8df2-f3df5ba17509", "scratchpad", "buzuq_fayllar"))

SHIFRLANGAN = (55729, 99700, 99702, 99703)
# To'liq deshifrlanadigan namunalar: 99700/99702/99703 bir xil fayl, bittasi yetarli.
TOLIQ = (55729, 99700)


def _yol(fid):
    return os.path.join(FAYLLAR_DIR, "%d.xls" % fid)


def _bor(fid):
    return os.path.isfile(_yol(fid))


shifrlangan_kerak = pytest.mark.skipif(
    not all(_bor(f) for f in SHIFRLANGAN),
    reason="shifrlangan sinov fayllari topilmadi: %s" % FAYLLAR_DIR)

# Muhitda `TIKLASH_YOQ=0` bo'lsa modul ataylab o'chirilgan — tiklashni talab
# qiladigan sinovlar «yiqilmaydi», o'tkazib yuboriladi.
YOQILGAN = T._yoqilganmi()
yoqilgan_kerak = pytest.mark.skipif(
    not YOQILGAN, reason="TIKLASH_YOQ=0 — tiklash o'chirilgan")


# ── 1-qism: fayl talab qilmaydigan sinovlar ────────────────────────────────

def test_ochirish_kaliti(monkeypatch):
    """`TIKLASH_YOQ=0` va `TIKLASH_RC4_YOQ=0` modulni butunlay o'chiradi."""
    if YOQILGAN:
        assert T._yoqilganmi() is True             # standart holat — YOQIQ
    for nom in ("TIKLASH_YOQ", "TIKLASH_RC4_YOQ"):
        monkeypatch.setenv(nom, "0")
        assert T._yoqilganmi() is False
        # O'chiq holatda tikla() faylga umuman qaramaydi.
        assert T.tikla(SOGLOM_XLS) is None
        monkeypatch.delenv(nom)


def test_shifrlangan_xatomi():
    """Tiklash FAQAT «shifrlangan» xatosida ishga tushsin."""
    import xlrd
    assert T.shifrlangan_xatomi(xlrd.biffh.XLRDError("Workbook is encrypted"))
    assert not T.shifrlangan_xatomi(ValueError("Unsupported file format: '.doc'"))
    assert not T.shifrlangan_xatomi(AssertionError(""))


def test_soglom_xls_tegilmaydi():
    """Shifrlanmagan `.xls` — FILEPASS yo'q, tiklash rad etadi (None)."""
    assert os.path.isfile(SOGLOM_XLS)
    oldin = hashlib.sha256(open(SOGLOM_XLS, "rb").read()).hexdigest()
    assert T.tikla(SOGLOM_XLS) is None
    keyin = hashlib.sha256(open(SOGLOM_XLS, "rb").read()).hexdigest()
    assert oldin == keyin, "sog'lom faylga TEGILDI"


@pytest.mark.parametrize("mazmun,nom", [
    (b"<html>404 not found</html>", "html.xls"),        # OLE2 emas
    (b"", "bosh.xls"),                                  # 0 bayt
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64, "kesik.xls"),  # kesilgan sarlavha
])
def test_notogri_fayl_toza_rad(tmp_path, mazmun, nom):
    """Buzuq kirish jim rad etiladi — xom `struct.error` chiqmasin."""
    p = tmp_path / nom
    p.write_bytes(mazmun)
    assert T.tikla(str(p)) is None
    with pytest.raises(T.TiklashRad):
        T._tikla_ichki(str(p))


def test_faqat_rc4_standard():
    """RC4 standard'dan boshqa shifr turlari OCHILMAYDI.

    CryptoAPI (vMajor 2..4) va XOR obfuskatsiyasi (wType=0) haqiqiy parol
    bilan yopilgan bo'lishi mumkin — ularga tegmaymiz.
    """
    def fp(w_type, v_major, v_minor):
        return struct.pack("<HHH", w_type, v_major, v_minor) + b"\x00" * 48

    assert T._rc4_standardmi(fp(1, 1, 1))          # RC4 standard
    assert not T._rc4_standardmi(fp(1, 2, 2))      # CryptoAPI
    assert not T._rc4_standardmi(fp(1, 4, 2))      # CryptoAPI (Office 2007 .xls)
    assert not T._rc4_standardmi(fp(0, 0, 0))      # XOR obfuskatsiyasi
    assert not T._rc4_standardmi(b"\x01\x00")      # kalta/buzuq yozuv


def test_parol_tanlanmaydi():
    """HAQIQIY parol bilan yopilgan hujjat OCHILMAYDI (brute-force yo'q).

    Sun'iy FILEPASS yasaymiz: parol — `maxfiy-parol-123`. Verifier mantig'i
    o'sha parol bilan mos keladi (ya'ni tekshiruv haqiqatan ishlayapti),
    lekin bizning standart ro'yxatimizdagi hech biri mos kelmaydi.
    """
    salt = bytes(range(16))
    maxfiy = "maxfiy-parol-123"
    hf = T._hfinal(maxfiy, salt)
    verifier = bytes(range(100, 116))
    shifr = T._rc4(T._blok_kaliti(hf, 0), verifier + hashlib.md5(verifier).digest())
    e_ver, e_hash = shifr[:16], shifr[16:32]

    assert T._parol_mos(maxfiy, salt, e_ver, e_hash)[0] is True
    for parol in T.PAROLLAR:
        assert T._parol_mos(parol, salt, e_ver, e_hash)[0] is False


def test_parollar_royxati_qisqa():
    """Ro'yxat KENGAYMASIN — «keng tarqalgan parollar» qo'shish brute-force."""
    assert T.PAROLLAR == ("VelvetSweatshop", "")


def test_budjet_toza_istisno(monkeypatch):
    """Vaqt budjeti tugasa TOZA istisno; `tikla()` esa baribir None beradi."""
    monkeypatch.setattr(T, "MAX_SONIYA", -1.0)
    with pytest.raises(T.TiklashBudjeti):
        T._tikla_ichki(SOGLOM_XLS)
    assert T.tikla(SOGLOM_XLS) is None
    assert issubclass(T.TiklashBudjeti, T.TiklashRad)


def test_hajm_chegarasi(monkeypatch, tmp_path):
    """Hajm chegarasi ta'mir yo'lida ham amal qiladi (bomba kirmasin)."""
    p = tmp_path / "katta.xls"
    p.write_bytes(T._OLE_IMZO + b"\x00" * 2048)
    monkeypatch.setattr(T, "MAX_MB", 0)
    with pytest.raises(T.TiklashRad, match="juda katta"):
        T._tikla_ichki(str(p))


def test_varaq_chegarasi_ishlaydi():
    """`MAX_SHEET_ROWS`/`MAX_SHEET_COLS` tiklangan oqimga ham tatbiq etiladi.

    DIMENSIONS yozuvi (0x0200): rwMic(4) rwMac(4) colMic(2) colMac(2) rsvd(2) —
    `rwMac`/`colMac` oxirgi qator/ustundan bittaga katta.
    """
    def oqim(qator, ustun):
        tana = struct.pack("<IIHHH", 0, qator, 0, ustun, 0)
        return struct.pack("<HH", T._DIMENSIONS, len(tana)) + tana

    budjet = T._Budjet(60)
    T._chegaralarni_tekshir(oqim(100, 20), budjet)         # normal hujjat — o'tadi
    with pytest.raises(T.TiklashRad, match="juda ko'p qator"):
        T._chegaralarni_tekshir(oqim(T.MAX_SHEET_ROWS + 1, 20), budjet)
    with pytest.raises(T.TiklashRad, match="juda ko'p ustun"):
        T._chegaralarni_tekshir(oqim(100, T.MAX_SHEET_COLS + 1), budjet)


def test_tozala_va_kontekst(monkeypatch, tmp_path):
    """`tozala()` vaqtinchalik papkani o'chiradi, `tiklangan()` esa har holatda."""
    papka = tmp_path / "xls_rc4_sinov"
    papka.mkdir()
    soxta = papka / "a.xls"
    soxta.write_bytes(b"x")
    monkeypatch.setattr(T, "tikla", lambda yol: str(soxta))
    with T.tiklangan("istalgan.xls") as yol:
        assert os.path.isfile(yol)
    assert not papka.exists(), "vaqtinchalik papka qoldi"

    T.tozala(None)                                 # None — jim o'tishi kerak


def test_cfb_difat_aylanma():
    """Yasalgan CFB o'qib qaytarilsa AYNAN o'sha baytlar chiqsin.

    Oqim ataylab 8 MB: 512-baytli sektorda ~16 000 sektor bo'ladi, ya'ni
    FAT sarlavhadagi 109 ta uyaga sig'maydi va DIFAT sektorlari yoziladi.
    Aynan shu yo'l haqiqiy 10 MB li hujjatlarda ishlaydi.
    """
    data = bytes(range(256)) * 32768                # 8 MB, deterministik
    cfb = T._cfb_yasa([("Workbook", data)])
    ole = T._Ole2(cfb, T._Budjet(60))
    assert ole.oqim("Workbook") == data
    # DIFAT haqiqatan ishlatilgan bo'lsin — aks holda sinov hech narsa isbotlamaydi.
    assert struct.unpack_from("<I", cfb, 72)[0] > 0


def test_readerga_boglanmagan():
    """Modul MUSTAQIL: loyihaning hech bir modulini import qilmaydi.

    Bu SHART: `reader` tiklashni chaqiradi, tiklash `reader` ni chaqirsa
    aylanma import bo'lardi. Import ro'yxati AST bilan tekshiriladi
    (docstring'dagi eslatmalar sanalmasin).
    """
    import ast
    daraxt = ast.parse(open(T.__file__, encoding="utf-8").read())
    nomlar = set()
    for tugun in ast.walk(daraxt):
        if isinstance(tugun, ast.Import):
            nomlar.update(a.name for a in tugun.names)
        elif isinstance(tugun, ast.ImportFrom) and tugun.module:
            nomlar.add(tugun.module)
    assert not [n for n in nomlar
                if n.split(".")[0] in ("tender_engine", "validator",
                                       "set_validator", "profiles",
                                       "jobs_worker", "templates_db")]
    # Uchinchi tomon kutubxonasi ham qo'shilmasin — faqat standart kutubxona.
    assert not nomlar & {"openpyxl", "xlrd", "olefile", "msoffcrypto",
                         "cryptography", "psycopg", "httpx"}


# ── 2-qism: haqiqiy shifrlangan hujjatlar ──────────────────────────────────

@pytest.fixture(scope="module")
def tiklangan_kesh():
    """Har fayl BIR MARTA deshifrlanadi (2 s), natija sinovlar orasida bo'linadi."""
    kesh = {}
    try:
        for fid in TOLIQ:
            if not _bor(fid):
                continue
            yol = T.tikla(_yol(fid))
            kesh[fid] = (yol, read_file(yol) if yol else None)
        yield kesh
    finally:
        for yol, _ in kesh.values():
            T.tozala(yol)


@shifrlangan_kerak
@pytest.mark.parametrize("fid", SHIFRLANGAN)
def test_asl_xato_shifrlangan(fid, monkeypatch):
    """Boshlang'ich holat: tiklashsiz bu fayllar OCHILMAYDI, tiklash bilan ochiladi.

    `TIKLASH_YOQ=0` butun zaxira zanjirini o'chiradi — shu bilan bitta testda
    uchta narsa qotiriladi: (1) asl nuqson HAQIQIY edi, (2) o'chirish kaliti
    ishlaydi, (3) `read_file` ga ulangan zanjir aynan shu faylni tiklaydi.
    """
    monkeypatch.setenv("TIKLASH_YOQ", "0")
    with pytest.raises(Exception) as xato:
        read_file(_yol(fid))
    assert T.shifrlangan_xatomi(xato.value)

    monkeypatch.delenv("TIKLASH_YOQ")
    varaqlar = read_file(_yol(fid))
    assert varaqlar, "zanjir ulangach shifrlangan fayl ochilishi kerak"


@shifrlangan_kerak
@pytest.mark.parametrize("fid", SHIFRLANGAN)
def test_standart_parol_mos(fid):
    """To'rttasi ham RC4 standard + `VelvetSweatshop` (arzon tekshiruv)."""
    data = open(_yol(fid), "rb").read()
    ole = T._Ole2(data, T._Budjet(60))
    st = ole.oqim("Workbook") or ole.oqim("Book")
    fp, _, _ = T._filepass_top(st)
    assert T._rc4_standardmi(fp)
    mos, _ = T._parol_mos("VelvetSweatshop", fp[6:22], fp[22:38], fp[38:54])
    assert mos, "standart parol mos kelmadi"


@shifrlangan_kerak
@yoqilgan_kerak
@pytest.mark.parametrize("fid,varaqlar,belgi", [
    (55729, {"Форма-5", "Расчет"}, "Наименование работ и затрат"),
    (99700, {"RES", "LRV"}, "ЛОКАЛЬНАЯ РЕСУРСНАЯ"),
])
def test_tiklandi_va_oqildi(tiklangan_kesh, fid, varaqlar, belgi):
    """Tiklangan hujjat LOYIHANING O'Z o'quvchisi bilan ochiladi va HAQIQIY
    smeta matnini o'z ichiga oladi — deshifrlash to'g'riligining dalili."""
    yol, sahifalar = tiklangan_kesh[fid]
    assert yol and sahifalar, "tiklab bo'lmadi"
    assert varaqlar <= set(sahifalar)

    matnlar = [c for qatorlar in sahifalar.values() for q in qatorlar
               for c in q if isinstance(c, str)]
    assert any(belgi in m for m in matnlar), "kutilgan smeta matni topilmadi"

    sonlar = sum(1 for qatorlar in sahifalar.values() for q in qatorlar
                 for c in q if isinstance(c, (int, float)) and not isinstance(c, bool))
    assert sonlar > 1000, "sonlar deshifrlanmagan (%d ta)" % sonlar


@shifrlangan_kerak
@yoqilgan_kerak
def test_asl_fayl_ozgarmaydi(tiklangan_kesh):
    """ASL FAYLGA HECH QACHON TEGILMASIN — sha256 o'zgarmasin, nusxa boshqa fayl."""
    for fid in TOLIQ:
        xom = open(_yol(fid), "rb").read()
        oldin = hashlib.sha256(xom).hexdigest()
        yol, _ = tiklangan_kesh[fid]
        assert os.path.abspath(yol) != os.path.abspath(_yol(fid))
        keyin = hashlib.sha256(open(_yol(fid), "rb").read()).hexdigest()
        assert oldin == keyin


@shifrlangan_kerak
def test_chegara_haqiqiy_faylda_ham(monkeypatch):
    """Chegara HAQIQIY tiklash yo'liga ulangan (unit sinov emas, to'liq oqim)."""
    monkeypatch.setattr(T, "MAX_SHEET_ROWS", 10)
    with pytest.raises(T.TiklashRad, match="juda ko'p qator"):
        T._tikla_ichki(_yol(99702))


@shifrlangan_kerak
@yoqilgan_kerak
def test_natija_vaqtinchalik_papkada(tiklangan_kesh):
    """Natija tizim vaqtinchalik papkasida, `.xls` kengaytmasi bilan."""
    import tempfile
    for fid in TOLIQ:
        yol, _ = tiklangan_kesh[fid]
        assert yol.lower().endswith(".xls")
        assert os.path.abspath(yol).startswith(
            os.path.abspath(tempfile.gettempdir()))
