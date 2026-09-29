# -*- coding: utf-8 -*-
"""XLSB (BIFF12 — Excel'ning IKKILIK ish kitobi) o'quvchisi.

MUAMMO (2026-09-04 auditi, id=111388): fayl `.xlsx` nomi bilan keladi,
ichi esa XLSB — `[Content_Types].xml` da
«application/vnd.ms-excel.sheet.binary.macroEnabled.main», ichida
`xl/workbook.bin` + `xl/worksheets/sheetN.bin`. Zip SOG'LOM, hujjat
to'g'ri va to'ldirilgan; faqat openpyxl bu formatni tanimaydi va
«File contains no valid workbook part» bilan yiqiladi. Natijada halol
hujjat «Faylni ochib bo'lmadi» deb RAD etilardi — bu loyihaning bosh
qoidasiga zid: «NOO'RIN RAD NOO'RIN QABULDAN YOMONROQ».

Ya'ni bu BUZUQ FAYL EMAS, BOSHQA FORMAT. Shuning uchun bu yerda «ta'mir»
ham yo'q — formatni to'g'ridan-to'g'ri, uning o'z qoidalari bo'yicha
o'qiymiz. `pyxlsb` o'rnatilmagan va O'RNATILMAYDI: BIFF12 yozuvlari
standart kutubxona (`zipfile` + `struct`) bilan o'qiladi.

CHAQIRISH TARTIBI (MUHIM). `reader.py` bu modulni FAQAT XATO YO'LIDA —
openpyxl yiqilgandan keyin — chaqiradi. Sog'lom `.xlsx` bu yerga umuman
kirmasligi kerak; `xlsb_faylmi()` darvozasi ham shuning uchun arzon va
qat'iy (zip ichida `xl/workbook.bin` bormi). Buni
`tests/test_xlsb.py` dagi ekvivalentlik testlari qotiradi.

ASL FAYLGA TEGILMAYDI: fayl faqat O'QILADI, vaqtinchalik nusxa ham
yasalmaydi — zip qismlari xotirada ochiladi.

CHEKLOV (bilib turib qabul qilingan): sana kataklari xom seriya raqami
bo'lib qaytadi, `datetime` ga aylantirilmaydi — buning uchun `styles.bin`
dagi raqam formatlarini ham yechish kerak bo'lardi. Tender hujjatlarida
hukm narx ustunlariga qarab chiqadi, sanalar unga kirmaydi.
"""

import os
import re
import struct
import time
import warnings
import zipfile


# ── Chegaralar — `reader.py` dagilar bilan BIR XIL nom va standart ────────
# Ataylab TAKRORLANGAN (import qilinmagan): `reader` bu modulni chaqiradi,
# teskari import aylanma bog'liqlik yasardi.
MAX_MERGE_CELLS = int(os.environ.get("MAX_MERGE_CELLS", "50000"))
MAX_SHEET_ROWS = int(os.environ.get("MAX_SHEET_ROWS", "200000"))
MAX_SHEET_COLS = int(os.environ.get("MAX_SHEET_COLS", "2000"))
MAX_XLSX_PART_BYTES = int(os.environ.get("MAX_XLSX_PART_BYTES", str(200 * 1024 * 1024)))

# Yig'iladigan jadval hajmi. E'lon qilingan o'lcham katta bo'lsa ham
# `balandlik x kenglik` ta katakli ro'yxat yasaymiz — chegarasiz qoldirilsa
# 200 000 x 2 000 = 400 mln katak xotirani tugatardi.
MAX_XLSB_KATAK = int(os.environ.get("MAX_XLSB_KATAK", str(8_000_000)))

# Vaqt/tugun budjeti. 2026-09-02 dagi O(qator^2) livelock (bitta fayl
# 60 daqiqa ishlab, 15 daqiqalik lease tugab, 6 ta worker ketma-ket shu
# tuzoqqa tushgan) shundan keyin QAT'IY budjetsiz qidiruv yozilmaydi.
XLSB_BUDJET_SEK = float(os.environ.get("XLSB_BUDJET_SEK", "60"))
XLSB_MAX_YOZUV = int(os.environ.get("XLSB_MAX_YOZUV", str(20_000_000)))

