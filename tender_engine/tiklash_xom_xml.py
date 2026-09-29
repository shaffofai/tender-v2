# -*- coding: utf-8 -*-
"""Yetishmagan OOXML qismlarini sintez qilib `.xlsx` ni tiklash (KeyError yo'li).

MUAMMO (2026-09-04 da o'lchangan): platformadagi 46 ta hujjatning zip qobig'i
SOG'LOM va `xl/worksheets/sheetN.xml` joyida turibdi, lekin paketning
«qo'shimcha» qismlari yo'q — 46/46 da `xl/workbook.xml`, 44/46 da
`xl/sharedStrings.xml`, bittasida `xl/styles.xml` ham. openpyxl bu qismlarni
nom bo'yicha o'qiydi va `KeyError: "There is no item named 'xl/workbook.xml'"`
bilan yiqiladi — natijada hujjat «Faylni ochib bo'lmadi» deb RAD etilardi.
Bu BIZNING o'quvchimizning cheklovi, hujjatning aybi emas (ustuvorlik qoidasi:
noo'rin rad noo'rin qabuldan yomonroq).

YECHIM: yetishmagan qismlarni VAQTINCHALIK NUSXAda sintez qilamiz va faylni
ODATDAGI o'quvchiga qaytaramiz. Shu yo'l ataylab tanlangan — merge kataklarni
ochish, kenglik/qator qirqish va xavfsizlik chegaralari (`MAX_SHEET_ROWS`,
`MAX_MERGE_CELLS` va h.k.) o'quvchidan BEPUL keladi, ya'ni tiklangan fayl
ham xuddi shu darvozalardan o'tadi (zip bomba ta'mir orqali kirib kelmaydi).

NARXLAR YO'QOLMAYDI: sonlar `sheetN.xml` ichida `<c r="D5"><v>123</v></c>`
ko'rinishida yotadi, `sharedStrings.xml` esa FAQAT matn lug'ati. Bu nazorat
tajribasi bilan isbotlangan (sog'lom fayldan sharedStrings o'chirilganda
chiqarilgan sonlar to'plami AYNAN teng qoldi).

MATN esa QAYTMAYDI — u zipda umuman yo'q. Sintez qilingan lug'at o'rniga
bo'sh joy qo'yiladi, ya'ni tiklangan hujjatda sarlavhalar bo'lmaydi va rol/
ustun tanish ishlamaydi.

SHUNING UCHUN TIKLASH O'Z ISHONCHLILIGINI E'LON QILADI: `tikla(hisobot=...)`
va `tiklab_oq(hisobot=...)` `hisobot["ss_orinbosar"]` ni to'ldiradi, reader esa
uni `read_file(hisobot=...)` orqali yuqoriga uzatadi (`matn_tiklanmadi`).
Bu MAJBURIY (2026-09-07 ko'rigi): belgisiz qolganda matni yulingan hujjat
oddiy yo'ldan o'tib, MATNGA tayangan mazmun da'volari bilan rad etilardi —
«sarlavha topilmadi», «bandlarga qiymat qo'yilmagan». Nazorat tajribasi:
sog'lom, TO'G'RI TO'LDIRILGAN hujjatdan `sharedStrings.xml` olib tashlansa
verdikt 1 -> 2 ga o'tar, ayni paytda dvigatelning O'ZI hujjatda 29 398 ta
yangi son ko'rib turardi (band tavsiflari yo'qolgani uchun band=2).
Endi bunday hujjat FAQAT sonlar bo'yicha hukm qilinadi
(`decision.hukm_shakl_erkin(..., matn_ishonchsiz=True)`).

XAVFSIZLIK SHARTLARI (buzilmasin):
  * Tiklash FAQAT o'qish XATO berganda chaqiriladi — sog'lom fayl bu modulga
    umuman kirmaydi (`kerakmi` ham qismlar YETISHMAGANIDA True beradi).
  * Asl faylga TEGILMAYDI: butun ish vaqtinchalik papkadagi nusxada.
  * Tiklash muvaffaqiyatsiz bo'lsa ASL xato qayta ko'tariladi (`tiklab_oq`),
    ishtirokchiga ko'rinadigan izoh o'zgarmaydi.
  * Qat'iy budjet: vaqt, qism soni va ochilgan bayt hajmi (env bilan
    sozlanadi) — 2026-09-02 dagi O(qator^2) livelock takrorlanmasin.
  * O'chirish kaliti: `TIKLASH_YOQ=0` (hamma tiklash yo'llari) yoki
    `TIKLASH_XOM_XML_YOQ=0` (faqat shu modul). Standart — YOQIQ.
"""

import os
import re
import shutil
import tempfile
import time
import warnings
import zipfile
import xml.etree.ElementTree as ET
from xml.sax.saxutils import quoteattr

# ── Nom fazolari va mazmun turlari ──────────────────────────────────────────
_NS_SS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_NS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_NS_PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
_NS_CT = "http://schemas.openxmlformats.org/package/2006/content-types"
_NS_APP = "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
_NS_VT = "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"

_CT_WB = ("application/vnd.openxmlformats-officedocument."
          "spreadsheetml.sheet.main+xml")
