# -*- coding: utf-8 -*-
"""Nol-bayt buzilishidan tiklash — «hamma 0x00 bayt 0x20 ga aylangan» .xlsx.

MUAMMO. Platformaga yuklangan ba'zi fayllar yo'lda MATN deb ko'chirilgan:
faylning HAR BIR 0x00 bayti 0x20 (probel) ga almashgan. `zipfile` bunday
faylni ochmaydi («Bad offset for central directory» / `BadZipFile`), natijada
to'ldirilgan HALOL hujjat «Faylni ochib bo'lmadi» deb RAD etilardi. Bu
BIZNING o'quvchimizning cheklovi edi, hujjatning aybi emas — loyihaning bosh
qoidasi esa «noo'rin rad noo'rin qabuldan yomonroq».

Buzilish o'lchov bilan tasdiqlangan: bu fayllarda 0x00 UMUMAN yo'q (yagona
uchramaydigan bayt), 0x20 chastotasi esa sog'lom nusxadagi 0x00+0x20
yig'indisiga teng.

USUL (uch bosqich):
  1. ZIP skeletini tiklash — sonli maydonlarda 0x20 ni 0x00 deb o'qish.
  2. Deflate oqimini orqaga qaytishli DFS bilan ochish: har 0x20 bayti
     ikki xil talqin qilinishi mumkin (0x20 yoki 0x00), shuning uchun har
     shunday nuqtada ikkala shox sinaladi. Noto'g'ri shoxlar inkremental
     kesish bilan darhol o'ladi: ASCII/UTF-8, XML tuzilmasi, OOXML teg va
     atribut lug'ati, katak manzillarining monotonligi.
  3. Yakuniy QABUL MEZONI — CRC32 va ochilgan o'lcham. Kesish qoidalari
     faqat qidiruvni qisqartiradi; noto'g'ri natijani ular emas, CRC rad
     etadi.

IKKI HAL QILUVCHI NOZIKLIK:
  a) CRC/o'lcham maydonlarining O'ZI ham buzilgan bo'lishi mumkin — ularni
     ko'r-ko'rona 0x20 -> 0x00 qilish TO'G'RI yechimni rad etadi (misol:
     113561 ning haqiqiy CRC si 0x20f4be07). Shuning uchun `_mos()` —
     bag'rikeng solishtiruv: xom == hisoblangan YOKI (xom == 0x20 va
     hisoblangan == 0x00). Bu hamon 8 bayt (o'lcham + CRC) mosligini
     talab qiladi, ya'ni yolg'on qabul ehtimoli ~2^-32.
  b) Ko'chirish paytida qo'shni ikki bayt O'RIN ALMASHISHI ham uchraydi
     (113533 da 105/106 pozitsiya). Shuning uchun DFS to'xtagan yozuvda
     qo'shni bayt transpozitsiyasi skanlanadi — CRC baribir hakam.

XAVFSIZLIK VA CHEGARALAR:
  • Modul FAQAT xato bo'lganda chaqiriladi (`nol_bayt_shubhasi` sog'lom
    faylda darhol False beradi — sog'lom fayl avvalgi yo'ldan aynan o'tadi).
  • Asl faylga HECH QACHON tegilmaydi: natija vaqtinchalik papkadagi YANGI
    .xlsx, chaqiruvchi uni tugagach o'chiradi (`_nomlarni_tozala` naqshi).
  • Qidiruv QAT'IY budjetli (tugun + soniya, `NOL_BAYT_TUGUN` /
    `NOL_BAYT_SONIYA`). Budjet tugasa toza `BudjetTugadi` istisnosi qaytadi —
    2026-09-02 dagi O(qator^2) livelock takrorlanmasin (bitta fayl 60 daqiqa
    ishlab, lease tugab, 6 worker ketma-ket shu tuzoqqa tushgan edi).
  • Reader'dagi zip-bomba chegaralari (MAX_XLSX_PART_BYTES va h.k.) tiklangan
    mazmunga HAM tatbiq etiladi — ta'mir orqali bomba kirib kelmasin.
  • SINTEZ qilinadigan qism (`sharedStrings` o'rinbosari) ham chegarali
    (`NOL_BAYT_MAX_SST`) va umumiy hajm budjetiga qo'shiladi: uning o'lchami
    hujjatdagi indeksdan kelib chiqadi, ya'ni bombani ta'mirning O'ZI
    yasashi mumkin edi.
  • `TIKLASH_YOQ=0` bilan butunlay o'chiriladi.

Modul MUSTAQIL: `reader.py` dan import qilmaydi (aylanma import bo'lmasin),
kerakli kichik yordamchilarni o'zida takrorlaydi.
"""

import binascii
import os
import re
import shutil
import struct
import tempfile
import time
import zipfile
import zlib


# ── Sozlamalar ───────────────────────────────────────────────────────────────
def _bayroq(nom, standart="1"):
    return os.environ.get(nom, standart).strip().lower() not in ("0", "false", "no", "")


#: Butun modulni o'chirish kaliti (standart: YOQIQ).
TIKLASH_YOQ = _bayroq("TIKLASH_YOQ")

#: DFS tugunlari va umumiy vaqt — BITTA fayl uchun. Ikkalasi ham qattiq
#: chegara: qaysi biri birinchi tugasa, `BudjetTugadi` ko'tariladi.
#: O'lchov (24 ta haqiqiy fayl, 3 noyob hujjat): eng og'iri 15 381 tugun /
#: 1.1 s. 200 000 — 13 baravar zaxira; tugun cheki DFS to'plamining
#: XOTIRASINI ham chegaralaydi (har tugun bitta `decompressobj` nusxasi).
#: Kesish kuchsiz bo'lgan hollarda 1.5 mln tugunda ham tugamaydi — shuning
#: uchun chek balandroq emas, VAQT esa ikkinchi mustaqil to'siq.
NOL_BAYT_TUGUN = int(os.environ.get("NOL_BAYT_TUGUN", "200000"))
NOL_BAYT_SONIYA = float(os.environ.get("NOL_BAYT_SONIYA", "30"))