# `[Content_Types].xml` — darvoza uchun o'qiladi, shuning uchun alohida
# (kichik) chegara: darvoza hech qachon 200 MB o'qib qo'ymasin.
_CT_MAX_BYTES = 4 * 1024 * 1024


class XlsbXato(ValueError):
    """XLSB o'qib bo'lmadi (format kutilganidek emas)."""


class XlsbJudaKatta(XlsbXato):
    """Fayl xavfsiz o'qish chegaralaridan oshdi (zararli yoki nosog'lom)."""


class XlsbBudjetTugadi(XlsbXato):
    """Vaqt yoki yozuv budjeti tugadi — o'qish to'xtatildi."""


# ── BIFF12 yozuv turlari (MS-XLSB) ────────────────────────────────────────
_ROW_HDR = 0        # BrtRowHdr        — joriy qator raqami
_BLANK = 1          # BrtCellBlank     — qiymatsiz, faqat uslubli katak
_RK = 2             # BrtCellRk        — siqilgan son
_ERROR = 3          # BrtCellError
_BOOL = 4           # BrtCellBool
_REAL = 5           # BrtCellReal      — IEEE-754 double
_ST = 6             # BrtCellSt        — ichki matn
_ISST = 7           # BrtCellIsst      — umumiy lug'atga (SST) havola
_FMLA_ST = 8        # BrtFmlaString    — formula, natijasi matn
_FMLA_NUM = 9       # BrtFmlaNum       — formula, natijasi son
_FMLA_BOOL = 10     # BrtFmlaBool
_FMLA_ERROR = 11    # BrtFmlaError
_SST_ITEM = 19      # BrtSSTItem       — sharedStrings.bin dagi bitta satr
_BUNDLE_SH = 156    # BrtBundleSh      — workbook.bin dagi varaq yozuvi
_MERGE_CELL = 176   # BrtMergeCell     — birlashtirilgan diapazon

# Qiymat OLIB KELADIGAN yozuvlar (BrtCellBlank kirmaydi — u faqat kenglikka
# ta'sir qiladi, xuddi `.xlsx` da uslubli bo'sh katak kabi).
_KATAK_YOZUVLARI = frozenset((_RK, _ERROR, _BOOL, _REAL, _ST, _ISST,
                              _FMLA_ST, _FMLA_NUM, _FMLA_BOOL, _FMLA_ERROR))
_BARCHA_KATAK = _KATAK_YOZUVLARI | {_BLANK}

# BrtCellError / BrtFmlaError xato kodlari. `.xls` yo'li ham xato katakni
# MATN bilan qaytaradi (CLAUDE.md, «#VALUE! siyosati»): to'liq buzuq ustun
# keyin «bo'sh» deb topilishi uchun.
_XATO_MATNI = {
    0x00: "#NULL!", 0x07: "#DIV/0!", 0x0F: "#VALUE!", 0x17: "#REF!",
    0x1D: "#NAME?", 0x24: "#NUM!", 0x2A: "#N/A", 0x2B: "#GETTING_DATA",
}

_REL_RE = re.compile(r"<Relationship\b[^>]*>")
_REL_ID_RE = re.compile(r'\bId="([^"]+)"')
_REL_TARGET_RE = re.compile(r'\bTarget="([^"]+)"')


# ── O'chirish kaliti ──────────────────────────────────────────────────────

def yoqilganmi() -> bool:
    """Modul yoqilganmi (standart: YOQIQ).

    `TIKLASH_YOQ=0` — barcha tiklash yo'llarini birdan o'chiradi,
    `TIKLASH_XLSB=0` — faqat shu modulni. Muhit HAR CHAQIRUVDA o'qiladi:
    xizmatni qayta yoqmasdan o'chirish imkoni qolsin.
    """
    return (os.environ.get("TIKLASH_YOQ", "1") != "0"
            and os.environ.get("TIKLASH_XLSB", "1") != "0")


# ── Darvoza: fayl haqiqatan XLSB mi ───────────────────────────────────────