_CT_VARAQ = ("application/vnd.openxmlformats-officedocument."
             "spreadsheetml.worksheet+xml")
_CT_SST = ("application/vnd.openxmlformats-officedocument."
           "spreadsheetml.sharedStrings+xml")
_CT_STYLES = ("application/vnd.openxmlformats-officedocument."
              "spreadsheetml.styles+xml")

_WB_NOM = "xl/workbook.xml"
_SST_NOM = "xl/sharedStrings.xml"
_STYLES_NOM = "xl/styles.xml"
_CT_NOM = "[Content_Types].xml"
_ROOT_RELS = "_rels/.rels"
_WB_RELS = "xl/_rels/workbook.xml.rels"

_XML_BOSH = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'

# Yo'qligi HAR DOIM o'qishni buzadigan qismlar.
_HAR_DOIM_KERAK = (_WB_NOM, _CT_NOM, _ROOT_RELS, _WB_RELS)
# Yo'qligi FAQAT havola qilinganda buzadigan qismlar: matni butunlay
# «inlineStr» bo'lgan sog'lom faylda `sharedStrings.xml` qonuniy ravishda
# bo'lmaydi (openpyxl ning o'zi shunday yozadi), uslubsiz faylda esa
# `styles.xml`. Ularni shartsiz «yetishmagan» deb sanash sog'lom faylni
# tiklash yo'liga tortib kelardi.
_SHARTLI = (_SST_NOM, _STYLES_NOM)


# ── Budjet va chegaralar (hammasi env bilan sozlanadi) ──────────────────────
def _int_env(nom, standart):
    """Muhit o'zgaruvchisini butun songa o'giradi, buzuq qiymatda standart."""
    try:
        return int(os.environ.get(nom, standart))
    except (TypeError, ValueError):
        return int(standart)


def _float_env(nom, standart):
    try:
        return float(os.environ.get(nom, standart))
    except (TypeError, ValueError):
        return float(standart)


# Vaqt budjeti — tiklash cho'zilib ketsa lease (15 daq) tugamasligi uchun.
BUDJET_SEK = _float_env("TIKLASH_XOM_XML_BUDJET_SEK", 30.0)
# Zip a'zolari soni — «million kichik fayl» ko'rinishidagi bombaga qarshi.
MAX_QISM = _int_env("TIKLASH_XOM_XML_MAX_QISM", 4096)
# Bitta qismning OCHILGAN hajmi (reader.py bilan AYNAN bir xil env nomi).
MAX_QISM_BAYT = _int_env("MAX_XLSX_PART_BYTES", str(200 * 1024 * 1024))
# Nusxaga ko'chiriladigan JAMI ochilgan bayt — zip bomba diskni to'ldirmasin.
MAX_JAMI_BAYT = _int_env("TIKLASH_XOM_XML_MAX_JAMI_MB", 500) * 1024 * 1024
# Sintez qilinadigan lug'at/uslub o'lchamlari.
#
# DIQQAT: bu o'lcham HUJJATDAN keladi (`<c t="s"><v>N</v></c>` dagi N ni
# ishtirokchi to'liq nazorat qiladi), ya'ni bombani ta'mirning O'ZI yasashi
# mumkin. Ilgari chegara 2 000 000 edi — o'lchandi: bunday lug'atni yasash
# va openpyxl bilan qayta o'qish ~80 s / 180 MB oladi. Korpusdagi haqiqiy
# maksimum 31 634, shuning uchun 200 000 olti barobar zaxira beradi.
MAX_SST = _int_env("TIKLASH_XOM_XML_MAX_SST", 200_000)
MAX_XF = _int_env("TIKLASH_XOM_XML_MAX_XF", 100_000)

_KOCHIRISH_BLOK = 1 << 20   # 1 MB — a'zolar oqim bilan ko'chiriladi


class TiklashXatosi(Exception):
    """Xom XML tiklash yo'li ishlamadi — chaqiruvchi ASL xatoni ko'tarsin."""


class TiklashKerakEmas(TiklashXatosi):
    """Fayl bu yo'lga tegishli emas (zip emas yoki qismlar joyida)."""


class TiklashOchirilgan(TiklashXatosi):
    """`TIKLASH_YOQ=0` / `TIKLASH_XOM_XML_YOQ=0` bilan o'chirilgan."""


class TiklashBudjeti(TiklashXatosi):
    """Vaqt yoki hajm budjeti tugadi — fayl juda katta/zararli bo'lishi mumkin."""


def _yoqilganmi():
    """Modul yoqilganmi (standart: ha)."""
    for nom in ("TIKLASH_YOQ", "TIKLASH_XOM_XML_YOQ"):
        if os.environ.get(nom, "1").strip() in ("0", "no", "false", "FALSE"):
            return False
    return True