#: Transpozitsiya ta'miri faqat SHUNCHA baytdan kichik yozuvda izlanadi —
#: skan O(yozuv_uzunligi) ta DFS urinishini bildiradi.
NOL_BAYT_SWAP_BAYT = int(os.environ.get("NOL_BAYT_SWAP_BAYT", "20000"))
#: Har swap nomzodiga beriladigan kichik tugun cheki (noto'g'ri nomzod
#: birinchi bo'lakdayoq o'ladi, shuning uchun ko'p kerak emas).
NOL_BAYT_SWAP_TUGUN = int(os.environ.get("NOL_BAYT_SWAP_TUGUN", "20000"))

#: Shubha bosqichida o'qiladigan eng katta fayl (undan kattasi bizning
#: holatimiz emas — xotirani behuda band qilmaymiz).
NOL_BAYT_MAX_FAYL = int(os.environ.get("NOL_BAYT_MAX_FAYL", str(64 * 1024 * 1024)))
#: Zip yozuvlari soni chegarasi.
NOL_BAYT_MAX_YOZUV = int(os.environ.get("NOL_BAYT_MAX_YOZUV", "500"))

#: `sharedStrings.xml` tiklanmasa BO'SH matn o'rinbosari qo'yiladimi.
#: Sonlarga ta'sir qilmaydi, LEKIN matnlar yo'qoladi — shuning uchun bunday
#: natija `hisobot["ss_orinbosar"]` bilan belgilanadi (chaqiruvchi uni
#: ishonchsiz deb hisoblashi mumkin).
NOL_BAYT_SS_ORINBOSAR = _bayroq("NOL_BAYT_SS_ORINBOSAR")

#: Sintez qilinadigan lug'at yozuvlarining ENG KATTA soni.
#:
#: MAJBURIY (2026-09-07 ko'rigida topilgan kuchaytirish): lug'at o'lchami
#: HUJJATDAN olinadi — `<c t="s"><v>N</v></c>` dagi N ni ishtirokchi to'liq
#: nazorat qiladi va u shuncha yozuv yasashga majbur qiladi. Chegarasiz
#: o'lchandi: 1 854 baytli fayl, indeks 2 000 000 -> 79 s / 182 MB;
#: indeks 5 000 000 -> 487 s / 447 MB (kuchaytirish x241 304), 2^31 esa
#: ~34 GB. Ya'ni bombani tashqi fayl emas, TA'MIRNING O'ZI yasardi va bu
#: 2026-09-02 dagi lease-livelock ssenariysini qaytarardi.
#: Korpusdagi haqiqiy maksimum — 31 634, shuning uchun 200 000 olti barobar
#: zaxira beradi. Chegaradan oshsa tiklash rad etiladi (`TiklashXatosi`) va
#: chaqiruvchi ASL o'qish xatosini ko'taradi — hujjat avvalgi holatidan
#: yomonlashmaydi.
NOL_BAYT_MAX_SST = int(os.environ.get("NOL_BAYT_MAX_SST", "200000"))

# reader.py dagi chegaralar AYNAN shu env o'zgaruvchilardan o'qiladi
# (import qilmaymiz — aylanma bog'lanish bo'lmasin).
MAX_XLSX_PART_BYTES = int(os.environ.get("MAX_XLSX_PART_BYTES", str(200 * 1024 * 1024)))
MAX_XLSX_JAMI_BYTES = int(os.environ.get("MAX_XLSX_JAMI_BYTES", str(500 * 1024 * 1024)))


class TiklashXatosi(Exception):
    """Nol-bayt tiklashi natija bermadi.

    Chaqiruvchi buni YUTIB, ASL o'qish xatosini qayta ko'tarishi kerak —
    ishtirokchiga ko'rinadigan izoh o'zgarmasin.
    """


class BudjetTugadi(TiklashXatosi):
    """Qidiruv tugun yoki vaqt budjetidan oshdi."""


# ── 1-bosqich: arzon shubha ──────────────────────────────────────────────────
def nol_bayt_shubhasi(xom):
    """Baytlar shu buzilishga o'xshaydimi (SOG'LOM faylda darhol False).

    Sog'lom .zip da 0x00 har doim bor (masalan «version needed» maydonining
    yuqori bayti, umumiy bayroqlar), buzilganida esa BITTA ham qolmaydi —
    shuning uchun «0x00 umuman yo'q» eng arzon va aniq ajratgich.
    """
    if not xom or len(xom) < 22 or len(xom) > NOL_BAYT_MAX_FAYL:
        return False
    if xom[:4] != b"PK\x03\x04":
        return False
    if b"\x00" in xom:
        return False
    return xom.rfind(b"PK\x05\x06") != -1


# ── 2-bosqich: ZIP skeleti ───────────────────────────────────────────────────
def _min(xom):
    """Sonli maydonning ENG KICHIK talqini: har 0x20 ni 0x00 deb o'qish."""
    return struct.unpack("<I", bytes(0 if c == 0x20 else c for c in xom))[0]


def _max(xom):
    """Sonli maydonning ENG KATTA talqini: 0x20 o'z holicha qolsa."""
    return struct.unpack("<I", xom)[0]


def _u16min(xom):
    return struct.unpack("<H", bytes(0 if c == 0x20 else c for c in xom))[0]


