# -*- coding: utf-8 -*-
"""Excel o'qish qatlami — validator.py dan KO'CHIRILGAN kod (P1).

Eski validator.py o'z joyida ishlayveradi (worker undan foydalanadi);
bu nusxa yangi dvigatel uchun. Ekvivalentlik korpusda o'lchanadi:
tests/test_engine_fasad.py + korpus/ekvivalentlik.py.

Ikki himoya qatlami (validator.py bilan bir xil):
  1. `_xlsx_preflight` — merge-bomba/zip-bomba `load_workbook` dan OLDIN
     ushlanadi (openpyxl merge diapazonini o'zi ochib xotirani tugatadi).
  2. Varaq o'lchami chegaralari — MAX_SHEET_ROWS x MAX_SHEET_COLS dan
     oshgan fayl umuman o'qilmaydi (ExcelTooLargeError).

.xls xato kataklari «#VALUE!» matni bilan qaytadi — to'liq buzuq ustun
keyin «bo'sh» deb topilishi uchun (CLAUDE.md, #VALUE! siyosati).

ZAXIRA ZANJIRI (2026-09-07). Asosiy yo'l XATO bergandagina `_zaxira_zanjiri`
ishga tushadi va faylni tiklash modullaridan MOS KELADIGANI bilan qayta
o'qishga uradi. Har usul FAQAT o'ziga tegishli xatoda chaqiriladi (ko'r-ko'rona
hammasi sinalmaydi), hammasi muvaffaqiyatsiz bo'lsa ASL xato qayta ko'tariladi —
ya'ni ishtirokchi ko'radigan izoh tiklash qatlami tufayli hech qachon
o'zgarmaydi. SOG'LOM fayl bu zanjirga UMUMAN kirmaydi: `try` bloki xato
bo'lmaganda bitta ham qo'shimcha bayt o'qimaydi.

TIKLASHNING ISHONCHLILIGI ham uzatiladi: `read_file(yol, hisobot={})` ga
lug'at berilsa, tiklash orqali o'qilganda unga `usul` va — eng muhimi —
`matn_tiklanmadi` yoziladi. Oxirgisi «hujjatning MATN lug'ati zipda yo'q
edi, o'rinbosar qo'yildi» degani: sonlar to'liq, matn esa YO'Q. Bunday
hujjatga MATNGA tayangan mazmun da'vosi (sarlavha topilmadi, bandlarga
qiymat qo'yilmagan) qilinmasligi SHART — `jobs_worker` uni faqat sonlar
bo'yicha hukm qiladi.

DIQQAT: quyidagi diagramma-varaq (chartsheet) tuzatishi zanjirga KIRMAYDI —
u asosiy o'qish yo'lidagi alohida nuqson tuzatishi va `TIKLASH_YOQ` ga
bo'ysunmaydi (sababi `_read_xlsx_ichki` dagi izohda).
"""

import os
import re
import shutil
import struct
import tempfile
import warnings
import zipfile

try:
    import openpyxl
except ImportError:
    raise ImportError("openpyxl is required: pip install openpyxl")

try:
    import xlrd
except ImportError:
    raise ImportError("xlrd is required: pip install xlrd")


# ── Zararli/nosog'lom fayllardan himoya chegaralari ────────────────────────
MAX_MERGE_CELLS = int(os.environ.get("MAX_MERGE_CELLS", "50000"))
MAX_SHEET_ROWS = int(os.environ.get("MAX_SHEET_ROWS", "200000"))
MAX_SHEET_COLS = int(os.environ.get("MAX_SHEET_COLS", "2000"))
MAX_XLSX_PART_BYTES = int(os.environ.get("MAX_XLSX_PART_BYTES", str(200 * 1024 * 1024)))
# Ulkan varaq XML ni QIRQISH paytida oqimdan o'tkaziladigan jami bayt chegarasi.
# Haqiqiy zip-bomba (bir necha GB ga ochiladigan) shu yerda to'xtaydi; Excel
# «butun varaqqa format berilgan» deb yozib qo'ygan 300-400 MB li halol fayl esa
# sig'adi (2026-09-08: 76032 — 328 MB, 138285 — 369 MB).
MAX_XLSX_QIRQISH_BYTES = int(os.environ.get("MAX_XLSX_QIRQISH_BYTES", str(1024 * 1024 * 1024)))
_QIRQISH_BOLAK = 8 * 1024 * 1024


class ExcelTooLargeError(ValueError):
    """Fayl xavfsiz o'qish chegaralaridan oshdi (zararli yoki nosog'lom fayl)."""


class XlsxPartTooLargeError(ExcelTooLargeError):
    """Varaq XML qismi `MAX_XLSX_PART_BYTES` dan katta.

    Alohida sinf, chunki bu holat ko'pincha ZARARLI EMAS: Excel butun varaqqa
    format berilganda har bo'sh katakni faylga yozadi (10-17 million
    `<c r=".." s=".."/>`), va 40 MB li halol smeta 330-370 MB XML ga ochiladi.
    `_read_xlsx` bunda qirqilgan nusxada qayta urinadi; haqiqiy bomba
    `MAX_XLSX_QIRQISH_BYTES` da baribir to'xtaydi.
    """