class _Budjet:
    """Vaqt va bayt hisoblagichi — chegaradan oshsa TOZA istisno beradi.

    Tiklashda qidiruv/ta'mir bor, ya'ni 2026-09-02 dagi livelock (bitta fayl
    60 daqiqa) takrorlanishi mumkin. Shuning uchun har qadamda tekshiriladi:
    cheksiz ishlagandan ko'ra tiklashdan VOZ KECHIB, asl xatoni qaytargan
    ma'qul — hujjat baribir avvalgi holatidan yomonlashmaydi.
    """

    def __init__(self, sek=None, jami_bayt=None):
        self.tugash = time.monotonic() + (BUDJET_SEK if sek is None else sek)
        self.qolgan_bayt = MAX_JAMI_BAYT if jami_bayt is None else jami_bayt

    def tekshir(self, qadam=""):
        if time.monotonic() > self.tugash:
            raise TiklashBudjeti(f"vaqt budjeti tugadi ({BUDJET_SEK} s){qadam}")

    def bayt(self, n, qadam=""):
        self.qolgan_bayt -= n
        if self.qolgan_bayt < 0:
            raise TiklashBudjeti(
                f"ochilgan hajm budjeti tugadi "
                f"({MAX_JAMI_BAYT // 1048576} MB){qadam}")


# ── Zip ichini o'qish yordamchilari ─────────────────────────────────────────
_VARAQ_RAQAM_RE = re.compile(r"(\d+)")
# t="s" — qiymati sharedStrings indeksiga havola qiladigan katak.
_SST_INDEKS_RE = re.compile(rb'<c[^>]*\bt="s"[^>]*>\s*<v>(\d+)</v>')
# s="N" — cellXfs dagi uslub indeksi.
_STIL_INDEKS_RE = re.compile(rb'<c[^>]*\bs="(\d+)"')
# dxfId="N" — shartli formatlash qoidasi `dxfs` ro'yxatiga murojaat qiladi.
_DXF_INDEKS_RE = re.compile(rb'\bdxfId="(\d+)"')
_CELLXFS_RE = re.compile(rb'<cellXfs\b[^>]*?count="(\d+)"[^>]*?(/?)>')


def _varaq_qismlari(nomlar):
    """Zipdagi varaq XML larini `sheetN` raqami bo'yicha tartiblab qaytaradi.

    Alifbo bo'yicha tartiblasa `sheet10` `sheet2` dan oldin kelib, varaqlar
    almashib ketardi — izohda «...jadvalida» noto'g'ri jadvalni ko'rsatardi.
    """
    varaqlar = [n for n in nomlar
                if n.startswith("xl/worksheets/") and n.endswith(".xml")
                and "_rels" not in n]

    def kalit(n):
        raqamlar = _VARAQ_RAQAM_RE.findall(os.path.basename(n))
        return (int(raqamlar[-1]) if raqamlar else 0, n)

    return sorted(varaqlar, key=kalit)


def _eng_katta_indekslar(z, varaqlar, budjet):
    """Varaqlardagi eng katta `t="s"`, `s="N"` va `dxfId="N"` indekslarini topadi.

    Sintez qilinadigan lug'at/uslub jadvali AYNAN shu qadar katta bo'lishi
    kerak: kichik bo'lsa openpyxl indeksga murojaat qilganda IndexError
    beradi va fayl baribir o'qilmay qolardi. `dxfId` — shartli formatlash
    qoidasining uslubi; nazorat tajribasida styles.xml yo'q faylda AYNAN shu
    `IndexError: list index out of range` bilan yiqilgan edi.
    """
    mx_sst = mx_stil = mx_dxf = -1
    for nom in varaqlar:
        budjet.tekshir(f" — «{nom}» skanida")
        info = z.getinfo(nom)
        if info.file_size > MAX_QISM_BAYT:
            raise TiklashBudjeti(
                f"«{nom}» ochilganda juda katta "
                f"({info.file_size // 1048576} MB) — zip bomba bo'lishi mumkin")
        with z.open(info) as fh:
            mazmun = fh.read(MAX_QISM_BAYT)
        budjet.bayt(len(mazmun), f" — «{nom}» skanida")
        for m in _SST_INDEKS_RE.finditer(mazmun):
            n = int(m.group(1))
            if n > mx_sst:
                mx_sst = n
        for m in _STIL_INDEKS_RE.finditer(mazmun):
            n = int(m.group(1))
            if n > mx_stil:
                mx_stil = n
        for m in _DXF_INDEKS_RE.finditer(mazmun):
            n = int(m.group(1))
            if n > mx_dxf:
                mx_dxf = n
    return mx_sst, mx_stil, mx_dxf


def _havola_bormi(z, varaqlar, naqsh, budjet):
    """Varaqlarning birortasida `naqsh` uchraydimi (birinchi topilishda to'xtaydi).

    Shartli qismlar (`sharedStrings`, `styles`) HAQIQATAN kerakmi — shuni
    hal qiladi. O'qib bo'lmaydigan darajada katta qism uchraса «kerak» deb
    hisoblaymiz: haqiqiy javobni tiklash bosqichi baribir aniqlaydi.
    """
    for nom in varaqlar:
        budjet.tekshir(f" — «{nom}» havola tekshiruvida")
        info = z.getinfo(nom)
        if info.file_size > MAX_QISM_BAYT:
            return True
        with z.open(info) as fh:
            mazmun = fh.read(MAX_QISM_BAYT)
        budjet.bayt(len(mazmun), f" — «{nom}» havola tekshiruvida")
        if naqsh.search(mazmun):
            return True
    return False