def xlsb_faylmi(yol: str) -> bool:
    """`.xlsx`/`.xls` nomi bilan kelgan fayl aslida XLSB mi.

    Nomga EMAS, mazmunga qaraydi: XLSB ham OPC (zip) paketi, lekin ish
    kitobi qismi XML emas, ikkilik — `xl/workbook.bin`. Haqiqiy `.xlsx`
    da bunday qism hech qachon bo'lmaydi, shuning uchun darvoza sog'lom
    faylni bu yo'lga BURMAYDI.

    Zaxira alomat — `[Content_Types].xml` dagi «ms-excel.sheet.binary»
    turi: ish kitobi qismi nostandart yo'lga qo'yilgan bo'lsa ham
    tanilsin.
    """
    try:
        with zipfile.ZipFile(yol) as z:
            nomlar = z.namelist()
            if "xl/workbook.bin" in nomlar:
                return True
            if "[Content_Types].xml" in nomlar:
                info = z.getinfo("[Content_Types].xml")
                if info.file_size <= _CT_MAX_BYTES:
                    if b"ms-excel.sheet.binary" in z.read("[Content_Types].xml"):
                        return True
    except (zipfile.BadZipFile, OSError, KeyError, ValueError, RuntimeError):
        return False        # zip emas yoki o'qilmadi — bizning ishimiz emas
    return False


# ── Budjet ────────────────────────────────────────────────────────────────

class _Budjet:
    """Vaqt va yozuv soni chegarasi.

    Vaqt har yozuvda emas, har `_QADAM` yozuvda o'lchanadi: `monotonic()`
    ni millionlab marta chaqirish o'qishning o'zidan qimmatga tushardi.
    """

    _QADAM = 4096

    def __init__(self, sek: float, max_yozuv: int):
        self._tugash = time.monotonic() + sek if sek > 0 else None
        self._qoldi = max_yozuv
        self._keyingi = self._QADAM

    def tik(self, nechta: int = 1) -> None:
        self._qoldi -= nechta
        if self._qoldi < 0:
            raise XlsbBudjetTugadi(
                "XLSB yozuvlar soni chegarasidan oshdi (XLSB_MAX_YOZUV)")
        self._keyingi -= nechta
        if self._keyingi <= 0:
            self._keyingi = self._QADAM
            self.vaqt_tekshir()

    def vaqt_tekshir(self) -> None:
        """Vaqtni SHU ZAHOTI o'lchaydi — qism chegaralarida chaqiriladi.

        Faqat `_QADAM` yozuvda tekshirish yetarli emas: har biri chegaradan
        kam yozuvli o'nlab varaqli fayl vaqt nazoratidan butunlay
        chetlab o'tardi.
        """
        if self._tugash is not None and time.monotonic() > self._tugash:
            raise XlsbBudjetTugadi(
                "XLSB o'qish vaqt budjetidan oshdi (XLSB_BUDJET_SEK)")


# ── BIFF12 ibtidoiy o'quvchilari ──────────────────────────────────────────

def _yozuvlar(buf: bytes, budjet: "_Budjet"):
    """BIFF12 oqimidan `(rid, payload)` juftlarini beradi.

    Sarlavha: `rid` — 1-2 baytli varint (har baytda 7 bit), keyin uzunlik —
    1-4 baytli varint.

    Oqim kutilmaganda tugasa (uzunlik buferdan chiqib ketsa) JIM to'xtaymiz:
    shu joygacha o'qilgani baribir foydali. Butun hujjatni rad etishdan
    ko'ra qisman o'qilgani afzal — «noo'rin rad» qoidasi.
    """
    i, n = 0, len(buf)
    while i < n:
        budjet.tik()
        b = buf[i]
        i += 1
        if b & 0x80:
            if i >= n:
                return
            rid = (b & 0x7F) | ((buf[i] & 0x7F) << 7)
            i += 1
        else:
            rid = b
        uzunlik, siljish = 0, 0
        for _ in range(4):          # uzunlik varinti eng ko'pi 4 bayt
            if i >= n:
                return
            c = buf[i]
            i += 1
            uzunlik |= (c & 0x7F) << siljish
            siljish += 7
            if not (c & 0x80):
                break
        if uzunlik < 0 or i + uzunlik > n:
            return
        yield rid, buf[i:i + uzunlik]
        i += uzunlik