_MERGE_RE = re.compile(rb'<mergeCell[^>]*\sref="([A-Z]{1,3})(\d{1,7}):([A-Z]{1,3})(\d{1,7})"')
# Qiymati BOR katak: <c r="AB12" ...> … <v> yoki <is>
#
# Ikkita nozik joy:
#   • `(?<!/)>`  — o'z-o'zini yopadigan `<c r="XFD1" s="1"/>` USLUBLI, lekin
#     qiymatsiz katak. Uni hisobga olsak, izlash keyingi katakning `<v>` iga
#     yetib borib, bo'sh ustunni «to'ldirilgan» deb ko'rsatardi.
#   • `<c[ /]` — izlash KEYINGI katakka o'tib ketmasin.
_QIYMATLI_KATAK_RE = re.compile(
    rb'<c r="([A-Z]{1,3})(\d{1,7})"[^>]*(?<!/)>(?:(?!</c>|<c[ /]).)*?<(?:v|is)[ >]',
    re.S)
_DIMENSION_RE = re.compile(rb'<dimension\s+ref="[A-Z]{1,3}\d{1,7}:([A-Z]{1,3})\d{1,7}"')
_DIMENSION_QATOR_RE = re.compile(
    rb'<dimension\s+ref="[A-Z]{1,3}\d{1,7}:[A-Z]{1,3}(\d{1,7})"')
_ROW_RE = re.compile(rb"<row[ >]")


def _col_raqami(harflar: bytes) -> int:
    """'A' -> 1, 'XFD' -> 16384"""
    n = 0
    for b in harflar:
        n = n * 26 + (b - 64)
    return n


def _xlsx_preflight(filepath: str):
    """openpyxl ga BERISHDAN OLDIN faylni xavfsizlikka tekshiradi.

    Bu shart: `openpyxl.load_workbook` ning O'ZI birlashtirilgan diapazonni
    katakma-katak ochadi, shuning uchun tekshiruvni undan keyin qo'yish
    kech bo'ladi — `<mergeCell ref="A1:XFD1048576"/>` bo'lgan 1.4 KB fayl
    yuklash paytidayoq bir necha GB xotira yeydi.

    Qaytaradi: (haqiqiy_kenglik, elon_kenglik, haqiqiy_qator, elon_qator)
      • haqiqiy_kenglik — QIYMATI BOR eng o'ng ustun raqami
      • elon_kenglik    — XML da e'lon qilingan kenglik (bo'sh, uslubli
                          kataklar ham kiradi — openpyxl `max_column` shunga
                          teng bo'ladi)
      • haqiqiy_qator   — XML dagi HAQIQIY `<row>` teglar soni
      • elon_qator      — `<dimension>` da e'lon qilingan oxirgi qator
    Hammasi 0 bo'lishi mumkin (.xlsx emas yoki bo'sh).

    ExcelTooLargeError — fayl xavfli/nosog'lom.
    """
    haqiqiy = elon = haqiqiy_qator = elon_qator = 0
    try:
        with zipfile.ZipFile(filepath) as z:
            varaqlar = [i for i in z.infolist()
                        if i.filename.startswith("xl/worksheets/")
                        and i.filename.endswith(".xml")]
            for info in varaqlar:
                if info.file_size > MAX_XLSX_PART_BYTES:
                    raise XlsxPartTooLargeError(
                        f"«{info.filename}» ochilganda juda katta "
                        f"({info.file_size // 1048576} MB) — zip bomba bo'lishi mumkin")
                with z.open(info) as fh:
                    mazmun = fh.read(MAX_XLSX_PART_BYTES)
                for m in _MERGE_RE.finditer(mazmun):
                    c1, r1, c2, r2 = m.group(1), int(m.group(2)), m.group(3), int(m.group(4))
                    kataklar = (abs(_col_raqami(c2) - _col_raqami(c1)) + 1) * (abs(r2 - r1) + 1)
                    if kataklar > MAX_MERGE_CELLS:
                        raise ExcelTooLargeError(
                            f"birlashtirilgan katak diapazoni juda katta "
                            f"({kataklar:,} katak; chegara {MAX_MERGE_CELLS:,}) — "
                            f"fayl buzuq yoki zararli")
                d = _DIMENSION_RE.search(mazmun)
                if d:
                    elon = max(elon, _col_raqami(d.group(1)))
                    q = _DIMENSION_QATOR_RE.search(mazmun)
                    if q:
                        elon_qator = max(elon_qator, int(q.group(1)))
                # HAQIQIY kenglik va HAQIQIY oxirgi qator — QIYMATI BOR
                # kataklar bo'yicha. `dimension` ko'pincha yolg'on bo'ladi
                # (butun varaqqa format berilsa «A1:IO1048508» deb yoziladi),
                # qiymatli katak esa faqat haqiqiy ma'lumot joyida turadi.
                for m in _QIYMATLI_KATAK_RE.finditer(mazmun):
                    n = _col_raqami(m.group(1))
                    if n > haqiqiy:
                        haqiqiy = n
                    q = int(m.group(2))
                    if q > haqiqiy_qator:
                        haqiqiy_qator = q
    except zipfile.BadZipFile:
        return 0, 0, 0, 0   # .xlsx emas — openpyxl o'zi tushunarli xato beradi
    return (haqiqiy, max(elon, haqiqiy),
            haqiqiy_qator, max(elon_qator, haqiqiy_qator))