def _yetishmagan(z, nomlar, varaqlar, budjet):
    """Ochiq zipda yetishmayotgan muhim qismlar ro'yxati."""
    yoq = [q for q in _HAR_DOIM_KERAK if q not in nomlar]
    if yoq:
        return yoq            # arzon yo'l — 46/46 haqiqiy fayl shu yerda
    for qism, naqsh in ((_SST_NOM, _SST_INDEKS_RE), (_STYLES_NOM, _STIL_INDEKS_RE)):
        if qism not in nomlar and _havola_bormi(z, varaqlar, naqsh, budjet):
            yoq.append(qism)
    return yoq


def _varaq_nomlari(z, nomlar, soni):
    """HAQIQIY varaq nomlarini `docProps/app.xml` dan tiklaydi.

    `workbook.xml` yo'q bo'lsa varaq nomlari ham yo'q, lekin ular ko'pincha
    `app.xml` ning `TitlesOfParts` ro'yxatida saqlanib qoladi (46/46 faylda
    shunday) — «RES», «Дефектный акт» kabi haqiqiy nomlar. Bu MUHIM, chunki
    nom ishtirokchiga ko'rinadigan izohda «...jadvalida» deb ishlatiladi va
    etalon bilan varaq juftlashda ham qatnashadi.

    Ro'yxatning boshidagi `soni` ta yozuv — varaqlar, qolgani nomlangan
    diapazonlar. Ishonch bo'lmasa (soni mos kelmasa, nom takrorlansa yoki
    Excel taqiqlagan belgi bo'lsa) None qaytadi — chaqiruvchi barqaror
    «Sheet1…» nomlariga o'tadi.
    """
    if "docProps/app.xml" not in nomlar:
        return None
    try:
        info = z.getinfo("docProps/app.xml")
        if info.file_size > 4 * 1024 * 1024:      # app.xml hech qachon bunchalik emas
            return None
        kok = ET.fromstring(z.read(info))
    except (KeyError, ET.ParseError, OSError, zipfile.BadZipFile):
        return None
    vektor = kok.find(f"{{{_NS_APP}}}TitlesOfParts/{{{_NS_VT}}}vector")
    if vektor is None:
        return None
    # Probel KESILMAYDI: Excel varaq nomi oxirida probelga ruxsat beradi
    # («2-1_ЛРВ КПП ») va etalon bilan varaq juftlash nom TENGLIGIga
    # tayanadi — kesilsa juftlik buzilib, jadval «o'chirilgan» ko'rinardi.
    nomlar_ro = [(el.text or "") for el in vektor.findall(f"{{{_NS_VT}}}lpstr")]
    if len(nomlar_ro) < soni:
        return None
    nomlar_ro = nomlar_ro[:soni]
    korilgan = set()
    for nom in nomlar_ro:
        # Excel qoidalari: bo'sh emas, 31 belgidan uzun emas, taqiqlangan
        # belgi yo'q, takrorlanmaydi. Biror sharti buzilsa BUTUN ro'yxatdan
        # voz kechamiz — yarmi haqiqiy, yarmi sintetik nom chalg'itadi.
        if not nom.strip() or len(nom) > 31 or set(nom) & set(r"[]:*?/\\"):
            return None
        past = nom.lower()
        if past in korilgan:
            return None
        korilgan.add(past)
    return nomlar_ro


# ── Yetishmagan qismlarni sintez qilish ─────────────────────────────────────
def _sst_yasa(soni):
    """`soni` ta bo'sh yozuvli sharedStrings.xml.

    Matnning O'ZI zipda yo'q — tiklab bo'lmaydi. Bo'sh joy («" "») qo'yiladi:
    bo'sh satr openpyxl da None ga aylanib, katak umuman yo'qday ko'rinardi.
    """
    if soni > MAX_SST:
        raise TiklashBudjeti(
            f"sharedStrings juda katta ({soni:,}; chegara {MAX_SST:,})")
    soni = max(soni, 0)
    return (f'{_XML_BOSH}<sst xmlns="{_NS_SS}" count="{soni}" '
            f'uniqueCount="{soni}">').encode() + \
        b"<si><t xml:space=\"preserve\"> </t></si>" * soni + b"</sst>"


def _standart_xf(xfid=True):
    return ('<xf numFmtId="0" fontId="0" fillId="0" borderId="0"'
            + (' xfId="0"/>' if xfid else "/>"))