def _mos(qiymat, xom):
    """Hisoblangan qiymat xom maydonga BAG'RIKENG mos keladimi.

    Maydonning o'zi ham buzilgan bo'lishi mumkin, shuning uchun 0x20 xom
    bayti 0x00 hisoblangan baytga ham mos deb qabul qilinadi (noziklik «a»).
    """
    q = struct.pack("<I", qiymat & 0xFFFFFFFF)
    for a, b in zip(xom, q):
        if a == b:
            continue
        if a == 0x20 and b == 0x00:
            continue
        return False
    return True


def _talqinlar(xom):
    """Buzilgan 4-baytli sonli maydonning BARCHA talqinlari (o'sish tartibida).

    Har 0x20 bayt ikki xil bo'lishi mumkin: haqiqiy probel (0x20) yoki
    buzilgan nol (0x00). Maydonda ko'pi bilan 4 ta shunday bayt bo'lgani
    uchun nomzadlar soni 16 tadan oshmaydi — qidiruv QAT'IY chegaralangan,
    2026-09-02 dagi livelock kabi holat bo'lishi mumkin emas.
    """
    noaniq = [k for k, c in enumerate(xom) if c == 0x20]
    if not noaniq:
        return [struct.unpack("<I", xom)[0]]
    nomzadlar = set()
    for niqob in range(1 << len(noaniq)):
        buf = bytearray(xom)
        for j, k in enumerate(noaniq):
            buf[k] = 0x20 if (niqob >> j) & 1 else 0x00
        nomzadlar.add(struct.unpack("<I", bytes(buf))[0])
    return sorted(nomzadlar)


def _imzoli_ofset(b, xom, imzo):
    """0x20 noaniqligini KUTILGAN IMZO bilan yechadi.

    `_min` (har 0x20 ni nol deb o'qish) faqat maydonda haqiqiy probel
    BO'LMAGANDA to'g'ri. Ofset 0x0F20 bo'lsa (= 3872) uning past bayti
    qonuniy 0x20 va `_min` uni 3840 deb o'qib, «lokal sarlavha ofseti
    tiklanmadi» degan xato berardi. Zipda esa mustaqil dalil bor: ofset
    AYNAN o'z imzosiga (`PK\\x03\\x04` / `PK\\x01\\x02`) tegishi shart —
    nomzadlar orasidan shuni tanlaymiz.

    Hech biri mos kelmasa `_min` qaytadi: chaqiruvchi avvalgidek toza xato
    beradi, ya'ni bu funksiya hech qachon YANGI nosozlik keltirmaydi.
    """
    for nomzad in _talqinlar(xom):
        if nomzad + len(imzo) <= len(b) and b[nomzad:nomzad + len(imzo)] == imzo:
            return nomzad
    return _min(xom)


def _yozuvlar(b):
    """Markaziy katalogni o'qib, yozuvlar ro'yxatini qaytaradi."""
    eo = b.rfind(b"PK\x05\x06")
    if eo < 0 or eo + 20 > len(b):
        raise TiklashXatosi("EOCD topilmadi")
    cdo = _imzoli_ofset(b, b[eo + 16:eo + 20], b"PK\x01\x02")
    o = cdo
    res = []
    while o + 46 <= eo and b[o:o + 4] == b"PK\x01\x02":
        if len(res) >= NOL_BAYT_MAX_YOZUV:
            raise TiklashXatosi("zip yozuvlari juda ko'p (%d dan ortiq)" % NOL_BAYT_MAX_YOZUV)
        nl = _u16min(b[o + 28:o + 30])
        el = _u16min(b[o + 30:o + 32])
        cl = _u16min(b[o + 32:o + 34])
        lho = _imzoli_ofset(b, b[o + 42:o + 46], b"PK\x03\x04")
        if lho + 30 > len(b) or b[lho:lho + 4] != b"PK\x03\x04":
            raise TiklashXatosi("lokal sarlavha ofseti tiklanmadi (%s)" % lho)
        e = {
            "nom": b[o + 46:o + 46 + nl].decode("latin1"),
            "usul": _u16min(b[o + 10:o + 12]),
            "crc_xom": b[o + 16:o + 20],
            "usz_xom": b[o + 24:o + 28],
            "csz": _min(b[o + 20:o + 24]),
            "lho": lho,
        }
        e["doff"] = lho + 30 + _u16min(b[lho + 26:lho + 28]) + _u16min(b[lho + 28:lho + 30])
        e["usz_max"] = _max(e["usz_xom"])
        res.append(e)
        o += 46 + nl + el + cl
    if not res:
        raise TiklashXatosi("markaziy katalog o'qilmadi")
    return res, cdo


def _joylashuv_izchilmi(ents, cdo):
    """Har yozuvning oxiri keyingisining boshiga tegadimi.

    Bu — ofsetlar to'g'ri tiklanganining mustaqil dalili: agar biror sonli
    maydonda 0x20 aslida haqiqiy probel bo'lgan bo'lsa, zanjir uziladi va
    biz taxmin qilishdan ko'ra toza xato qaytarganimiz ma'qul.
    """
    for k, e in enumerate(ents):
        keyingi = ents[k + 1]["lho"] if k + 1 < len(ents) else cdo
        if e["doff"] + e["csz"] != keyingi:
            return False
    return True