def _wstr(buf: bytes, off: int):
    """XLWideString: `cch` (4 bayt) + UTF-16LE. -> (matn, keyingi_ofset)."""
    if off + 4 > len(buf):
        return None, off + 4
    cch = struct.unpack_from("<I", buf, off)[0]
    off += 4
    if cch == 0xFFFFFFFF:           # NULL satr
        return None, off
    oxiri = off + 2 * cch
    if oxiri > len(buf):            # kesilgan satr — boridan foydalanamiz
        oxiri = len(buf) - (len(buf) - off) % 2
    return buf[off:oxiri].decode("utf-16-le", "replace"), off + 2 * cch


def _rk_qiymat(rk: int) -> float:
    """RK — Excel'ning siqilgan son formati.

    Ikkita bayroq bitidan tashkil topgan:
      • bit 0 (`fX100`) — qiymat 100 ga bo'linishi kerak;
      • bit 1 (`fInt`)  — qolgan 30 bit ISHORALI butun son, aks holda ular
        IEEE-754 double'ning YUQORI 30 biti (quyi 34 bit nol deb olinadi).

    Qayta yig'ish ISHORASIZ (`<Q`) qilinadi: manfiy sonda eng yuqori
    (ishora) bit yoqilgan bo'ladi va `<q` bilan «argument out of range»
    beradi — ya'ni bitta manfiy narx butun hujjatni o'qilmas qilardi.
    """
    if rk & 0x02:                                   # fInt
        v = rk >> 2
        if v & 0x20000000:                          # 30-bitli ishorali son
            v -= 0x40000000
        v = float(v)
    else:
        v = struct.unpack("<d", struct.pack("<Q", (rk & 0xFFFFFFFC) << 32))[0]
    return v / 100.0 if rk & 0x01 else v


def _son(v: float):
    """Butun qiymatni `int` qilib qaytaradi — openpyxl bilan bir xil bo'lsin.

    `.xlsx` da `<v>1</v>` openpyxl'da `int`, `<v>1.5</v>` esa `float` bo'ladi.
    XLSB — XLSX ning ikkilik egizagi va `reader` uni `.xlsx` tarmog'idan
    chaqiradi, shuning uchun tur ham o'sha yo'ldagidek bo'lgani ma'qul.
    Juda katta sonlar `float` bo'lib qoladi (aniqlik baribir yo'qolgan).
    """
    if -1e15 < v < 1e15 and v == int(v):
        return int(v)
    return v


# ── Ish kitobi qismlari ───────────────────────────────────────────────────

def _qism_oqi(z: zipfile.ZipFile, nom: str) -> bytes:
    """Zip qismini chegara bilan o'qiydi (zip-bombadan himoya)."""
    info = z.getinfo(nom)
    if info.file_size > MAX_XLSX_PART_BYTES:
        raise XlsbJudaKatta(
            f"«{nom}» ochilganda juda katta ({info.file_size // 1048576} MB) — "
            f"zip bomba bo'lishi mumkin")
    with z.open(info) as fh:
        return fh.read(MAX_XLSX_PART_BYTES)


def _sst_oqi(z: zipfile.ZipFile, budjet: "_Budjet") -> list:
    """`xl/sharedStrings.bin` — umumiy matn lug'ati.

    `BrtSSTItem` = bayroq bayti (1) + XLWideString. Bayroqdan keyin boy
    matn (rich text) bo'laklari kelishi mumkin — ular bizga kerak emas.
    """
    if "xl/sharedStrings.bin" not in z.namelist():
        return []
    sst = []
    for rid, pl in _yozuvlar(_qism_oqi(z, "xl/sharedStrings.bin"), budjet):
        if rid == _SST_ITEM:
            matn, _ = _wstr(pl, 1)
            sst.append(matn or "")
    return sst


def _rels_xaritasi(z: zipfile.ZipFile) -> dict:
    """`xl/_rels/workbook.bin.rels` -> {relId: nishon_yo'li}."""
    nom = "xl/_rels/workbook.bin.rels"
    if nom not in z.namelist():
        return {}
    xom = _qism_oqi(z, nom).decode("utf-8", "replace")
    xarita = {}
    for teg in _REL_RE.findall(xom):
        i = _REL_ID_RE.search(teg)
        t = _REL_TARGET_RE.search(teg)
        if i and t:
            xarita[i.group(1)] = t.group(1)
    return xarita