def _styles_yasa(soni, dxf_soni=0):
    """`soni` ta uslubli minimal styles.xml (fayldan butunlay yo'q bo'lsa).

    `dxf_soni` — shartli formatlash uslublari. Bo'sh `<dxf/>` yetarli:
    bizga ko'rinish emas, INDEKSNING mavjudligi kerak (openpyxl ro'yxatdan
    `dxfId` bo'yicha oladi va yo'q bo'lsa IndexError bilan yiqiladi).
    """
    if max(soni, dxf_soni) > MAX_XF:
        raise TiklashBudjeti(
            f"uslublar juda ko'p ({max(soni, dxf_soni):,}; chegara {MAX_XF:,})")
    soni = max(soni, 1)
    xf = _standart_xf()
    dxfs = (f'<dxfs count="{dxf_soni}">{"<dxf/>" * dxf_soni}</dxfs>'
            if dxf_soni > 0 else "")
    return (f'{_XML_BOSH}<styleSheet xmlns="{_NS_SS}">'
            '<fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>'
            '<fills count="1"><fill><patternFill patternType="none"/></fill></fills>'
            '<borders count="1"><border><left/><right/><top/><bottom/>'
            '<diagonal/></border></borders>'
            f'<cellStyleXfs count="1">{xf}</cellStyleXfs>'
            f'<cellXfs count="{soni}">{xf * soni}</cellXfs>'
            '<cellStyles count="1">'
            '<cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
            f'{dxfs}</styleSheet>').encode("utf-8")


def _styles_kengaytir(xom, kerak):
    """Mavjud styles.xml dagi `cellXfs` ni `kerak` tagacha uzaytiradi.

    ALMASHTIRMAYMIZ, KENGAYTIRAMIZ: tayyor styles.xml da son formatlari
    (sana, valyuta) yotadi — uni tashlab yuborsak sanalar xom songa aylanib,
    to'ldirilganlik hisobi buziladi. Faqat yetishmagan uslub yozuvlari
    oxiriga qo'shiladi. Mos joy topilmasa None (chaqiruvchi tegmaydi).
    """
    m = _CELLXFS_RE.search(xom)
    if not m:
        return None
    bor = int(m.group(1))
    if bor >= kerak:
        return None
    if kerak > MAX_XF:
        raise TiklashBudjeti(f"cellXfs juda katta ({kerak:,}; chegara {MAX_XF:,})")
    qosh = (_standart_xf(xfid=b"cellStyleXfs" in xom) * (kerak - bor)).encode("utf-8")
    yangi_bosh = f'<cellXfs count="{kerak}">'.encode("utf-8")
    if m.group(2) == b"/":
        # `<cellXfs count="0"/>` — o'z-o'zini yopgan, ichi bo'sh.
        return xom[:m.start()] + yangi_bosh + qosh + b"</cellXfs>" + xom[m.end():]
    yopish = xom.find(b"</cellXfs>", m.end())
    if yopish == -1:
        return None
    return (xom[:m.start()] + yangi_bosh + xom[m.end():yopish]
            + qosh + xom[yopish:])


def _rels_yangila(xom, varaqlar, sintez_sst, sintez_styles):
    """workbook.xml.rels ni har bir varaq ko'rinadigan qilib to'ldiradi.

    Mavjud munosabatlar (mavzu, tashqi havolalar, `TargetMode="External"`)
    AYNAN saqlanadi — ular qayta yozilsa openpyxl boshqa joyda yiqilishi
    mumkin. Qaytadi: (rels_bayt|None, [(rId, varaq_qismi), ...] tartibda).
    """
    kok = None
    if xom is not None:
        try:
            kok = ET.fromstring(xom)
        except ET.ParseError:
            kok = None                    # buzuq rels — yangisini yasaymiz
    yangi = kok is None
    if yangi:
        kok = ET.Element(f"{{{_NS_PKG}}}Relationships")

    band = set()
    bor_varaq = {}                        # xl/… qism nomi -> rId
    turlari = set()
    for rel in list(kok):
        rid = rel.get("Id") or ""
        band.add(rid)
        tur = (rel.get("Type") or "").rsplit("/", 1)[-1]
        turlari.add(tur)
        if tur == "worksheet" and rel.get("TargetMode") != "External":
            nishon = (rel.get("Target") or "").lstrip("/")
            if not nishon.startswith("xl/"):
                nishon = "xl/" + nishon
            bor_varaq.setdefault(nishon, rid)

    ozgardi = yangi
    keyingi = 1

    def _yangi_rid():
        nonlocal keyingi
        while f"rId{keyingi}" in band:
            keyingi += 1
        rid = f"rId{keyingi}"
        band.add(rid)
        return rid

    for varaq in varaqlar:
        if varaq in bor_varaq:
            continue
        rid = _yangi_rid()
        ET.SubElement(kok, f"{{{_NS_PKG}}}Relationship", {
            "Id": rid, "Type": _NS_REL + "worksheet",
            "Target": varaq[len("xl/"):]})
        bor_varaq[varaq] = rid
        ozgardi = True

    for shart, tur, nishon in ((sintez_sst, "sharedStrings", "sharedStrings.xml"),
                               (sintez_styles, "styles", "styles.xml")):
        if shart and tur not in turlari:
            ET.SubElement(kok, f"{{{_NS_PKG}}}Relationship", {
                "Id": _yangi_rid(), "Type": _NS_REL + tur, "Target": nishon})
            ozgardi = True

    # Varaq tartibi: rId raqami bo'yicha (Excel varaqlarga rId1..rIdN ni
    # yorliq tartibida beradi — `app.xml` dagi nomlar ham SHU tartibda;
    # `.rels` dagi YOZILISH tartibi esa aralash bo'ladi, unga tayansak
    # varaqlar joyini almashtirib yuborardi).
    # rId raqamsiz bo'lsa qism nomidagi `sheetN` tartibiga qaytamiz.
    tartib_indeks = {varaq: i for i, varaq in enumerate(varaqlar)}

    def kalit(juft):
        raqam = _VARAQ_RAQAM_RE.findall(juft[1])
        return (int(raqam[0]) if raqam else 1 << 30, tartib_indeks[juft[0]])

    tartib = sorted(((varaq, bor_varaq[varaq]) for varaq in varaqlar), key=kalit)
    juftlar = [(rid, varaq) for varaq, rid in tartib]

    if not ozgardi:
        return None, juftlar
    # `us-ascii` (standart) — kirill nomlar belgi-havolaga aylanadi, XML
    # uchun bu to'g'ri va hech qanday kodlash e'loniga bog'liq emas.
    ET.register_namespace("", _NS_PKG)
    return _XML_BOSH.encode() + ET.tostring(kok), juftlar