# ── 3-bosqich: inkremental kesish qoidalari ──────────────────────────────────
#
# Quyidagi lug'atlar Excel yozadigan OOXML ni qamraydi. Ular RAD ETUVCHI
# emas, KESUVCHI: noma'lum teg uchragan shox tashlanadi, ammo yakuniy
# qabulni baribir CRC32 hal qiladi. Shu sababli lug'atning to'liq
# bo'lmasligi noto'g'ri natijaga emas, faqat «tiklanmadi» ga olib keladi.
TAGLAR = set("""worksheet dimension sheetViews sheetView selection pane sheetFormatPr
cols col sheetData row c v f is t r rPr sst si phoneticPr mergeCells mergeCell
printOptions pageMargins pageSetup headerFooter oddHeader oddFooter evenHeader evenFooter
firstHeader firstFooter rowBreaks colBreaks brk drawing legacyDrawing picture
conditionalFormatting cfRule dataValidations dataValidation formula1 formula2
autoFilter filterColumn filters filter sortState hyperlinks hyperlink
sheetProtection protectedRanges protectedRange pageSetUpPr tabColor outlinePr
extLst ext ignoredErrors ignoredError customSheetViews customSheetView
sheetCalcPr controls control oleObjects oleObject tableParts tablePart
x14ac:dyDescent mc:AlternateContent mc:Choice mc:Fallback x14:dataValidations
x14:dataValidation xm:f xm:sqref
sheetPr scenarios customProperties
dataConsolidate cellWatches smartTags webPublishItems oleSize
printerSettings colorScale cfvo color dataBar iconSet
sz rFont family charset scheme b i u strike vertAlign outline shadow condense extend
rPh""".split())

ATRLAR = set("""xmlns xmlns:r xmlns:mc xmlns:x14ac xmlns:xr xmlns:xr2 xmlns:xr3 xmlns:x14
xmlns:xm mc:Ignorable mc:PreserveAttributes ref tabSelected view zoomScale
zoomScaleNormal zoomScaleSheetLayoutView zoomScalePageLayoutView workbookViewId
activeCell sqref topLeftCell defaultRowHeight defaultColWidth x14ac:dyDescent
min max width customWidth bestFit style hidden outlineLevel collapsed
spans ht customHeight thickBot thickTop s t r count uniqueCount
left right top bottom header footer paperSize orientation scale fitToWidth
fitToHeight horizontalDpi verticalDpi r:id id name display location tooltip
xSplit ySplit activePane state pane sheet fitToPage applyNumberFormat
customFormat ph dyDescent baseColWidth summaryBelow summaryRight
errorStyle showInputMessage showErrorMessage allowBlank operator type
prompt promptTitle error errorTitle dxfId priority stopIfTrue
xr:uid xr3:uid alignWithMargins differentOddEven differentFirst
scaleWithDoc headings gridLines gridLinesSet blackAndWhite draft cellComments
useFirstPageNumber firstPageNumber usePrinterDefaults copies pageOrder
manualBreakCount man pt
version encoding standalone outline showGridLines showRowColHeaders
zeroHeight defaultThemeVersion filterMode enableFormatConditionsCalculation
codeName syncHorizontal syncVertical transitionEvaluation published
outlineLevelRow outlineLevelCol quotePrefix cm vm si ca aca dt2D dtr del1 del2
r1 r2 shared array bx errors
showZeros showFormulas rightToLeft showOutlineSymbols showWhiteSpace
defaultGridColor colorId tint theme rgb indexed auto val
applyAlignment applyBorder autoPageBreaks
applyFill applyFont applyProtection numFmtId fontId fillId borderId xfId
customBuiltin showAutoFilter showRuler evenAndOdd
xml:space horizontalCentered verticalCentered defaultPageBreakPreview""".split())

_RAQAM = set(b"0123456789")
_KATTA = set(b"ABCDEFGHIJKLMNOPQRSTUVWXYZ")
_SON_BELGI = set(b"0123456789.-+eE")
_REF_BELGI = _KATTA | _RAQAM | set(b":$")
_TURLAR = {b"s", b"n", b"str", b"b", b"e", b"inlineStr", b"noConversion"}

#: Atribut qiymatining sinfi — noto'g'ri dekodlangan shox `s="12a"` kabi
#: qiymat bilan darhol o'ladi.
_SINF = {}
for _a in ("s", "count", "fontId", "numFmtId", "fillId", "borderId", "xfId", "min", "max",
           "paperSize", "scale", "fitToWidth", "fitToHeight", "horizontalDpi", "verticalDpi",
           "workbookViewId", "outlineLevel", "outlineLevelRow", "outlineLevelCol",
           "uniqueCount", "priority", "dxfId", "firstPageNumber", "copies", "colorId",
           "indexed", "theme", "zoomScale", "zoomScaleNormal", "zoomScaleSheetLayoutView",
           "zoomScalePageLayoutView", "xSplit", "ySplit", "id", "man", "pt", "baseColWidth"):
    _SINF[_a] = "butun"
for _a in ("width", "ht", "defaultRowHeight", "defaultColWidth", "left", "right", "top",
           "bottom", "header", "footer", "x14ac:dyDescent", "dyDescent", "tint"):
    _SINF[_a] = "son"
for _a in ("r", "ref", "sqref", "activeCell", "topLeftCell", "spans"):
    _SINF[_a] = "ref"
_SINF["t"] = "tur"


def _sinf_ok(nom, qiymat):
    s = _SINF.get(nom)
    if s is None:
        return True
    if s == "butun":
        return 0 < len(qiymat) < 12 and all(c in _RAQAM for c in qiymat)
    if s == "son":
        return 0 < len(qiymat) < 24 and all(c in _SON_BELGI for c in qiymat)
    if s == "ref":
        return bool(qiymat) and len(qiymat) <= 40 and all(
            c in _REF_BELGI or c == 0x20 for c in qiymat)
    if s == "tur":
        return qiymat in _TURLAR
    return True


def _ascii_ok(ch):
    for c in ch:
        if not (32 <= c < 127 or c in (9, 10, 13)):
            return False
    return True


def _utf8_ok(q, ch):
    """UTF-8 izchilligi. Qaytaradi: tugallanmagan DUM baytlari yoki None."""
    buf = q + ch
    for kes in range(4):
        try:
            (buf[:len(buf) - kes] if kes else buf).decode("utf-8")
        except UnicodeDecodeError:
            continue
        return buf[len(buf) - kes:] if kes else b""
    return None