def _yol_yechish(nishon: str, nomlar: set):
    """Rels nishonini zip ichidagi haqiqiy yo'lga aylantiradi.

    Nishon `xl/` ga NISBATAN yoziladi («worksheets/sheet1.bin»), lekin
    ba'zi generatorlar mutlaq («/xl/worksheets/sheet1.bin») yoki to'liq
    yo'l yozadi — uchalasi ham qabul qilinadi.
    """
    if not nishon:
        return None
    toza = nishon.lstrip("/")
    for nomzod in (toza, "xl/" + toza):
        if nomzod in nomlar:
            return nomzod
    return None


def _varaqlar(z: zipfile.ZipFile, budjet: "_Budjet") -> list:
    """`workbook.bin` dan [(varaq_nomi, zip_yo'li), ...] — TARTIBI SAQLANADI.

    `BrtBundleSh` = hsState(4) + iTabID(4) + relId (XLWideString) +
    nomi (XLWideString).
    """
    if "xl/workbook.bin" not in z.namelist():
        raise XlsbXato("XLSB paketida «xl/workbook.bin» yo'q")
    nomlar = set(z.namelist())
    xarita = _rels_xaritasi(z)

    juftlar, korilgan = [], set()
    for rid, pl in _yozuvlar(_qism_oqi(z, "xl/workbook.bin"), budjet):
        if rid != _BUNDLE_SH:
            continue
        rel, off = _wstr(pl, 8)
        nom, _ = _wstr(pl, off)
        yol = _yol_yechish(xarita.get(rel or "", ""), nomlar)
        if yol is None or yol in korilgan:
            continue
        korilgan.add(yol)
        juftlar.append((nom or f"Sheet{len(juftlar) + 1}", yol))

    if not juftlar:
        # Rels buzuq yoki BrtBundleSh o'qilmadi — hujjatni shu sabab bilan
        # rad etmaymiz, varaqlarni zipdan to'g'ridan-to'g'ri olamiz.
        for nom in sorted(n for n in nomlar
                          if n.startswith("xl/worksheets/sheet")
                          and n.endswith(".bin")):
            juftlar.append((os.path.splitext(os.path.basename(nom))[0], nom))
    if not juftlar:
        raise XlsbXato("XLSB paketida varaq topilmadi")
    return juftlar


# ── Varaqni o'qish ────────────────────────────────────────────────────────

def _varaq_oqi(xom: bytes, sst: list, budjet: "_Budjet"):
    """Bitta `sheetN.bin` -> (qiymatlar, birlashmalar, elon_qator, elon_ustun).

    `qiymatlar` — {qator: {ustun: qiymat}} (0 dan boshlanadi, faqat qiymati
    BOR kataklar). `elon_*` — bo'sh-uslubli kataklarni ham hisobga olgan
    o'lcham; `.xlsx` yo'lidagi `max_row`/`max_column` ga mos keladi.
    """
    qiymatlar, birlashmalar = {}, []
    elon_qator = elon_ustun = 0
    r = 0
    for rid, pl in _yozuvlar(xom, budjet):
        if rid == _ROW_HDR:
            if len(pl) >= 4:
                r = struct.unpack_from("<I", pl, 0)[0]
                if r + 1 > elon_qator:
                    elon_qator = r + 1
        elif rid in _BARCHA_KATAK:
            if len(pl) < 8:
                continue
            c = struct.unpack_from("<I", pl, 0)[0]
            if c + 1 > elon_ustun:
                elon_ustun = c + 1
            v = None
            if rid == _RK:
                if len(pl) >= 12:
                    v = _son(_rk_qiymat(struct.unpack_from("<I", pl, 8)[0]))
            elif rid in (_REAL, _FMLA_NUM):
                if len(pl) >= 16:
                    v = _son(struct.unpack_from("<d", pl, 8)[0])
            elif rid == _ISST:
                if len(pl) >= 12:
                    k = struct.unpack_from("<I", pl, 8)[0]
                    v = sst[k] if k < len(sst) else ""
            elif rid in (_ST, _FMLA_ST):
                v, _ = _wstr(pl, 8)
            elif rid in (_BOOL, _FMLA_BOOL):
                if len(pl) >= 9:
                    v = bool(pl[8])
            elif rid in (_ERROR, _FMLA_ERROR):
                if len(pl) >= 9:
                    v = _XATO_MATNI.get(pl[8], "#VALUE!")
            if v is not None:
                qiymatlar.setdefault(r, {})[c] = v
        elif rid == _MERGE_CELL and len(pl) >= 16:
            r1, r2, c1, c2 = struct.unpack_from("<4I", pl, 0)
            if r1 > r2 or c1 > c2:
                continue
            birlashmalar.append((r1, r2, c1, c2))
            if r2 + 1 > elon_qator:
                elon_qator = r2 + 1
            if c2 + 1 > elon_ustun:
                elon_ustun = c2 + 1
    return qiymatlar, birlashmalar, elon_qator, elon_ustun