def _read_xlsx_keng(filepath: str, kenglik: int, balandlik: int = 0) -> dict:
    """Ustunlari juda ko'p E'LON QILINGAN, lekin ma'lumoti tor fayl.

    Amalda uchraydi: hujjatning boshidagi bir necha ustunda qiymat bor,
    keyingi 16 000 ustunda esa faqat uslub (rang, chegara) qolgan. Oddiy
    yo'l bilan o'qilsa openpyxl millionlab bo'sh katak yaratib xotirani
    tugatadi — shuning uchun oqim (read_only) bilan, kenglikni cheklab
    o'qiymiz.

    Farqi: merge kataklar OCHILMAYDI (read_only rejimda mavjud emas).
    Bu qabul qilinadigan murosa — bunday fayllarda baribir sarlavha
    tuzilmasi buzilgan bo'ladi va hukm xom qiymatlar bo'yicha chiqadi.
    """
    wb = openpyxl.load_workbook(filepath, data_only=True, read_only=True)
    try:
        natija = {}
        for ws in wb.worksheets:
            qatorlar = []
            # `balandlik` — QIYMATI BOR oxirgi qator (preflight aniqlagan).
            # Undan narisi faqat format izi: e'lon qilingan 1 048 508 qatorni
            # aylanib chiqish o'nlab daqiqa oladi va hech narsa qo'shmaydi.
            chegara = min(balandlik or MAX_SHEET_ROWS, MAX_SHEET_ROWS)
            for qator in ws.iter_rows(max_col=max(kenglik, 1),
                                      max_row=chegara, values_only=True):
                qatorlar.append(list(qator))
                if len(qatorlar) > MAX_SHEET_ROWS:
                    raise ExcelTooLargeError(
                        f"«{ws.title}» varag'ida {MAX_SHEET_ROWS} dan ko'p qator")
            natija[ws.title] = qatorlar
        return natija
    finally:
        wb.close()


_NOMLAR_RE = re.compile(rb"<definedNames>.*?</definedNames>", re.S)
_NOMLAR_XATOSI = "could not assign names"


def _nomlarni_tozala(filepath: str) -> str:
    """`<definedNames>` bo'limi buzuq faylning tozalangan NUSXASINI yasaydi.

    openpyxl bunday faylda «Unable to read workbook: could not assign names»
    bilan yiqiladi va butun hujjat o'qilmay qoladi. Nomlar (Excel'dagi
    nomlangan diapazonlar) bizga KERAK EMAS — ularni olib tashlab, faylni
    normal o'qiymiz. Asl faylga TEGILMAYDI.
    """
    nusxa = os.path.join(tempfile.mkdtemp(prefix="xlsx_nom_"),
                         os.path.basename(filepath))
    with zipfile.ZipFile(filepath) as manba, \
            zipfile.ZipFile(nusxa, "w", zipfile.ZIP_DEFLATED) as nishon:
        for info in manba.infolist():
            data = manba.read(info)
            if info.filename == "xl/workbook.xml":
                data = _NOMLAR_RE.sub(b"", data)
            nishon.writestr(info, data)
    return nusxa


# ── Ulkan varaq XML ni OQIMLI qirqish (2026-09-08) ─────────────────────────
# Qiymatsiz katak: o'z-o'zini yopadigan `<c r="A1" s="3"/>` YOKI ichida
# `<v>`/`<is>`/`<f>` bo'lmagan `<c ...></c>`. `<c\b` — `<col>`, `<cols>`,
# `<cfRule>`, `<conditionalFormatting>` ga tegmaydi (so'z chegarasi).
# Formulali katak (`<f>`) SAQLANADI — keshlangan qiymati bo'lmasa ham.
_BOSH_KATAK_RE = re.compile(rb"<c\b[^>]*/>|<c\b[^>]*>(?:(?!<v|<is|<f|</c>).)*</c>", re.S)
# Kataklari tashlangach bo'sh qolgan qator: `<row r="5" spans="1:10"/>` yoki
# `<row ...></row>`. Katak manzillari MUTLAQ (`r="A12"`), shuning uchun bo'sh
# qatorni olib tashlash qolganlarini surmaydi.
_BOSH_QATOR_RE = re.compile(rb"<row\b[^>]*/>|<row\b[^>]*>\s*</row>", re.S)