def _colnum(s):
    n = 0
    for c in s:
        if 65 <= c <= 90:
            n = n * 26 + (c - 64)
        else:
            return None
    return n


def _ref_ajrat(buf):
    """«AB12» -> (28, 12); noto'g'ri bo'lsa None."""
    i = 0
    while i < len(buf) and 65 <= buf[i] <= 90:
        i += 1
    if i == 0 or i == len(buf):
        return None
    d = buf[i:]
    if not d.isdigit():
        return None
    return _colnum(buf[:i]), int(d)


#: Qat'iy tekshiruvchining boshlang'ich holati.
#: (rejim, bufer, atribut, teg, matn, qator, oxirgi_ustun)
#: rejim: 0 matn, 1 teg-nom, 2 teg-ichi, 3 atribut-nom, 4 «"» qiymat, 5 «'» qiymat
QATIY_BOSH = (0, b"", b"", b"", b"", 0, 0)


def _qatiy_feed(st, ch):
    """OOXML lug'ati + katak manzili invariantlari bilan inkremental tekshiruv.

    Excel yozgan har qanday varaqda `<row r="N">` da N qat'iy o'suvchi,
    `<c r="XN">` da esa N joriy qatorga teng va ustun harfi qat'iy o'suvchi.
    Noto'g'ri dekodlangan shox shu invariantda tezda o'ladi — aynan shu
    qoida qidiruvni million tugundan o'n mingga tushiradi.
    """
    rej, buf, atr, teg, matn, row, lastcol = st
    for c in ch:
        if rej == 0:
            if c == 0x3c:
                t = matn.strip()
                if t and teg == b"v" and not all(x in _SON_BELGI for x in t):
                    return None
                rej, buf, matn = 1, b"", b""
            elif c < 32 and c not in (9, 10, 13):
                return None
            else:
                matn += bytes([c])
                if len(matn) > 4096:
                    return None
        elif rej == 1:
            if c == 0x2f and buf == b"":
                buf = b"/"
            elif c in (0x20, 0x2f, 0x3e, 0x09, 0x0a, 0x0d):
                yopuvchi = buf[:1] == b"/"
                nom = buf[1:] if yopuvchi else buf
                s = nom.decode("latin1")
                if s.startswith("?") or s.startswith("!"):
                    pass
                elif s and s not in TAGLAR:
                    return None
                if not yopuvchi and nom:
                    teg = nom
                if c == 0x3e:
                    rej, buf = 0, b""
                else:
                    rej, buf = 2, b""
            elif c == 0x3c:
                return None
            else:
                buf += bytes([c])
                if len(buf) > 40:
                    return None
        elif rej == 2:
            if c == 0x3e:
                rej, buf = 0, b""
            elif c == 0x3c:
                return None
            elif c in (0x20, 0x09, 0x0a, 0x0d, 0x2f, 0x3f):
                pass
            elif c == 0x22:
                rej, buf = 4, b""
            elif c == 0x27:
                rej, buf = 5, b""
            else:
                rej, buf = 3, bytes([c])
        elif rej == 3:
            if c == 0x3d:
                s = buf.decode("latin1")
                if s not in ATRLAR:
                    return None
                atr, rej, buf = s, 2, b""
            elif c in (0x20, 0x09, 0x0a, 0x0d, 0x3c, 0x3e):
                return None
            else:
                buf += bytes([c])
                if len(buf) > 40:
                    return None
        else:  # rej 4 yoki 5 — atribut qiymati
            yop = 0x22 if rej == 4 else 0x27
            if c == yop:
                if not _sinf_ok(atr, buf):
                    return None
                if atr == "r":
                    if teg == b"row":
                        if not buf.isdigit():
                            return None
                        n = int(buf)
                        if n <= row:
                            return None
                        row, lastcol = n, 0
                    elif teg == b"c":
                        p = _ref_ajrat(buf)
                        if p is None:
                            return None
                        col, r2 = p
                        if row and r2 != row:
                            return None
                        if col <= lastcol:
                            return None
                        lastcol = col
                rej, buf, atr = 2, b"", b""
            elif c == 0x3c:
                return None
            elif c < 32 and c not in (9, 10, 13):
                return None
            else:
                buf += bytes([c])
                if len(buf) > 600:
                    return None
    return (rej, buf, atr, teg, matn, row, lastcol)


_NOM_BOSH = set(b"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_:/?!")


def _erkin_feed(st, ch):
    """Lug'atsiz, faqat XML tuzilmasi bo'yicha tekshiruv (holat — butun son).

    Ixtiyoriy `.xml`/`.rels` uchun: teg nomlarini oldindan bilmaymiz,
    shuning uchun faqat qavslar/qo'shtirnoqlar izchilligi ushlanadi.
    """
    for c in ch:
        if st == 0:
            if c == 0x3c:
                st = 4
            elif c < 32 and c not in (9, 10, 13):
                return None
        elif st == 4:
            if c in _NOM_BOSH:
                st = 1
            else:
                return None
        elif st == 1:
            if c == 0x22:
                st = 2
            elif c == 0x27:
                st = 3
            elif c == 0x3e:
                st = 0
            elif c == 0x3c:
                return None
            elif c < 32 and c not in (9, 10, 13):
                return None
        elif st == 2:
            if c == 0x22:
                st = 1
            elif c == 0x3c:
                return None
            elif c < 32 and c not in (9, 10, 13):
                return None
        else:  # st == 3
            if c == 0x27:
                st = 1
            elif c == 0x3c:
                return None
            elif c < 32 and c not in (9, 10, 13):
                return None
    return st