def _workbook_yasa(juftlar, nomlar_ro):
    """Sintez qilingan workbook.xml — faqat varaqlar ro'yxati."""
    s = [f'{_XML_BOSH}<workbook xmlns="{_NS_SS}" xmlns:r="{_NS_REL}"><sheets>']
    for i, (rid, _varaq) in enumerate(juftlar):
        nom = nomlar_ro[i] if nomlar_ro else f"Sheet{i + 1}"
        s.append(f'<sheet name={quoteattr(nom)} sheetId="{i + 1}" r:id="{rid}"/>')
    s.append("</sheets></workbook>")
    return "".join(s).encode("utf-8")


def _ct_yangila(xom, varaqlar, qoshiladi):
    """[Content_Types].xml ga yetishmagan Override larni qo'shadi.

    openpyxl sharedStrings ni AYNAN shu manifestdan topadi — qismni zipga
    qo'shib, manifestni yangilamasak lug'at o'qilmay qolardi.
    """
    if xom is None:
        s = [f'{_XML_BOSH}<Types xmlns="{_NS_CT}">'
             '<Default Extension="rels" ContentType="application/vnd.'
             'openxmlformats-package.relationships+xml"/>'
             '<Default Extension="xml" ContentType="application/xml"/>']
        s.append(f'<Override PartName="/{_WB_NOM}" ContentType="{_CT_WB}"/>')
        for varaq in varaqlar:
            s.append(f'<Override PartName="/{varaq}" ContentType="{_CT_VARAQ}"/>')
        s.append(f'<Override PartName="/{_SST_NOM}" ContentType="{_CT_SST}"/>')
        s.append(f'<Override PartName="/{_STYLES_NOM}" ContentType="{_CT_STYLES}"/>')
        s.append("</Types>")
        return "".join(s).encode("utf-8")

    matn = xom.decode("utf-8", "replace")
    if "</Types>" not in matn:
        return None
    qosh = ""
    kerakli = [(f"/{_WB_NOM}", _CT_WB)]
    kerakli += [(f"/{v}", _CT_VARAQ) for v in varaqlar]
    if _SST_NOM in qoshiladi:
        kerakli.append((f"/{_SST_NOM}", _CT_SST))
    if _STYLES_NOM in qoshiladi:
        kerakli.append((f"/{_STYLES_NOM}", _CT_STYLES))
    for qism, ct in kerakli:
        if f'PartName="{qism}"' not in matn:
            qosh += f'<Override PartName="{qism}" ContentType="{ct}"/>'
    if not qosh:
        return None
    return matn.replace("</Types>", qosh + "</Types>").encode("utf-8")


def _root_rels_yasa():
    return (f'{_XML_BOSH}<Relationships xmlns="{_NS_PKG}">'
            f'<Relationship Id="rId1" Type="{_NS_REL}officeDocument" '
            f'Target="{_WB_NOM}"/></Relationships>').encode("utf-8")


# ── Ommaviy API ─────────────────────────────────────────────────────────────
def yetishmagan_qismlar(filepath, budjet=None):
    """Fayldan yetishmayotgan muhim OOXML qismlari ro'yxati (bo'sh = tegmaymiz).

    Odatda arzon: `workbook.xml` yo'q bo'lsa (46/46 holat) faqat zip katalogi
    o'qiladi. Faqat shartli qism yo'q bo'lsa varaqlar skanlanadi — u ham
    birinchi havolada to'xtaydi va budjetga bo'ysunadi.

    Zip emas yoki varaq XML i yo'q bo'lsa — bu modulning ishi emas
    (masalan `.xls`, buzuq zip): bo'sh ro'yxat.
    """
    try:
        with zipfile.ZipFile(filepath) as z:
            nomlar = set(z.namelist())
            varaqlar = _varaq_qismlari(nomlar)
            if not varaqlar:
                return []
            return _yetishmagan(z, nomlar, varaqlar, budjet or _Budjet())
    except (zipfile.BadZipFile, OSError, RuntimeError, TiklashBudjeti):
        return []