def _qirq_oqim(src, dst, chegara=None):
    """`src` oqimini `<row>` chegaralarida bo'lakma-bo'lak qirqib `dst` ga yozadi.

    Xotirada bir vaqtda ~2 bo'lak turadi (16 MB) — 370 MB XML ham sig'adi.
    Bo'lak oxiridagi chala qator keyingi bo'lakka ko'chiriladi, shuning
    uchun regex hech qachon chala `<c>` ko'rmaydi (bir qator 8 MB dan katta
    bo'lsa ham xavfsiz: chala katak `/>` yoki `</c>` siz mos kelmaydi va
    o'zgarishsiz o'tadi).

    Qaytaradi: (o'qilgan_bayt, yozilgan_bayt). `chegara` oshsa
    ExcelTooLargeError — haqiqiy zip-bomba.
    """
    qoldiq = b""
    oqildi = yozildi = 0
    while True:
        b = src.read(_QIRQISH_BOLAK)
        if not b:
            break
        oqildi += len(b)
        if chegara is not None and oqildi > chegara:
            raise ExcelTooLargeError(
                f"varaq XML {chegara // 1048576} MB dan katta — zip bomba")
        b = qoldiq + b
        kes = b.rfind(b"<row")
        if kes <= 0:
            kes = len(b)
        qism, qoldiq = b[:kes], b[kes:]
        qism = _BOSH_QATOR_RE.sub(b"", _BOSH_KATAK_RE.sub(b"", qism))
        dst.write(qism)
        yozildi += len(qism)
    if qoldiq:
        qism = _BOSH_QATOR_RE.sub(b"", _BOSH_KATAK_RE.sub(b"", qoldiq))
        dst.write(qism)
        yozildi += len(qism)
    return oqildi, yozildi


def _katta_varaqlarni_qirq(filepath: str) -> str:
    """Varaq XML qismi `MAX_XLSX_PART_BYTES` dan katta faylning QIRQILGAN
    nusxasini yasaydi (`_nomlarni_tozala` uslubi: vaqtinchalik papka, asl
    faylga tegilmaydi).

    Nega kerak (2026-09-08, jonli navbat): 76032 — 39.6 MB li .xlsx,
    `sheet3.xml` 328 MB ga ochiladi: 1 048 575 qator yozilgan, shundan
    qiymatli katak 43 202 ta, faqat-uslubli (bo'sh) katak 10.4 million;
    haqiqiy ma'lumot 5 932-qatorgacha. 138285 — 369 MB, 6 808 qiymat,
    16.8 million bo'sh katak. Ikkalasi ham to'ldirilgan halol smeta
    («Форма N 5»), ilgari «zip bomba bo'lishi mumkin» bilan o'lik bo'lardi.
    Qirqilgach: 328 MB → 2.5 MB (2.1 s), 369 MB → 0.3 MB (2.5 s), qiymatlar
    aynan saqlanadi (43 202 / 43 202).

    Faqat chegaradan OSHGAN varaq qismlari qirqiladi; qolgan hamma qism
    (merge, uslub, sharedStrings, workbook) o'zgarishsiz ko'chiriladi.
    Qirqishdan keyin ham qism katta bo'lsa — preflight yana
    XlsxPartTooLargeError beradi (haqiqiy ma'lumot shuncha); oqim
    `MAX_XLSX_QIRQISH_BYTES` dan oshsa — ExcelTooLargeError (bomba).
    """
    papka = tempfile.mkdtemp(prefix="xlsx_qirq_")
    nusxa = os.path.join(papka, os.path.basename(filepath))
    try:
        with zipfile.ZipFile(filepath) as manba, \
                zipfile.ZipFile(nusxa, "w", zipfile.ZIP_DEFLATED) as nishon:
            for info in manba.infolist():
                if (info.filename.startswith("xl/worksheets/")
                        and info.filename.endswith(".xml")
                        and info.file_size > MAX_XLSX_PART_BYTES):
                    vaqtinchalik = os.path.join(
                        papka, os.path.basename(info.filename) + ".qirq")
                    with manba.open(info) as src, open(vaqtinchalik, "wb") as dst:
                        oqildi, yozildi = _qirq_oqim(src, dst, MAX_XLSX_QIRQISH_BYTES)
                    warnings.warn(
                        f"ulkan varaq XML qirqildi ({info.filename}: "
                        f"{oqildi // 1048576} MB → {yozildi // 1024} KB, faqat "
                        f"qiymatsiz kataklar tashlandi): {os.path.basename(filepath)}")
                    nishon.write(vaqtinchalik, info.filename)
                    os.remove(vaqtinchalik)
                else:
                    nishon.writestr(info, manba.read(info))
    except BaseException:
        shutil.rmtree(papka, ignore_errors=True)
        raise
    return nusxa