def _rejim_zanjiri(nom):
    """Yozuv nomi bo'yicha kesish rejimlari — QAT'IYDAN yumshoq tomon.

    Qat'iy rejim tez, lekin lug'at to'liq bo'lmasa yoki varaqda kirillcha
    inline matn bo'lsa ishlamaydi — shuning uchun zanjir bo'ylab pastga
    tushiladi. Qabulni baribir CRC hal qilgani uchun yumshatish xavfsiz.
    """
    if "/worksheets/" in nom and nom.endswith(".xml"):
        return ("lugat", "ss", "utf8")
    if nom.endswith("sharedStrings.xml"):
        return ("ss", "utf8")
    if nom.endswith(".xml") or nom.endswith(".rels"):
        return ("utf8",)
    return ("bin",)


# ── 4-bosqich: budjet va DFS ─────────────────────────────────────────────────
class _Budjet:
    """Bitta fayl uchun tugun + vaqt chegarasi (2-talab: livelock bo'lmasin)."""

    def __init__(self, tugun=None, soniya=None):
        self.tugun = NOL_BAYT_TUGUN if tugun is None else tugun
        self.sarflandi = 0
        self.muddat = time.monotonic() + (NOL_BAYT_SONIYA if soniya is None else soniya)

    def sarfla(self):
        self.sarflandi += 1
        if self.sarflandi > self.tugun:
            raise BudjetTugadi("tugun budjeti tugadi (%d)" % self.tugun)
        # Vaqtni har tugunda o'lchash qimmat — har 512 tugunda yetarli.
        # Birinchi tugunda ham o'lchanadi (`& 0x1FF == 1`), aks holda
        # 512 tugundan kichik ish vaqt chegarasini umuman ko'rmasdi.
        if (self.sarflandi & 0x1FF) == 1 and time.monotonic() > self.muddat:
            raise BudjetTugadi("vaqt budjeti tugadi")

    def vaqt_qoldimi(self):
        return time.monotonic() <= self.muddat


def _bosh_holat(o0, rej):
    """Birinchi (ambiguitysiz) bo'lakdan keyingi tekshiruvchi holati."""
    if rej == "bin":
        return b"", None
    if rej == "lugat":
        if not _ascii_ok(o0):
            return None, None
        return b"", _qatiy_feed(QATIY_BOSH, o0)
    if rej == "ss":
        q = _utf8_ok(b"", o0)
        if q is None:
            return None, None
        return q, _qatiy_feed(QATIY_BOSH, o0)
    q = _utf8_ok(b"", o0)
    if q is None:
        return None, None
    return q, _erkin_feed(0, o0)


def _keyingi_holat(q, xs, out, rej):
    """Chiqishning yangi bo'lagini tekshiruvchidan o'tkazadi."""
    if rej == "bin":
        return b"", None, True
    if rej == "lugat":
        if not _ascii_ok(out):
            return None, None, False
        xs2 = _qatiy_feed(xs, out)
        return q, xs2, xs2 is not None
    if rej == "ss":
        q2 = _utf8_ok(q, out)
        if q2 is None:
            return None, None, False
        xs2 = _qatiy_feed(xs, out)
        return q2, xs2, xs2 is not None
    q2 = _utf8_ok(q, out)
    if q2 is None:
        return None, None, False
    xs2 = _erkin_feed(xs, out)
    return q2, xs2, xs2 is not None


def _dfs(data, e, rej, budjet, cheklov=None):
    """Bir yozuvni ochib beradi yoki None.

    `data` — buzilgan siqilgan bo'lak. Har 0x20 bayti ambiguity nuqtasi;
    ular bo'yicha orqaga qaytishli qidiruv yuritiladi. `cheklov` — SHU
    chaqiruv uchun tugun cheki (swap skanida kichik qo'yiladi); umumiy
    budjet baribir `budjet` orqali sarflanadi.
    """
    crc_xom, usz_xom, usz_max = e["crc_xom"], e["usz_xom"], e["usz_max"]
    poz = [i for i, c in enumerate(data) if c == 0x20]
    m = len(poz)
    seg_end = poz[1:] + [len(data)]

    budjet.sarfla()
    d0 = zlib.decompressobj(-15)
    try:
        o0 = d0.decompress(data[:poz[0]] if m else data)
    except Exception:
        return None
    q, xs = _bosh_holat(o0, rej)
    if q is None or (rej != "bin" and xs is None):
        return None

    def yakun(ulen, crc, unused):
        return (not unused) and _mos(ulen, usz_xom) and _mos(crc, crc_xom)

    if m == 0:
        if yakun(len(o0), binascii.crc32(o0), d0.unused_data):
            return bytes(data)
        return None

    yigildi = 0
    stack = [(0, d0, q, xs, len(o0), binascii.crc32(o0), ())]
    while stack:
        i, d, q, xs, ulen, crc, tan = stack.pop()
        if i == m:
            if yakun(ulen, crc, d.unused_data):
                bb = bytearray(data)
                for p, c in zip(poz, tan):
                    bb[p] = c
                return bytes(bb)
            continue
        keyin = data[poz[i] + 1:seg_end[i]]
        for c in (0x20, 0x00):
            budjet.sarfla()
            yigildi += 1
            if cheklov is not None and yigildi > cheklov:
                return None
            d2 = d.copy()
            try:
                out = d2.decompress(bytes([c]) + keyin)
            except Exception:
                continue
            u2 = ulen + len(out)
            # Ochilgan hajm xom maydonning ENG KATTA talqinidan oshsa —
            # bu shox noto'g'ri (zip-bomba himoyasi ham shu yerda).
            if u2 > usz_max or u2 > MAX_XLSX_PART_BYTES:
                continue
            q2, xs2, ok = _keyingi_holat(q, xs, out, rej)
            if not ok:
                continue
            stack.append((i + 1, d2, q2, xs2, u2, binascii.crc32(out, crc), tan + (c,)))
    return None