def kerakmi(filepath, xato=None):
    """Shu fayl uchun xom-XML tiklash mantiqan o'rinlimi.

    `xato` berilsa u KeyError bo'lishi shart — openpyxl yo'q qismni AYNAN
    shu bilan bildiradi. Boshqa xatolar (BadZipFile, XLRDError) boshqa
    tiklash yo'llariga tegishli, bu modul ularga aralashmaydi.

    DIQQAT: sog'lom faylda `sharedStrings.xml` matn bo'lmasa qonuniy
    ravishda yo'q bo'lishi mumkin — shuning uchun bu funksiya FAQAT o'qish
    xato berganidan keyin chaqirilsin.
    """
    if not _yoqilganmi():
        return False
    if xato is not None and not isinstance(xato, KeyError):
        return False
    return bool(yetishmagan_qismlar(filepath))


def tikla(filepath, budjet=None, hisobot=None):
    """Yetishmagan qismlari sintez qilingan VAQTINCHALIK nusxa yasaydi.

    Asl faylga tegilmaydi. Qaytadi: nusxa yo'li (`.xlsx` kengaytmasi bilan —
    mazmuni zip bo'lgani uchun nomi `.xls` bo'lsa ham). Nusxani chaqiruvchi
    `tozala()` bilan o'chiradi.

    `hisobot` — ixtiyoriy lug'at; muvaffaqiyatda to'ldiriladi:
    `qoshildi` (sintez qilingan qismlar) va `ss_orinbosar` — MATN lug'ati
    sintez qilinganmi. Ikkinchisi chaqiruvchi uchun MUHIM: bunday hujjatda
    sarlavhalar ham, band tavsiflari ham yo'q (zipda umuman yo'q edi), ya'ni
    MATNGA tayangan hech qanday mazmun da'vosi qilinmasligi kerak.

    Istisnolar: `TiklashOchirilgan`, `TiklashKerakEmas`, `TiklashBudjeti`
    (hammasi `TiklashXatosi`) — chaqiruvchi ASL xatoni ko'tarsin.
    """
    if not _yoqilganmi():
        raise TiklashOchirilgan("xom XML tiklash o'chirilgan (TIKLASH_YOQ=0)")
    budjet = budjet or _Budjet()
    papka = tempfile.mkdtemp(prefix="xlsx_xom_")
    nusxa = os.path.join(
        papka, os.path.splitext(os.path.basename(filepath))[0] + ".xlsx")
    try:
        qoshildi = _tikla_ichki(filepath, nusxa, budjet)
    except BaseException:
        shutil.rmtree(papka, ignore_errors=True)
        raise
    if hisobot is not None:
        hisobot["qoshildi"] = list(qoshildi)
        hisobot["ss_orinbosar"] = any(
            q.startswith("sharedStrings(") for q in qoshildi)
    warnings.warn(
        f"xom XML tiklandi ({', '.join(qoshildi)}): {os.path.basename(filepath)}")
    return nusxa


def _tikla_ichki(filepath, nusxa, budjet):
    """Sintez + nusxaga yozish. Qaytadi: qo'shilgan qismlar ro'yxati."""
    with zipfile.ZipFile(filepath) as z:
        nomlar = set(z.namelist())
        if len(z.infolist()) > MAX_QISM:
            raise TiklashBudjeti(
                f"zipda juda ko'p qism ({len(z.infolist())}; chegara {MAX_QISM})")
        varaqlar = _varaq_qismlari(nomlar)
        if not varaqlar:
            raise TiklashKerakEmas("zipda varaq XML i yo'q — bu yo'l mos emas")
        if not _yetishmagan(z, nomlar, varaqlar, budjet):
            # Hamma qism joyida — sog'lom fayl. TEGMAYMIZ (1-xavfsizlik sharti).
            raise TiklashKerakEmas("muhim qismlar joyida — tiklash kerak emas")

        mx_sst, mx_stil, mx_dxf = _eng_katta_indekslar(z, varaqlar, budjet)
        budjet.tekshir(" — sintez oldidan")

        qoshildi = []
        yangi = {}                       # qism nomi -> bayt (yoziladigan)

        # sharedStrings: faqat kataklar unga MUROJAAT qilsa kerak bo'ladi.
        if _SST_NOM not in nomlar and mx_sst >= 0:
            yangi[_SST_NOM] = _sst_yasa(mx_sst + 1)
            qoshildi.append(f"sharedStrings({mx_sst + 1})")

        if _STYLES_NOM not in nomlar:
            yangi[_STYLES_NOM] = _styles_yasa(mx_stil + 1, mx_dxf + 1)
            qoshildi.append(f"styles({mx_stil + 1})")
        elif mx_stil >= 0:
            keng = _styles_kengaytir(z.read(_STYLES_NOM), mx_stil + 1)
            if keng is not None:
                yangi[_STYLES_NOM] = keng
                qoshildi.append(f"styles+{mx_stil + 1}")

        rels_bayt, juftlar = _rels_yangila(
            z.read(_WB_RELS) if _WB_RELS in nomlar else None,
            varaqlar, _SST_NOM in yangi, _STYLES_NOM in yangi)
        if rels_bayt is not None:
            yangi[_WB_RELS] = rels_bayt
            qoshildi.append("workbook.rels")

        if _WB_NOM not in nomlar:
            nomlar_ro = _varaq_nomlari(z, nomlar, len(juftlar))
            yangi[_WB_NOM] = _workbook_yasa(juftlar, nomlar_ro)
            qoshildi.append("workbook(%s)" % ("app.xml nomlari" if nomlar_ro
                                              else "Sheet1…"))

        if _ROOT_RELS not in nomlar:
            yangi[_ROOT_RELS] = _root_rels_yasa()
            qoshildi.append(".rels")

        ct = _ct_yangila(z.read(_CT_NOM) if _CT_NOM in nomlar else None,
                         varaqlar, set(yangi))
        if ct is not None:
            yangi[_CT_NOM] = ct
            qoshildi.append("Content_Types")

        if not qoshildi:
            raise TiklashKerakEmas("sintez qilishga narsa topilmadi")

        _nusxa_yoz(z, nusxa, yangi, budjet)
    return qoshildi