def _read_xlsx(filepath: str) -> dict:
    """
    Read an .xlsx workbook, expanding merged cells so every cell in a merged
    region carries the top-left value.

    Guards against hostile files: absurdly large merge ranges are not expanded
    cell-by-cell, over-wide sheets are trimmed to their real data width, and a
    corrupt `<definedNames>` block is repaired in a temporary copy.

    Returns {sheet_name: list_of_rows}  where each row is a plain Python list.
    """
    try:
        return _read_xlsx_ichki(filepath)
    except XlsxPartTooLargeError:
        # Varaq XML chegaradan katta — ko'pincha «butun varaqqa format» izi.
        # Qiymatsiz kataklarni tashlab, kichik nusxada qayta o'qiymiz;
        # haqiqiy bomba `_katta_varaqlarni_qirq` yoki qayta preflight'da
        # baribir ExcelTooLargeError beradi.
        nusxa = _katta_varaqlarni_qirq(filepath)
        try:
            return _read_xlsx_ichki(nusxa)
        finally:
            shutil.rmtree(os.path.dirname(nusxa), ignore_errors=True)
    except ValueError as exc:
        if _NOMLAR_XATOSI not in str(exc):
            raise
        # Buzuq nomlangan diapazonlar — tozalangan nusxada qayta urinamiz.
        nusxa = _nomlarni_tozala(filepath)
        try:
            return _read_xlsx_ichki(nusxa)
        finally:
            shutil.rmtree(os.path.dirname(nusxa), ignore_errors=True)


def _read_xlsx_ichki(filepath: str) -> dict:
    haqiqiy, elon, haqiqiy_qator, elon_qator = _xlsx_preflight(filepath)

    # Ustunlar soni chegaradan oshsa — HAQIQIY ma'lumot kengligiga qaraymiz.
    # «68 qator x 16 132 ustun» degan fayllarda amalda faqat dastlabki bir
    # necha ustunda qiymat bor; qolgani uslub izi. Bunday hujjatni rad etish
    # uchun asos yo'q (buyurtmachi ko'rsatmasi, 2026-08-19) — uni tor kenglik
    # bilan oqim orqali o'qiymiz. Haqiqiy kenglik ham chegaradan oshsa,
    # rad etmasdan CHEGARAGACHA qirqamiz (undan narisi deyarli har doim shovqin).
    #
    # QATORLAR uchun ham AYNAN shu holat (2026-09-02 da jonli topildi):
    # 104 KB li fayl `dimension="A1:IO1048508"` deb e'lon qiladi, XML da esa
    # atigi 1 423 ta `<row>` bor — butun varaqqa format berilgani uchun.
    # Oddiy yo'l bilan o'qilsa openpyxl 1 048 508 x 249 bo'sh katak yaratib
    # o'nlab daqiqa ishlaydi, keyin «juda ko'p qator» deb RAD etiladi.
    # Haqiqiy qatorlar chegaradan kam bo'lsa — oqim rejimida o'qiymiz.
    if elon > MAX_SHEET_COLS or elon_qator > MAX_SHEET_ROWS:
        return _read_xlsx_keng(filepath,
                               min(haqiqiy or MAX_SHEET_COLS, MAX_SHEET_COLS),
                               haqiqiy_qator)

    wb = openpyxl.load_workbook(filepath, data_only=True)
    result = {}
    # `wb.sheetnames` DIAGRAMMA varaqlarini (chartsheet) ham qaytaradi, ular
    # esa `max_row`/`iter_rows` ni umuman bilmaydi — `wb[nom]` bilan olingan
    # `Chartsheet` da `AttributeError` bo'lib, BUTUN hujjat «ochib bo'lmadi»
    # deb rad etilardi (id=122844; fayl SOG'LOM, nuqson bizda edi).
    # `wb.worksheets` faqat ma'lumot varaqlarini beradi — diagrammada
    # baribir katak yo'q, ya'ni verdiktga qo'shadigan hech narsasi yo'q.
    #
    # QAMROV (2026-09-07 ko'rigida aniqlashtirildi): bu tuzatish ZAXIRA
    # ZANJIRIGA KIRMAYDI va ataylab `TIKLASH_YOQ` ga BO'YSUNMAYDI. U asosiy
    # o'qish yo'lidagi mustaqil nuqson tuzatishi: `TIKLASH_YOQ=0` bilan
    # orqaga qaytarish TIKLASHNI o'chirish uchun, ma'lum bir `AttributeError`
    # yiqilishini QAYTA TIKLASH uchun emas. Sog'lom fayllarga ta'siri yo'q —
    # 2 295 faylli ekvivalentlik yurishida 0 farq (korpusdagi 1 954 ta
    # `.xlsx` da chartsheet umuman uchramaydi).
    for ws in wb.worksheets:
        shname = ws.title

        # Qator soni — haqiqiy chegara (xotira). Ustun soni esa QIRQILADI:
        # keng e'lon qilingan varaq rad etish uchun asos emas.
        if ws.max_row > MAX_SHEET_ROWS:
            raise ExcelTooLargeError(
                f"«{shname}» varag'ida juda ko'p qator "
                f"({ws.max_row}; chegara {MAX_SHEET_ROWS})")
        max_col = min(ws.max_column, MAX_SHEET_COLS)

        # Build a (row, col) -> value map for all merged cell ranges
        merge_map: dict = {}
        for merge_range in ws.merged_cells.ranges:
            n_rows = merge_range.max_row - merge_range.min_row + 1
            n_cols = merge_range.max_col - merge_range.min_col + 1
            if n_rows * n_cols > MAX_MERGE_CELLS:
                # Haqiqiy hujjatlarda bunday diapazon bo'lmaydi — ochmaymiz.
                continue
            tl_value = ws.cell(merge_range.min_row, merge_range.min_col).value
            for r in range(merge_range.min_row, merge_range.max_row + 1):
                for c in range(merge_range.min_col, merge_range.max_col + 1):
                    merge_map[(r, c)] = tl_value

        rows = []
        for row_idx, row in enumerate(ws.iter_rows(max_col=max_col or None,
                                                   values_only=False), start=1):
            row_vals = []
            for col_idx, cell in enumerate(row, start=1):
                if (row_idx, col_idx) in merge_map:
                    row_vals.append(merge_map[(row_idx, col_idx)])
                else:
                    row_vals.append(cell.value)
            rows.append(row_vals)
        result[shname] = rows
    return result