def _swap_tamir(data, e, rej, budjet):
    """Qo'shni ikki baytning o'rin almashishini izlaydi (noziklik «b»).

    Ko'chirish paytida ba'zan qo'shni juftlik joyini almashtiradi
    (113533 da 105/106). Har nomzod uchun kichik cheklovli DFS yuritiladi —
    noto'g'ri nomzod birinchi bo'lakdayoq o'ladi, shuning uchun skan arzon.
    Qaytaradi: (tuzatilgan_siqilgan_bayt, [pozitsiya]) yoki (None, []).
    """
    if len(data) > NOL_BAYT_SWAP_BAYT:
        return None, []
    for j in range(len(data) - 1):
        if data[j] == data[j + 1]:
            continue
        t = bytearray(data)
        t[j], t[j + 1] = t[j + 1], t[j]
        r = _dfs(bytes(t), e, rej, budjet, cheklov=NOL_BAYT_SWAP_TUGUN)
        if r is not None:
            return r, [j]
    return None, []


def _yozuvni_tikla(xom, e, budjet):
    """Bitta zip yozuvini ochib beradi: (mazmun, tuzatishlar) yoki (None, [])."""
    data = xom[e["doff"]:e["doff"] + e["csz"]]
    if len(data) != e["csz"]:
        return None, []
    # Zip-bomba: e'lon qilingan hajmning ENG KICHIK talqini ham chegaradan
    # oshsa, haqiqiy hajm ham oshadi (haqiqiy qiymat min va max orasida).
    # `usz_max` ni bu yerda ISHLATIB BO'LMAYDI — 0x20 li maydonda u sun'iy
    # ravishda ulkan chiqadi (0x20202020 ≈ 538 MB) va sog'lom yozuvni ham
    # bomba deb rad etardi. Haqiqiy himoya DFS ichida: ochilgan bayt soni
    # MAX_XLSX_PART_BYTES dan oshgan shox tashlanadi.
    if _min(e["usz_xom"]) > MAX_XLSX_PART_BYTES:
        raise TiklashXatosi("«%s» ochilganda juda katta bo'lishi mumkin — zip bomba"
                            % e["nom"])
    if e["usul"] == 0:                       # siqilmagan — ochish shart emas
        if _mos(binascii.crc32(data), e["crc_xom"]) and _mos(len(data), e["usz_xom"]):
            return data, []
        return None, []
    if e["usul"] != 8:
        return None, []

    for rej in _rejim_zanjiri(e["nom"]):
        r = _dfs(data, e, rej, budjet)
        if r is not None:
            return zlib.decompressobj(-15).decompress(r), []
    # Hech bir rejim yechmadi — transpozitsiya gipotezasi, BUTUN zanjir bo'ylab.
    #
    # Ilgari bu yerda faqat eng qat'iy rejim (`[0]`) ishlatilardi va ta'mir
    # amalda HECH QACHON ishlamasdi: `_rejim_zanjiri` izohi aynan shuni
    # aytadi — «lug'at to'liq bo'lmasa yoki varaqda kirillcha inline matn
    # bo'lsa» qat'iy rejim yechmaydi, aynan shunday varaqlar esa bizda
    # ko'pchilik. O'lchov (4 qatorli kirillcha varaq, 505 bayt):
    #   rejim «lugat» — 505 tugun sarflab TOPMAYDI
    #   rejim «ss»    — 45 tugunda TOPADI
    # Yumshoq rejimda izlash xavfsiz, chunki qabulni baribir CRC hal qiladi
    # (`_dfs` ichida), umumiy `budjet` esa chegarani ushlab turadi.
    for rej in _rejim_zanjiri(e["nom"]):
        r, tuz = _swap_tamir(data, e, rej, budjet)
        if r is not None:
            return zlib.decompressobj(-15).decompress(r), tuz
    return None, []


# ── 5-bosqich: yig'ish ───────────────────────────────────────────────────────
#: Bularsiz .xlsx ni o'qib bo'lmaydi — biri tiklanmasa tiklash muvaffaqiyatsiz.
_ZARUR = ("[Content_Types].xml", "xl/workbook.xml")


def _xavfsiz_nom(nom):
    """Zip yozuvi nomi xavfsizmi (yo'l chiqib ketishi bo'lmasin)."""
    if not nom or nom.endswith("/"):
        return False
    if nom.startswith("/") or nom.startswith("\\") or ":" in nom:
        return False
    return ".." not in nom.replace("\\", "/").split("/")


def _ss_orinbosar(chiqish):
    """`sharedStrings.xml` o'rniga BO'SH matnlar to'plamini yasaydi.

    Varaqlardagi `t="s"` kataklari lug'at indeksiga murojaat qiladi;
    lug'at bo'lmasa openpyxl yiqiladi. Bo'sh o'rinbosar bilan SONLAR
    saqlanadi (narx tekshiruvi shularga tayanadi), MATNLAR esa yo'qoladi —
    shuning uchun natija `hisobot["ss_orinbosar"]` bilan belgilanadi.

    O'lcham HUJJATDAN keladi va shuning uchun CHEGARALANADI — `NOL_BAYT_MAX_SST`
    ga qarang (chegarasiz 1.8 KB li fayl 80 MB lik qism yasay olardi).
    """
    mx = -1
    for nom, mazmun in chiqish.items():
        if "/worksheets/" not in nom:
            continue
        for mm in re.finditer(rb'<c[^>]*t="s"[^>]*>\s*<v>(\d+)</v>', mazmun):
            mx = max(mx, int(mm.group(1)))
    n = mx + 1
    if n > NOL_BAYT_MAX_SST:
        raise TiklashXatosi(
            "sharedStrings o'rinbosari juda katta (%d; chegara %d)"
            % (n, NOL_BAYT_MAX_SST))
    bosh = (b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'
            b'<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
            b' count="%d" uniqueCount="%d">' % (n, n))
    return bosh + b"<si><t></t></si>" * n + b"</sst>"