def _nusxa_yoz(z, nusxa, yangi, budjet):
    """Asl a'zolarni + sintez qilingan qismlarni yangi zipga yozadi.

    TUZOQ (prototipda 46/46 shu sababdan yiqilgan edi): a'zoni ko'chirishda
    ASL `ZipInfo` ni QAYTA ISHLATMASLIK kerak. Undagi `flag_bits` (masalan
    ma'lumot deskriptori biti) ko'chirilsa yangi arxiv sarlavhasiga mos
    kelmay qoladi va o'qishda «BadZipFile: Bad magic number for file header»
    chiqadi. Shuning uchun faqat NOM beriladi, qolganini zipfile o'zi yozadi.
    """
    yozilgan = set()
    with zipfile.ZipFile(nusxa, "w", zipfile.ZIP_DEFLATED) as chiq:
        for info in z.infolist():
            nom = info.filename
            if info.is_dir() or nom in yozilgan or nom in yangi:
                continue
            budjet.tekshir(f" — «{nom}» ko'chirishda")
            if info.file_size > MAX_QISM_BAYT:
                raise TiklashBudjeti(
                    f"«{nom}» ochilganda juda katta "
                    f"({info.file_size // 1048576} MB) — zip bomba bo'lishi mumkin")
            budjet.bayt(info.file_size, f" — «{nom}» ko'chirishda")
            yozilgan.add(nom)
            # Oqim bilan: 200 MB li qismni xotiraga to'liq olmaymiz.
            with z.open(info) as manba, chiq.open(nom, "w") as nishon:
                shutil.copyfileobj(manba, nishon, _KOCHIRISH_BLOK)
        # SINTEZ QILINGAN qismlar ham budjetdan o'tadi. Ular ko'chirilayotgan
        # a'zolar emas, LEKIN o'lchami baribir hujjatga bog'liq (lug'at/uslub
        # indekslari hujjatdan olinadi) — ya'ni ularni budjetdan chiqarib
        # qo'yish «ta'mirning o'zi yasagan bomba» yo'lini ochiq qoldirardi.
        for nom, bayt in yangi.items():
            if nom in yozilgan:
                continue
            budjet.tekshir(f" — «{nom}» yozishda")
            if len(bayt) > MAX_QISM_BAYT:
                raise TiklashBudjeti(
                    f"sintez qilingan «{nom}» juda katta "
                    f"({len(bayt) // 1048576} MB)")
            budjet.bayt(len(bayt), f" — «{nom}» yozishda")
            yozilgan.add(nom)
            chiq.writestr(nom, bayt)


def tozala(nusxa):
    """`tikla()` yasagan vaqtinchalik papkani o'chiradi (xatoni yutadi)."""
    if nusxa:
        shutil.rmtree(os.path.dirname(nusxa), ignore_errors=True)


def tiklab_oq(filepath, oquvchi, asl_xato=None, hisobot=None):
    """Faylni tiklangan nusxada `oquvchi` bilan o'qishga URINADI.

    Tiklashning eng xavfsiz ishlatilishi: nusxa har holda tozalanadi va
    HAR QANDAY muvaffaqiyatsizlikda ASL xato qayta ko'tariladi — ya'ni
    ishtirokchiga ko'rinadigan izoh o'zgarmaydi va tiklash qatlami hech
    qachon YANGI rad sababi keltirib chiqarmaydi (5-xavfsizlik sharti).

    `oquvchi` — bitta yo'l qabul qiladigan chaqiriladigan obyekt
    (masalan `reader._read_xlsx_ichki`).
    `hisobot` — `tikla()` dagi kabi; tiklashning ISHONCHLILIGI yuqoriga
    shu orqali uzatiladi (`ss_orinbosar`).
    """
    try:
        nusxa = tikla(filepath, hisobot=hisobot)
    except Exception:
        if asl_xato is not None:
            raise asl_xato
        raise
    try:
        return oquvchi(nusxa)
    except Exception:
        # Tiklangan fayl ham o'qilmadi (masalan xavfsizlik chegarasidan
        # oshdi) — bu ham asl xatoning davomi, yangi xato tug'dirmaymiz.
        if asl_xato is not None:
            raise asl_xato
        raise
    finally:
        tozala(nusxa)