def _birlashmalarni_ochish(qiymatlar: dict, birlashmalar: list, varaq: str) -> None:
    """Birlashtirilgan diapazonning HAR katagiga chap-yuqori qiymatini yozadi.

    `.xlsx` yo'li ham shunday qiladi (`reader._read_xlsx`) — sarlavha
    topish mantig'i shunga tayanadi.

    `MAX_MERGE_CELLS` dan katta diapazon OCHILMAYDI, lekin fayl RAD ham
    ETILMAYDI: qiymatlarning o'zi baribir o'qilgan, ochilmagani faqat
    takror nusxalar. Chegara bu yerda xotira uchun, hukm uchun emas.
    """
    jami = tashlandi = 0
    for r1, r2, c1, c2 in birlashmalar:
        kataklar = (r2 - r1 + 1) * (c2 - c1 + 1)
        if kataklar > MAX_MERGE_CELLS or jami + kataklar > MAX_MERGE_CELLS:
            tashlandi += 1
            continue
        qiymat = qiymatlar.get(r1, {}).get(c1)
        if qiymat is None:
            continue
        jami += kataklar
        for r in range(r1, r2 + 1):
            qator = qiymatlar.setdefault(r, {})
            for c in range(c1, c2 + 1):
                qator[c] = qiymat
    if tashlandi:
        # Bitta ogohlantirish — har diapazon uchun emas: chegaraga urilgan
        # faylda minglab birlashma bo'lishi mumkin.
        warnings.warn(f"«{varaq}»: {tashlandi} ta birlashma ochilmadi "
                      f"(chegara {MAX_MERGE_CELLS:,} katak)")


def _olcham(qiymatlar: dict, elon_qator: int, elon_ustun: int, varaq: str):
    """Yig'iladigan jadval o'lchamini tanlaydi: (balandlik, kenglik).

    `reader._read_xlsx_ichki` bilan bir xil mantiq: odatda E'LON QILINGAN
    o'lcham olinadi (bo'sh-uslubli kataklar ham `.xlsx` da grid'ga kiradi),
    lekin u chegaradan oshsa — QIYMATI BOR maydonga qirqiladi. Sabab
    o'sha: butun varaqqa format berilgan hujjatda e'lon qilingan o'lcham
    yolg'on bo'ladi, uni rad etish uchun asos yo'q.
    """
    haqiqiy_qator = max(qiymatlar) + 1 if qiymatlar else 0
    haqiqiy_ustun = max((max(q) for q in qiymatlar.values()), default=-1) + 1

    # `max(..., haqiqiy_*)` — qiymat ELON QILINGAN maydondan tashqarida
    # qolib ketmasin: nostandart generatorda `BrtRowHdr` siz katak ham
    # uchraydi, va u holda e'lon qilingan balandlik 0 bo'lib, o'qilgan
    # ma'lumot jim yo'qolardi.
    balandlik = max(elon_qator if elon_qator <= MAX_SHEET_ROWS else 0,
                    haqiqiy_qator)
    kenglik = max(elon_ustun if elon_ustun <= MAX_SHEET_COLS else 0,
                  haqiqiy_ustun)
    if balandlik * kenglik > MAX_XLSB_KATAK:
        balandlik, kenglik = haqiqiy_qator, haqiqiy_ustun

    if balandlik > MAX_SHEET_ROWS:
        raise XlsbJudaKatta(
            f"«{varaq}» varag'ida juda ko'p qator "
            f"({balandlik}; chegara {MAX_SHEET_ROWS})")
    kenglik = min(kenglik, MAX_SHEET_COLS)
    if balandlik * kenglik > MAX_XLSB_KATAK:
        raise XlsbJudaKatta(
            f"«{varaq}» varag'i juda katta ({balandlik} x {kenglik} katak; "
            f"chegara {MAX_XLSB_KATAK:,})")
    return balandlik, kenglik