def _xls_ochish(filepath: str):
    """`xlrd.open_workbook`, SST (umumiy matn lug'ati) buzuq bo'lsa ham.

    MUAMMO (2026-09-02 da jonli topildi): platformadagi ba'zi `.xls` fayllar
    Excel bilan emas, boshqa generator (PHP kutubxonasi) bilan yasalgan va
    ularning SST yozuvida «nechta satr bor» hisoblagichi haqiqiy songa mos
    kelmaydi. Excel bunday faylni MULOYIM ochadi (boridan foydalanadi),
    xlrd esa `assert _unused_i == nstrings - 1` bilan yiqiladi — hatto xato
    matnisiz (`AssertionError('')`), shuning uchun izoh ham bo'sh chiqardi.

    Oqibati OG'IR edi: Excel ochadigan HALOL to'ldirilgan hujjat
    «Faylni ochib bo'lmadi» deb RAD etilardi (ustuvorlik qoidasiga zid),
    buyurtmachi shabloni bo'lsa — butun tender verdiktsiz qolardi
    (tender 279238, 279700 — 9 ta fayl shu sababdan turib qolgan edi).

    Yechim: yiqilgan holatdagina `xlrd.book.handle_sst` ni vaqtincha
    yumshoq nusxa bilan almashtiramiz — u NECHTA satr bo'lsa shuncha
    o'qiydi va assert qilmaydi. Faqat MATN lug'atiga tegadi; sonlar
    (narxlar) alohida yozuvlarda saqlanadi va bu yo'ldan ta'sirlanmaydi.
    """
    try:
        return xlrd.open_workbook(filepath)
    except AssertionError:
        pass                              # pastda yumshoq yo'l bilan urinamiz

    import warnings
    from xlrd import book as _xbook

    asl = _xbook.unpack_SST_table

    def _yumshoq(datatab, nstrings):
        """xlrd PARSERINING O'ZI, faqat «nechta o'qildi» ga chidamli.

        Kodni ko'chirmaymiz (xlrd versiyasi o'zgarsa buzilmasin) — o'rniga
        eng katta o'qiladigan sonni ikkilik qidiruv bilan topamiz (~13
        urinish), qolgan o'rinlar bo'sh satr bilan to'ldiriladi. Bo'sh
        satr MUHIM: aks holda katak lug'atga murojaat qilganda IndexError
        bo'lib, fayl baribir o'qilmay qolardi.
        """
        try:
            return asl(datatab, nstrings)
        except (AssertionError, IndexError, ValueError, struct.error):
            pass
        past, yuqori = 0, nstrings
        while past < yuqori:
            orta = (past + yuqori + 1) // 2
            try:
                asl(datatab, orta)
                past = orta
            except (AssertionError, IndexError, ValueError, struct.error):
                yuqori = orta - 1
        satrlar, izohlar = asl(datatab, past) if past else ([], {})
        satrlar = list(satrlar) + [""] * (nstrings - len(satrlar))
        warnings.warn(
            f"SST to'liq emas ({past}/{nstrings} satr) — boridan "
            f"foydalanildi: {os.path.basename(filepath)}")
        return satrlar, izohlar

    _xbook.unpack_SST_table = _yumshoq
    try:
        return xlrd.open_workbook(filepath)
    finally:
        _xbook.unpack_SST_table = asl