def tikla(filepath, hisobot=None):
    """Nol-bayt buzilgan .xlsx ni tiklab, VAQTINCHALIK nusxa yo'lini qaytaradi.

    Asl faylga tegilmaydi. Chaqiruvchi tugagach nusxa papkasini o'chiradi:

        nusxa = tiklash_nol_bayt.tikla(yol)
        try:
            ...
        finally:
            shutil.rmtree(os.path.dirname(nusxa), ignore_errors=True)

    `hisobot` — ixtiyoriy lug'at; to'ldiriladi (`yozuv_soni`, `tiklandi`,
    `tuzatish`, `ss_orinbosar`, `tashlandi`, `tugun`, `soniya`).

    Muvaffaqiyatsizlikda `TiklashXatosi` (yoki uning turi `BudjetTugadi`)
    ko'tariladi — chaqiruvchi ASL o'qish xatosini qayta ko'tarishi kerak.
    BOSHQA turdagi istisno chiqmaydi: kutilmagan ichki xato ham shu turga
    o'raladi, aks holda ishtirokchiga ko'rinadigan izoh «tiklash xatosi»
    bo'lib o'zgarib ketardi (5-talab).
    """
    if hisobot is None:
        hisobot = {}
    if not TIKLASH_YOQ:
        raise TiklashXatosi("nol-bayt tiklash o'chirilgan (TIKLASH_YOQ=0)")
    try:
        return _tikla_ichki(filepath, hisobot)
    except TiklashXatosi:
        raise
    except Exception as xato:                # zlib/struct/IO — hammasi bir turga
        raise TiklashXatosi("tiklash ichki xatosi: %s: %s"
                            % (type(xato).__name__, xato))


def _tikla_ichki(filepath, hisobot):
    boshlandi = time.monotonic()
    hajm = os.path.getsize(filepath)
    if hajm > NOL_BAYT_MAX_FAYL:
        raise TiklashXatosi("fayl juda katta (%d bayt)" % hajm)
    with open(filepath, "rb") as fh:
        xom = fh.read()
    if not nol_bayt_shubhasi(xom):
        raise TiklashXatosi("nol-bayt naqshi topilmadi")

    budjet = _Budjet()
    ents, cdo = _yozuvlar(xom)
    if not _joylashuv_izchilmi(ents, cdo):
        # Ofsetlar zanjiri uzilgan — taxmin qilgandan ko'ra toza xato beramiz.
        raise TiklashXatosi("zip skeleti izchil emas — ofsetlar tiklanmadi")

    chiqish = {}
    tuzatish = []
    tashlandi = []
    jami = 0
    for e in ents:
        if not _xavfsiz_nom(e["nom"]):
            tashlandi.append(e["nom"])
            continue
        mazmun, tuz = _yozuvni_tikla(xom, e, budjet)
        if mazmun is None:
            tashlandi.append(e["nom"])
            continue
        jami += len(mazmun)
        if jami > MAX_XLSX_JAMI_BYTES:
            raise TiklashXatosi("tiklangan mazmun juda katta (%d bayt)" % jami)
        chiqish[e["nom"]] = mazmun
        if tuz:
            tuzatish.append((e["nom"], tuz))

    hisobot.update(yozuv_soni=len(ents), tiklandi=len(chiqish), tuzatish=tuzatish,
                   tashlandi=tashlandi, ss_orinbosar=False,
                   tugun=budjet.sarflandi, soniya=round(time.monotonic() - boshlandi, 2))

    varaqlar = [k for k in chiqish
                if k.startswith("xl/worksheets/") and k.endswith(".xml")]
    yetishmaydi = [k for k in _ZARUR if k not in chiqish]
    if yetishmaydi or not varaqlar:
        raise TiklashXatosi("zarur yozuvlar tiklanmadi: %s"
                            % (", ".join(yetishmaydi) or "varaq yo'q"))

    # sharedStrings tiklanmagan, lekin varaqlar unga murojaat qiladi.
    if ("xl/sharedStrings.xml" not in chiqish
            and any(b't="s"' in chiqish[k] for k in varaqlar)):
        if not NOL_BAYT_SS_ORINBOSAR:
            raise TiklashXatosi("sharedStrings.xml tiklanmadi")
        # SINTEZ QILINGAN qism ham umumiy hajm budjetiga kiradi: u yuqoridagi
        # sikldan KEYIN yasaladi, ya'ni `jami` hisobiga o'z-o'zidan tushmaydi.
        ss = _ss_orinbosar(chiqish)
        jami += len(ss)
        if jami > MAX_XLSX_JAMI_BYTES:
            raise TiklashXatosi("tiklangan mazmun juda katta (%d bayt)" % jami)
        chiqish["xl/sharedStrings.xml"] = ss
        hisobot["ss_orinbosar"] = True

    papka = tempfile.mkdtemp(prefix="xlsx_nol_")
    nusxa = os.path.join(papka, os.path.basename(filepath))
    try:
        with zipfile.ZipFile(nusxa, "w", zipfile.ZIP_DEFLATED) as z:
            for nom, mazmun in chiqish.items():
                z.writestr(nom, mazmun)
    except Exception:
        shutil.rmtree(papka, ignore_errors=True)
        raise
    hisobot["yol"] = nusxa
    hisobot["soniya"] = round(time.monotonic() - boshlandi, 2)
    return nusxa