def _noyob(nom: str, band: set) -> str:
    """Varaq nomlari takrorlansa lug'atda biri yo'qolmasin."""
    if nom not in band:
        return nom
    i = 2
    while f"{nom} ({i})" in band:
        i += 1
    return f"{nom} ({i})"


# ── Ommaviy API ───────────────────────────────────────────────────────────

def oqi(yol: str, budjet_sek: float = None, max_yozuv: int = None) -> dict:
    """XLSB faylni o'qiydi -> {varaq_nomi: [[qiymat, ...], ...]}.

    Qaytish shakli `reader.read_file` bilan AYNAN bir xil: har varaq —
    qatorlar ro'yxati, har qator — oddiy Python ro'yxati, bo'sh katak
    `None`. Birlashmalar ochilgan, xato kataklar «#VALUE!» kabi matn.

    XlsbXato — format kutilganidek emas; XlsbJudaKatta — chegaradan oshdi;
    XlsbBudjetTugadi — vaqt/yozuv budjeti tugadi.
    """
    budjet = _Budjet(XLSB_BUDJET_SEK if budjet_sek is None else budjet_sek,
                     XLSB_MAX_YOZUV if max_yozuv is None else max_yozuv)
    try:
        z = zipfile.ZipFile(yol)
    except (zipfile.BadZipFile, OSError) as xato:
        raise XlsbXato(f"XLSB paketi ochilmadi: {xato}")

    with z:
        sst = _sst_oqi(z, budjet)
        natija = {}
        for nom, qism in _varaqlar(z, budjet):
            budjet.vaqt_tekshir()
            qiymatlar, birlashmalar, elon_q, elon_u = _varaq_oqi(
                _qism_oqi(z, qism), sst, budjet)
            _birlashmalarni_ochish(qiymatlar, birlashmalar, nom)
            balandlik, kenglik = _olcham(qiymatlar, elon_q, elon_u, nom)
            jadval = [[None] * kenglik for _ in range(balandlik)]
            for r, qator in qiymatlar.items():
                if r >= balandlik:
                    continue
                nishon = jadval[r]
                for c, v in qator.items():
                    if c < kenglik:
                        nishon[c] = v
            natija[_noyob(nom, set(natija))] = jadval
    return natija


def tikla(yol: str, asl_xato: BaseException = None, budjet_sek: float = None,
          max_yozuv: int = None):
    """`reader.py` uchun kirish nuqtasi — FAQAT XATO YO'LIDAN chaqiriladi.

    Qaytaradi: varaqlar lug'ati, yoki `None` — «bu mening ishim emas»
    (modul o'chirilgan yoki fayl XLSB emas). Bunda chaqiruvchi o'z
    xatosini o'zi ko'taraveradi.

    O'qish BOSHLANIB, keyin yiqilsa — ASL XATO qayta ko'tariladi
    (`asl_xato` berilgan bo'lsa). Bu ataylab: ishtirokchiga ko'rinadigan
    izoh tiklash urinishi tufayli O'ZGARMASLIGI kerak; XLSB xatosi esa
    `__cause__` da nosozlik qidirish uchun saqlanadi.
    """
    if not yoqilganmi() or not xlsb_faylmi(yol):
        return None
    try:
        return oqi(yol, budjet_sek=budjet_sek, max_yozuv=max_yozuv)
    except Exception as xato:               # noqa: BLE001 — ataylab keng
        warnings.warn(f"XLSB o'qilmadi ({os.path.basename(yol)}): "
                      f"{type(xato).__name__}: {xato}")
        if asl_xato is not None:
            raise asl_xato from xato
        raise