def _read_xls(filepath: str) -> dict:
    """
    Read an .xls workbook.

    xlrd cell types: 0=empty, 1=text, 2=number, 3=date, 4=bool, 5=error.
    Empty cells are normalised to None; error cells keep their Excel error
    text ("#VALUE!" etc.) so downstream ERROR_VALUE checks work for .xls too.

    Returns {sheet_name: list_of_rows}.
    """
    try:
        from xlrd.biffh import error_text_from_code
    except ImportError:
        error_text_from_code = {}
    wb = _xls_ochish(filepath)
    result = {}
    for sh in wb.sheets():
        rows = []
        for r in range(sh.nrows):
            row = []
            for c in range(sh.ncols):
                cell = sh.cell(r, c)
                if cell.ctype == 0:        # empty
                    row.append(None)
                elif cell.ctype == 5:      # error -> "#VALUE!" kabi matn
                    row.append(error_text_from_code.get(cell.value, "#VALUE!"))
                else:
                    row.append(cell.value)
            rows.append(row)
        result[sh.name] = rows
    return result


# ── Zaxira zanjiri: faqat ASOSIY YO'L XATO BERGANDA ───────────────────────
#
# 92 ta hujjat «Faylni ochib bo'lmadi» deb RAD etilgan edi — 2026-09-04 dagi
# tekshiruv hammasi tiklanishini isbotladi, ya'ni rad BIZNING o'quvchimizning
# cheklovi edi, hujjatning aybi emas. Loyihaning bosh qoidasi bo'yicha
# («noo'rin rad noo'rin qabuldan yomonroq») bunday fayl oxirigacha ochilishi
# kerak.
#
# Zanjir uchta qat'iy shartga bo'ysunadi:
#   1. Har usul FAQAT O'ZIGA tegishli xatoda uriniladi (RC4 — «encrypted»,
#      xom XML — `KeyError`, nol-bayt — `BadZipFile`). Ko'r-ko'rona hammasini
#      sinash sekin va chalg'ituvchi bo'lardi.
#   2. Tiklash qatlami HECH QACHON yangi xato tug'dirmaydi: har qanday
#      nosozlikda `None` qaytadi va chaqiruvchi ASL xatoni ko'taradi.
#   3. Tiklangan nusxa ODDIY o'quvchidan o'tadi (`_read_xlsx`/`_read_xls`),
#      shuning uchun MAX_SHEET_* / MAX_MERGE_CELLS kabi xavfsizlik
#      chegaralari unga ham tatbiq etiladi — ta'mir orqali zip-bomba
#      kirib kelmaydi.


def _tiklash_yoqilganmi() -> bool:
    """Butun zaxira zanjirining o'chirish kaliti (standart: YOQIQ).

    Modullarning har biri ham shu o'zgaruvchini o'zi tekshiradi; bu yerdagi
    tekshiruv ularni IMPORT ham qilmaslik uchun — o'chirilgan tizimda zanjir
    umuman mavjud bo'lmagandek ishlaydi.
    """
    return os.environ.get("TIKLASH_YOQ", "1").strip() not in ("0", "no", "false")


def _zaxira_rc4(filepath: str, asl_xato: BaseException, hisobot: dict = None):
    """Standart parol bilan shifrlangan `.xls` (VelvetSweatshop yoki bo'sh)."""
    from tender_engine import tiklash_rc4
    if not tiklash_rc4.shifrlangan_xatomi(asl_xato):
        return None
    with tiklash_rc4.tiklangan(filepath) as nusxa:
        if nusxa is None:
            return None
        # Nusxa WITH ichida o'qilishi SHART — chiqishda temp papka o'chadi.
        return _read_xls(nusxa)


def _zaxira_xom_xml(filepath: str, asl_xato: BaseException, hisobot: dict = None):
    """Zipdan `workbook.xml`/`sharedStrings.xml` kabi qismlar yo'qolgan fayl.

    `kerakmi()` faqat `KeyError` da True beradi: sog'lom faylda ham
    `sharedStrings.xml` qonuniy ravishda bo'lmasligi mumkin, shuning uchun
    bu tekshiruv o'qish XATO berganidan keyingina o'rinli.
    """
    from tender_engine import tiklash_xom_xml
    if not tiklash_xom_xml.kerakmi(filepath, asl_xato):
        return None
    # `tiklab_oq` nusxani o'zi tozalaydi va har qanday nosozlikda AYNAN
    # `asl_xato` ni qayta ko'taradi — uni pastda tutib `None` ga aylantiramiz.
    ich = {}
    natija = tiklash_xom_xml.tiklab_oq(filepath, _read_xlsx, asl_xato,
                                       hisobot=ich)
    if hisobot is not None and ich.get("ss_orinbosar"):
        hisobot["matn_tiklanmadi"] = True
    return natija


def _zaxira_nol_bayt(filepath: str, asl_xato: BaseException, hisobot: dict = None):
    """Yo'lda MATN deb ko'chirilgan `.xlsx` — har 0x00 bayt 0x20 ga aylangan."""
    if not isinstance(asl_xato, zipfile.BadZipFile):
        return None
    from tender_engine import tiklash_nol_bayt
    ich = {}
    nusxa = tiklash_nol_bayt.tikla(filepath, hisobot=ich)
    try:
        natija = _read_xlsx(nusxa)
    finally:
        shutil.rmtree(os.path.dirname(nusxa), ignore_errors=True)
    if hisobot is not None and ich.get("ss_orinbosar"):
        hisobot["matn_tiklanmadi"] = True
    return natija


def _zaxira_xlsb(filepath: str, asl_xato: BaseException, hisobot: dict = None):
    """XLSB — buzilish emas, BOSHQA format (zip sog'lom, ish kitobi ikkilik).

    Xato turiga qarab ajratib bo'lmaydi (openpyxl `OSError`, xlrd o'zining
    `XLRDError` ini beradi), shuning uchun darvoza mazmun bo'yicha:
    `xl/workbook.bin` bor-yo'qligi. Haqiqiy `.xlsx` da bunday qism hech
    qachon bo'lmaydi. Shu sababli zanjirning OXIRIDA turadi — xato turi
    aniq bo'lgan usullar birinchi navbatni oladi.
    """
    from tender_engine import tiklash_xlsb
    return tiklash_xlsb.tikla(filepath, asl_xato=asl_xato)


#: Zanjir tartibi: darvozasi eng ARZON va eng ANIQ bo'lgani birinchi.
_ZAXIRA_ZANJIRI = (_zaxira_rc4, _zaxira_xom_xml, _zaxira_nol_bayt, _zaxira_xlsb)


def _zaxira_zanjiri(filepath: str, asl_xato: BaseException, hisobot: dict = None):
    """Tiklash usullarini navbat bilan sinaydi. HECH QACHON istisno bermaydi.

    Qaytadi: varaqlar lug'ati, yoki `None` — hech biri yordam bermadi
    (chaqiruvchi ASL xatoni qayta ko'taradi, 5-xavfsizlik sharti).

    Har usul O'Z lug'atiga yozadi va u FAQAT muvaffaqiyatda `hisobot` ga
    ko'chiriladi: aks holda yiqilgan usulning belgisi (masalan
    `matn_tiklanmadi`) keyin ishlagan usulning natijasiga yopishib qolardi.
    """
    if not _tiklash_yoqilganmi():
        return None
    for usul in _ZAXIRA_ZANJIRI:
        ich = {}
        try:
            natija = usul(filepath, asl_xato, ich)
        except Exception:
            # Tiklash moduli yiqildi (yoki ataylab `asl_xato` ni qaytardi,
            # yoki umuman import bo'lmadi — deploy manifesti nuqsoni).
            # Bularning HAMMASI bir xil oqibatga olib keladi: keyingi usulga
            # o'tamiz, oxirida esa asl xato ko'tariladi.
            continue
        if natija is not None:
            if hisobot is not None:
                hisobot.update(ich)
                hisobot["usul"] = usul.__name__
            return natija
    return None


def _kengaytma_boyicha(filepath: str) -> dict:
    """Kengaytmaga qarab tegishli o'quvchiga yuboradi (AVVALGI xatti-harakat)."""
    ext = os.path.splitext(filepath)[1].lower()
    if ext == ".xlsx":
        return _read_xlsx(filepath)
    elif ext == ".xls":
        return _read_xls(filepath)
    else:
        raise ValueError(f"Unsupported file format: '{ext}'")


def read_file(filepath: str, hisobot: dict = None) -> dict:
    """
    Dispatch to the appropriate reader based on file extension.

    Returns {sheet_name: list_of_rows} or raises on error.

    Asosiy yo'l xato bersa — zaxira zanjiri (yuqoriga qarang). Sog'lom fayl
    bu yerdan AYNAN avvalgidek o'tadi: `try` bloki xato bo'lmaganda hech
    qanday qo'shimcha ish bajarmaydi va `hisobot` ga TEGILMAYDI (bo'sh
    lug'at = «oddiy yo'l», tiklash bo'lmagan).

    `hisobot` — ixtiyoriy chiqish lug'ati. Tiklash orqali o'qilganda
    to'ldiriladi:
      • `usul` — qaysi zaxira usuli yordam berdi;
      • `matn_tiklanmadi` — MATN lug'ati (`sharedStrings.xml`) zipda umuman
        yo'q edi va o'rinbosar bilan almashtirildi. Bunday hujjatda SONLAR
        to'liq, MATN esa yo'q — chaqiruvchi matnga tayangan mazmun da'vosini
        (sarlavha, band tavsifi, ustun nomi) qilmasligi SHART.
    """
    try:
        return _kengaytma_boyicha(filepath)
    except Exception as asl_xato:
        natija = _zaxira_zanjiri(filepath, asl_xato, hisobot)
        if natija is None:
            raise               # ASL xato — ishtirokchi ko'radigan izoh o'zgarmasin
        return natija
