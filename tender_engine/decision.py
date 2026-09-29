# -*- coding: utf-8 -*-
"""Yakuniy hukm (P2) — eski validate_one USTIGA REVIEW tasnifi.

Eski dvigatel 3 holat biladi (0/1/2). Yangi qatlam 5 ichki holatga
ajratadi (QAYTA_QURISH Q-C); YANGI qobiliyat — REVIEW_AMBIGUOUS:

    Ishtirokchi hujjatni QAYTA TUZGAN (etalon varag'i «o'chirilgan» deb
    ko'rinadi), LEKIN faylda jiddiy hajmda YANGI narxsimon sonlar bor —
    ya'ni narxlar kiritilgan-u, tuzilma boshqa. Eski kod buni rad etadi;
    ustuvorlik qoidasi bo'yicha bu xavfli (582/588/38238 misollari).

REVIEW→1 buyurtmachi qarori (2026-08-11): ikkilanishda qabul tomonga.
ESLATMA: yozilgan 1 o'sha fayl uchun QAYTMAS (send muzlatadi) — shuning
uchun har REVIEW evidence'da `sinf='review'` bilan iz qoldiradi.

BO'SH-HUJJAT INVARIANTI (REVIEW dan USTUN):
  - Q1 aynan nusxa / tegilmagan jadval — yangi son YO'Q, sanoq chegaraga
    yetmaydi, REVIEW berilmaydi;
  - Q3 siyrak (PRICE_SPARSE / PRICE_ONLY_TOTAL) — kodlar ochiq taqiqlangan;
  - faqat raqamlash to'ldirilgan hujjat — «narxsimon» filtri (butun son
    < 1000 sanalmaydi) uni chegaraga yetkazmaydi.

Soya rejimida bu hukm FAQAT evidence'ga yoziladi — bazadagi verdiktga
eski kod javob beradi (almashtirish Q-B darvozalaridan keyin).
"""

import math
import re

from tender_engine.validate import (   # noqa: F401
    validate_one,
    STATUS_FILLED,
    STATUS_DEFECT,
    STATUS_TECHNICAL,
)

from tender_engine import evidence as _ev
from tender_engine.normalize import is_numeric_nonzero, normk  # noqa: F401

# Dalil uchun o'lchov (qarorga ta'sir qilmaydi): faylda etalonda YO'Q nechta
# narxsimon son bor. Qarorni `NARX_NISBATI` + `NARX_MIN_BAND` chiqaradi.
REVIEW_YANGI_RAQAM = 30

# ── «Narx bor — qabul» siyosati (buyurtmachi qarori 2026-08-12) ────────────
# Buyurtmachi shablonni noto'g'ri slotga yuklasa, ishtirokchi AYBDOR EMAS.
# Shuning uchun tuzilma farqi o'z-o'zicha rad sababi emas. LEKIN Q3 («jiddiy
# to'ldirish») SAQLANADI — buyurtmachi shuni alohida tanladi.
#
# Farqi shundaki, Q3 endi ISHTIROKCHINING O'Z hujjatida o'lchanadi: eski
# dvigatel maxrajni ETALONDAN olgani uchun («D3. Maxraj muammosi») noto'g'ri
# shablon bilan solishtirilganda 57% to'ldirilgan hujjatga «1% to'ldirilgan»
# deb baho berardi. Korpusda o'lchandi: 49 nomzoddan 45 tasi ≥44%, 4 tasi
# ≤12.5% — orada keng bo'shliq bor, shuning uchun 30% chegara qat'iy emas.
# Chegaralarni buyurtmachi belgiladi (2026-08-12): «narx bor bo'lsa va u
# shunchaki 3-4 ta qiymat bo'lmasligi kerak, kamida 15% to'ldirilgan bo'lsagina
# qabul qilinadi».
NARX_NISBATI = 0.15          # bandlarning kamida shuncha qismi narxlangan bo'lsin
NARX_MIN_BAND = 5            # 3-4 ta qiymat YETARLI EMAS (hujjatni qisqartirib aldab bo'lmasin)

# #14-h (2026-09-10, buyurtmachi: «narx bo'lsa qabul qilishi kerak») —
# #14-son ning `hukm` yo'lidagi ko'rinishi.
#
# MUAMMO. `SHAKL_ERKIN_KOP_SON` faqat shakl-erkin yo'lda (shablon UMUMAN
# tanilmaganda) ishlardi. Shablon tanilib, lekin tuzilma mos kelmasa
# (HEADER_NOT_FOUND, SHEET_DELETED va h.k.) hujjat `hukm` yo'lidan o'tardi
# va u yerda bu zaxira YO'Q edi. Natijada bir xil dalilga ikki xil javob
# chiqardi (5k sinovi: 27003 — 1 218 yangi narx, QABUL; 15788 — 739 yangi
# narx, RAD; ikkalasi ham HEADER_NOT_FOUND).
#
# CHEGARA QAYERDAN. A+B korpusida o'lchandi (`xom/q14h_olchov`): B dagi
# TASDIQLANGAN TO'G'RI radlarda eng ko'p yangi narxsimon son — 265
# (7581), keyingilari 257, 249, 203. Regressiya 0 bo'lgan eng past chegara
# 275; 300 esa AYNI foydani (A da 15) beradi va 265 dan yetarli uzoq.
# Past tushirish xavfli: 200 da B da 4 ta to'g'ri rad ochilib ketadi.
#
# SHEET_UNFILLED ISTISNOSI — shartning O'ZAGI. «Jadval buyurtmachi
# shablonidagi holatida qoldirilgan» degani hujjat TASHLAB KETILGAN, undagi
# sonlar soni ahamiyatsiz: 19239 da bitta varaq to'ldirilib 8 tasi tegilmagan
# (680 yangi son) — eski rad TO'G'RI va #12-V1 da ham aynan shu fayl shu
# sababdan qaytarilgan edi. Istisnosiz bu qoida uni ochib yuborardi.
HUKM_KOP_SON = 300

# SIYOSAT VERSIYASI — chegara/qoida o'zgarganda QO'LDA ko'tariladi va har
# evidence yozuviga (varaq_dalil.siyosat) tushadi: keyin istalgan verdiktda
# «qaysi siyosat ishlagan» degan savolga aniq javob bo'ladi. Chegaralar
# tests/test_audit_iz.py da barmoq-izi bilan qotirilgan: qiymat o'zgarsa-yu
# versiya o'zgarmasa — test yiqiladi (evidence'da siyosatlar aralashib
# ketishining oldini oladi).
# s2026-09-08: chegaralar O'ZGARMAGAN (barmoq-izi bir xil), lekin qoidalar
# qo'shildi — #6 mazmun-nusxa, #7 NO_NEW_VALUES, #9/#9b kichik shakl, #10 varaq
# tanlovi, #11-b nol-guard, #12-V3 varaqli maxraj. Evidence'da qaysi qoidalar
# to'plami ishlagani ajralib tursin.
# s2026-09-10: #14-son — shakl-erkin yo'lida `SHAKL_ERKIN_KOP_SON` (yangi
# chegara, barmoq-iziga kiritildi).
# s2026-09-10b: #14-h — o'sha qoida `hukm` yo'lida ham (`HUKM_KOP_SON`=300,
# SHEET_UNFILLED istisnosi bilan). Bir xil dalilga ikki yo'lda ikki xil
# javob chiqishi tugatildi.
# s2026-09-11: P1 — PRICE_SPARSE va shakl-erkin yo'llarida ham ishtirokchining
# O'Z hujjatidagi Q3 (`jiddiy_toldirilganmi`) so'raladi (chegaralar o'zgarmadi).
# s2026-09-15: NOL = QIYMAT EMAS — `bosh_ustun_holati` va
# `narx_ustuni_toldirilganmi` 0 ni sanamas edi («9 nol + 1 narx» → RAD).
# s2026-09-17: BEKOR — buyurtmachi qarori (09-17/18, O'RTACHA YO'L = 09-14 holati):
# `bosh_ustun_holati` va `narx_ustuni_toldirilganmi` da 0 to'ldirilgan katak
# («9 nol + 1 narx» → qabul), LEKIN `_nolmas_yangi_son` nolni sanamaydi —
# hujjatda bironta ham nolmas yangi son bo'lmasa (#11-b, `bosh_hujjat_qabulmi`)
# RAD. «Hamma katak 0» varianti o'lchandi va RAD ETILDI: A da 27 tasdiqlangan
# noo'rin qabul (faqat nollar) qayta ochilardi. Chegaralar o'zgarmadi.
SIYOSAT_VERSIYA = "s2026-09-17"

# Faqat shu kodlar bilan rad etilgan fayl REVIEW nomzodi bo'la oladi.
# FILE_UNREADABLE kirmaydi — o'qib bo'lmagan faylga «narxlari bor» deyish
# uchun dalil yo'q.
_REVIEW_STRUKTURA = {
    "SHEET_DELETED", "COLUMN_COUNT", "SHEET_UNFILLED", "HEADER_NOT_FOUND",
    # Narx ustuni «bo'sh» topilgan holatlar ham kiradi: noto'g'ri shablon
    # bilan solishtirilganda BOSHQA ustunga qaralgan bo'lishi mumkin.
    # Haqiqiy to'ldirilganlik pastda ishtirokchining o'z hujjatida o'lchanadi.
    "PRICE_EMPTY", "ADDED_COLUMN_EMPTY", "NO_PRICE_FILLED", "COLUMN_RENAMED",
    "PRICE_ONLY_TOTAL",
}

# Bo'sh-hujjat belgisi — bunda REVIEW berilmaydi (Q3 himoyasi buzilmasin).
# PRICE_ONLY_TOTAL yuqoridagi ro'yxatda: u ham etalon maxrajidan kelib chiqqan
# bo'lishi mumkin, lekin nisbat testi uni baribir ushlaydi.
_BOSH_HUJJAT = {"PRICE_SPARSE"}

_ALPHA = re.compile(r"[^\W\d_]", re.UNICODE)

# DIQQAT: kalit so'zlar `normk` DAN O'TKAZIB saqlanadi — ular `normk(katak)`
# natijasi bilan solishtiriladi, ya'ni ikkalasi ham bir xil «yig'ilgan»
# fazoda bo'lishi shart. Xom holda yozilsa mos kelmaydi:
#     normk("ИТОГО") = "иtoro"   (г→r), ro'yxatdagi xom "итого" ga teng emas
#     normk("Жами")  = "жamи",          xom "жами" ga teng emas
# Shu sabab ilgari ruscha «ИТОГО» va o'zbekcha «Жами» jami qatorlari
# BAND deb sanalar, ulardagi summalar esa narx deb hisoblanar edi —
# «faqat jami qatoriga narx yozish» (Q3) himoyasi teshilardi.
_JAMI = tuple(sorted({normk(x) for x in (
    "jami", "Жами", "жами", "итого", "итог", "Итог", "всего", "Всего",
    "jami:", "итого:", "umumiy", "умумий",
    # Soliq qatorlari ham MUSTAQIL qiymat emas — ular jamining foizi.
    # 2026-08-24 auditi: 4 ta hujjat faqat «ИТОГО + НДС 12% + ИТОГО с НДС»
    # bilan to'ldirilgan ko'ringan, aslida bitta mustaqil narx ham yo'q edi.
    "ндс", "НДС", "ққс", "ҚҚС", "qqs", "QQS", "soliq", "солиқ",
)} - {""}))


def _float(v):
    """Son yoki son-matn -> float; aks holda None. bool son emas."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace("\xa0", "").replace(" ", "").replace(",", "."))
    except (ValueError, TypeError):
        return None


# Narx chegaralari. HAQIQIY FAYLDA TEKSHIRILGAN (id=262, 2026-08-12):
# smetada narx ustunlari BO'SH bo'lsa ham qatorlarda hajm (0.105, 0.6, 3.3)
# va koeffitsiyent (0.03, 0.17, 0.02) sonlari turadi. «Kasrli bo'lsa narx»
# degan sodda qoida ularni narx deb sanab, BO'SH hujjatni qabul qilardi.
NARX_MIN_QIYMAT = 1000       # bundan katta har qanday son narx bo'lishi mumkin
NARX_MIN_KASR = 100          # kasrli son ham bundan kichik bo'lsa — hajm/koeffitsiyent


def _narxsimon(f):
    """Narxga o'xshagan son.

    Chetlatiladi: raqamlash (1, 2, 3…), kichik butun miqdorlar (60 dona),
    hajm va koeffitsiyentlar (0.105, 3.3, 0.17). Narxlar so'mda o'lchanadi —
    ming so'mda berilganda ham band narxi 100 dan katta bo'ladi.
    """
    a = abs(f)
    if a >= NARX_MIN_QIYMAT:
        return True
    return (not f.is_integer()) and a >= NARX_MIN_KASR


def yangi_narxsimon_soni(et_varaqlar, pt_varaqlar):
    """Ishtirokchi faylidagi etalonda YO'Q narxsimon sonlar soni.

    Etalonda ham bor qiymatlar sanalmaydi (Q1 himoyasi: aynan nusxa -> 0).
    """
    etalon = set()
    for rows in (et_varaqlar or {}).values():
        for qator in rows:
            for katak in qator:
                f = _float(katak)
                if f is not None:
                    etalon.add(round(f, 4))
    n = 0
    for rows in (pt_varaqlar or {}).values():
        for qator in rows:
            for katak in qator:
                f = _float(katak)
                if f is not None and _narxsimon(f) and round(f, 4) not in etalon:
                    n += 1
    return n


def _matnli(v):
    """Tavsifga o'xshagan katak: kamida 2 ta harf."""
    return len(_ALPHA.findall(str(v or ""))) >= 2


def _jami_qatormi(qator):
    for c in qator:
        t = normk(c)
        if t and any(t.startswith(j) or t == j for j in _JAMI):
            return True
    return False


# ── ASOSIY MEZON: buyurtmachi shabloni RAQAMLAR bilan to'ldirilganmi ──────
#
# Buyurtmachi ko'rsatmasi (2026-08-20): «ishtirokchi buyurtmachi yuklagan
# excel faylini SONLAR bilan to'ldirdimi — to'ldirish faqat narxlar bilan
# bo'lmaydi, raqamlar istalgan narsa bo'lishi mumkin».
#
# Sabab amalda ko'rindi: buyurtmachi excel3 slotiga MUDDAT jadvalini
# yuklagan («№ | Ishlar nomi | Kun»), ishtirokchi uni to'liq to'ldirgan
# (15, 60, 45 kun) — narx filtri kichik butun sonlarni chetlab, 59 ta
# to'g'ri hujjatni rad etgan.
#
# Shuning uchun mezon: buyurtmachi BO'SH QOLDIRGAN kataklarga ishtirokchi
# son kiritdimi. Nima so'ralgani (kun, miqdor, narx) — buyurtmachi shabloni
# hal qiladi, biz talqin qilmaymiz.
#
# Saqlanadigan himoyalar:
#   • aynan nusxa (Q1) — yangi son yo'q, rad;
#   • siyrak to'ldirish (Q3) — bo'sh kataklarning <15% i, rad;
#   • tartib raqamlari (1, 2, 3…) to'ldirish deb sanalmaydi.
BOSH_USTUN_MIN = 3          # kamida shuncha katak to'ldirilgan bo'lsin


def _band_kalitlari(varaqlar):
    """(varaq, qator_indeksi) → qator. Tavsifi bor, JAMI bo'lmagan qatorlar."""
    natija = {}
    for nom, qatorlar in (varaqlar or {}).items():
        for i, qator in enumerate(qatorlar):
            if not any(_matnli(c) for c in qator):
                continue
            if _jami_qatormi(qator):
                continue
            natija[(nom, i)] = qator
    return natija


def _bosh(katak):
    return katak is None or not str(katak).strip()


def bosh_ustun_holati(et_varaqlar, pt_varaqlar):
    """Etalon bo'sh qoldirgan ustunlar va ishtirokchi ularni to'ldirgani.

    Qaytaradi: (bosh_ustunlar_soni, to'ldirilgan_katak, bo'sh_katak_slotlari)
    Solishtiruv POZITSIYA bo'yicha — ishtirokchi buyurtmachi shaklini
    ishlatgan holat uchun. Shakl qayta tuzilgan bo'lsa juftlik topilmaydi va
    natija (0, 0, 0) bo'ladi — u holda narx mezoni ishlaydi.
    """
    et_band = _band_kalitlari(et_varaqlar)
    if not et_band:
        return 0, 0, 0
    kenglik = max(len(q) for q in et_band.values())
    bosh_ustunlar = [j for j in range(kenglik)
                     if sum(1 for q in et_band.values()
                            if j >= len(q) or _bosh(q[j])) >= 0.8 * len(et_band)]
    if not bosh_ustunlar:
        return 0, 0, 0

    pt_band = _band_kalitlari(pt_varaqlar)

    # Tartib raqami ustunini chetlaymiz: ishtirokchi qatorlarni raqamlab
    # chiqsa, bu «to'ldirish» emas. Belgisi — ustundagi qiymatlar 1, 2, 3…
    # ketma-ketligini tashkil qiladi.
    def _raqamlash(j):
        qiymatlar = []
        for kalit in et_band:
            pt_q = pt_band.get(kalit)
            if pt_q is not None and j < len(pt_q):
                f = _float(pt_q[j])
                if f is not None:
                    qiymatlar.append(f)
        if len(qiymatlar) < 3:
            return False
        return all(v == i for i, v in enumerate(qiymatlar, 1))

    # MUHIM (2026-09-02): `_raqamlash(j)` natijasi faqat `j` ga bog'liq —
    # BIR MARTA hisoblanadi. Ilgari u qator siklining ichida chaqirilib,
    # o'zi ham hamma qatorni aylanardi: 20 000 qatorli smetada bu
    # O(qator² × ustun) — BITTA fayl 60+ daqiqa «qotib» turardi, lease
    # (15 daq) tugab, tayyor natija ham tashlab yuborilardi.
    raqamlash_ustunlari = {j for j in bosh_ustunlar if _raqamlash(j)}

    slotlar = toldirilgan = 0
    for kalit, et_q in et_band.items():
        pt_q = pt_band.get(kalit)
        for j in bosh_ustunlar:
            if j in raqamlash_ustunlari:
                continue
            if not (j >= len(et_q) or _bosh(et_q[j])):
                continue
            slotlar += 1
            if pt_q is not None and j < len(pt_q) and not _bosh(pt_q[j]):
                # NOL = KIRITILGAN (2026-09-17, buyurtmachi qarori — 09-15 dagi
                # «0 = qiymat emas» bekor qilindi). Ishtirokchi 0 yozgan bo'lsa
                # ham katakni to'ldirgan: son bo'lsa yetarli, qiymati muhim emas.
                f = _float(pt_q[j])
                if f is not None:
                    toldirilgan += 1
    return len(bosh_ustunlar), toldirilgan, slotlar


def bosh_ustun_toldirilganmi(et_varaqlar, pt_varaqlar):
    """Buyurtmachi bo'sh qoldirgan kataklarga ishtirokchi son kiritganmi.

    Ustun NOMI va qiymat TURI muhim emas (kun, miqdor, narx — farqi yo'q).
    Muhimi: bo'sh kataklarning yetarli qismi to'ldirilgan bo'lsin.

    Qaytaradi: (ha_yoq, to'ldirilgan, slotlar)
    """
    _bosh_n, toldirilgan, slotlar = bosh_ustun_holati(et_varaqlar, pt_varaqlar)
    if slotlar == 0:
        return False, toldirilgan, slotlar
    yetarli = (toldirilgan >= min(BOSH_USTUN_MIN, slotlar)
               and toldirilgan >= NARX_NISBATI * slotlar)
    return yetarli, toldirilgan, slotlar


# ── KOD ustunlari narx emas ───────────────────────────────────────────────
#
# 2026-08-20 auditida 2 ta fayl noo'rin QABUL qilingani aniqlandi: smetada
# «Шифр номера нормативов» ustunidagi norma kodlari (45059, 36052, 30139…)
# 1000 dan katta butun son bo'lgani uchun narx deb sanalgan, ayni paytda
# «Стоимость» ustunlari BUTUNLAY bo'sh edi.
#
# Kod ustunlari hech qachon narx bo'lmaydi — sarlavhasi bo'yicha chetlanadi.
_KOD_KALITLARI = tuple(normk(s) for s in (
    "шифр", "шифр номера", "обоснование", "код", "kod",
    "n п.п.", "n п.п", "n п/п", "№ п/п", "№п/п", "т/р", "t/r",
    "поз.", "позиция", "нормат",
))


def _kod_ustunlari(qatorlar, chegara=25):
    """Sarlavhasi KOD ga o'xshagan ustun indekslari (narx bo'la olmaydi).

    IKKI shart birga bo'lishi kerak — sarlavha VA qiymatlar ko'rinishi:
      1. sarlavhada «Шифр», «Обоснование», «№ п/п» kabi so'z;
      2. ustundagi sonlarning deyarli hammasi BUTUN (kodlarda kasr bo'lmaydi).
    Ikkinchi shart himoya uchun: sarlavhasi birlashtirilgan katakdan
    noto'g'ri o'qilsa ham, kasrli qiymatli haqiqiy narx ustuni chetlanmaydi.
    """
    nomzod = set()
    for qator in qatorlar[:chegara]:
        for j, katak in enumerate(qator):
            t = normk(katak)
            if not t or len(t) > 40:
                continue
            if any(t == k or t.startswith(k) for k in _KOD_KALITLARI):
                nomzod.add(j)

    natija = set()
    for j in nomzod:
        sonlar = []
        for qator in qatorlar:
            if j < len(qator):
                f = _float(qator[j])
                if f is not None:
                    sonlar.append(f)
        if not sonlar:
            natija.add(j)                       # son yo'q — zarari ham yo'q
        elif sum(1 for s in sonlar if float(s).is_integer()) >= 0.9 * len(sonlar):
            natija.add(j)                       # butun sonli — kod ustuni
    return natija


# ── ETALONNING NARX USTUNI to'ldirilganmi (eng ishonchli belgi) ──────────
#
# 2026-08-24 auditi (3 338 fayl, 47 shubhali, 43 tasi tasdiqlandi): 31 ta
# NOO'RIN RAD topildi. Beshta har xil nuqson bo'lsa-da, hammasida bir xil
# manzara: buyurtmachi shablonining «Стоимость / Сумма / НАРХ» ustuni
# ishtirokchi tomonidan TO'LDIRILGAN, lekin dvigatel boshqa joyga qarab
# «narx kiritilmagan» degan. Sabablari:
#   • Q3 nisbati buyurtmachining umumiy resurs katalogiga nisbatan (R1)
#   • narx ustuni YO'Q varaqlar «to'ldirilmagan» deb sanalgan (R2)
#   • rol noto'g'ri aniqlanib, «qo'shilgan ustun» qidirilgan (R3)
#   • ishtirokchi QO'SHGAN varaq tashlab ketilgan (R4)
#   • sarlavha qatoridagi begona son ustun moslashuvini siljitgan (R5)
#
# Shuning uchun mustaqil, sodda va ustunga bog'liq o'lchov qo'shiladi:
# etalonda NARX nomli va BO'SH ustun bor bo'lsa, ishtirokchi o'sha ustun
# indeksiga son kiritganmi. Qator moslashuvi TALAB QILINMAYDI — shu tufayli
# qatorlar surilgan yoki varaq qayta nomlangan hollarda ham ishlaydi.
_NARX_USTUN_KALIT = tuple(normk(s) for s in (
    "стоимость", "сумма", "цена", "нарх", "narx", "қиймат", "qiymat",
    "сметная стоимость", "на единицу измерения", "birlik uchun narx",
    "жами қиймати", "qiymati", "summa",
))


def _narx_ustun_indekslari(qatorlar, chegara=30):
    """Sarlavhasi NARXga o'xshagan ustunlar (kod/miqdor ustunlari chiqarilgan)."""
    kod = _kod_ustunlari(qatorlar, chegara)
    natija = set()
    for qator in qatorlar[:chegara]:
        for j, katak in enumerate(qator):
            if j in kod:
                continue
            t = normk(katak)
            if t and len(t) <= 70 and any(k in t for k in _NARX_USTUN_KALIT):
                natija.add(j)
    return natija


def narx_ustuni_toldirilganmi(et_varaqlar, pt_varaqlar):
    """Ishtirokchi O'Z hujjatining NARX ustuniga qiymat kiritganmi.

    Ikkala tomonda ham narx ustuni O'Z SARLAVHASI bo'yicha topiladi —
    etalonning ustun indeksi ishtirokchi faylida ishlatilmaydi. Shu tufayli
    qatorlar surilgan, varaq qayta nomlangan yoki ustun qo'shilgan hollarda
    ham to'g'ri ustun o'lchanadi (2026-08-24 auditidagi R1-R5 nuqsonlari).

    Shart:
      • etalonda narx ustuni BO'SH bo'lishi kerak — aks holda to'ldirish
        talab qilinmaydi (etalonning o'zi to'la bo'lsa, solishtirish ma'nosiz);
      • maxraj — ISHTIROKCHINING o'z band qatorlari (jami/soliq qatorlari
        sanalmaydi), ya'ni siyrak to'ldirish baribir tutiladi.

    Qaytaradi: (ha_yoq, to'ldirilgan_katak, band_qatorlari)
    """
    et_varaqlar = et_varaqlar or {}
    pt_varaqlar = pt_varaqlar or {}

    # 1) etalonda umuman BO'SH narx ustuni bormi?
    etalon_bosh_narx = False
    et_sonlar = set()
    for rows in et_varaqlar.values():
        for q in rows:
            for c in q:
                f = _float(c)
                if f is not None:
                    et_sonlar.add(round(f, 4))
    for rows in et_varaqlar.values():
        for j in _narx_ustun_indekslari(rows):
            bosh = son = 0
            for q in rows:
                if not any(_matnli(c) for c in q) or _jami_qatormi(q):
                    continue
                if j >= len(q) or _bosh(q[j]):
                    bosh += 1
                else:
                    son += 1
            if bosh >= 3 and bosh >= son:
                etalon_bosh_narx = True
                break
        if etalon_bosh_narx:
            break
    if not etalon_bosh_narx:
        return False, 0, 0

    # 2) ishtirokchining O'Z narx ustunlarida nechta YANGI qiymat bor
    #
    # Nisbat HAR VARAQ uchun alohida hisoblanadi. Sabab (2026-08-24 auditi,
    # R2 nuqsoni): ko'p varaqli smetada narx ustuni faqat bitta varaqda
    # bo'ladi, qolganlari (СВОД, ЛРВ, дефектный акт) narxlanmaydi. Umumiy
    # nisbat olinsa, to'ldirilgan varaq narxlanmaydigan varaqlar hisobiga
    # «suyultirilib», hujjat noo'rin rad etilardi (16669: haqiqiy narx
    # to'ldirilishi 55%, umumiy nisbat esa 2.6%).
    eng_band = eng_toldi = 0
    for rows in pt_varaqlar.values():
        ustunlar = _narx_ustun_indekslari(rows)
        if not ustunlar:
            continue
        band = toldirilgan = 0
        for q in rows:
            if not any(_matnli(c) for c in q) or _jami_qatormi(q):
                continue
            band += 1
            for j in ustunlar:
                if j < len(q):
                    f = _float(q[j])
                    # NOL = KIRITILGAN (2026-09-17, buyurtmachi qarori — 09-15 dagi
                    # «0 = qiymat emas» bekor qilindi): 0 ham to'ldirilgan katak.
                    # Etalonda bor sonlar baribir sanalmaydi (Q1 himoyasi).
                    if f is not None and round(f, 4) not in et_sonlar:
                        toldirilgan += 1
                        break
        if band == 0:
            continue
        yetarli = (toldirilgan >= min(BOSH_USTUN_MIN, band)
                   and toldirilgan >= NARX_NISBATI * band)
        if yetarli:
            return True, toldirilgan, band
        if toldirilgan > eng_toldi:
            eng_toldi, eng_band = toldirilgan, band
    return False, eng_toldi, eng_band


def narx_nisbati(et_varaqlar, pt_varaqlar):
    """ISHTIROKCHINING O'Z hujjatida: nechta band bor, nechtasi narxlangan.

    Maxraj ETALONDAN emas, ishtirokchi faylidan olinadi — buyurtmachi
    noto'g'ri shablon yuklaganda etalon maxraji ma'nosiz bo'ladi.
    Aldashdan himoya: etalonda ALLAQACHON bor sonlar narx deb sanalmaydi
    (aynan nusxa → 0), raqamlash sonlari `_narxsimon` filtridan o'tmaydi.

    Qaytaradi: (band_soni, narxlangan_band_soni)
    """
    etalon = set()
    for rows in (et_varaqlar or {}).values():
        for qator in rows:
            for katak in qator:
                f = _float(katak)
                if f is not None:
                    etalon.add(round(f, 4))

    band = narxli = 0
    for rows in (pt_varaqlar or {}).values():
        kod = _kod_ustunlari(rows)            # «Шифр…», «№ п/п» — narx emas
        for qator in rows:
            if not any(_matnli(c) for c in qator):
                continue                      # tavsifsiz qator — band emas
            if _jami_qatormi(qator):
                continue                      # JAMI/ИТОГО qatori sanalmaydi
            band += 1
            for j, katak in enumerate(qator):
                if j in kod:
                    continue
                f = _float(katak)
                if (f is not None and _narxsimon(f)
                        and round(f, 4) not in etalon):
                    narxli += 1
                    break
    return band, narxli


def narx_nisbati_varaqli(et_varaqlar, pt_varaqlar):
    """`narx_nisbati` — lekin HAR VARAQ uchun alohida: [(varaq, band, narxli)].

    Hisob `narx_nisbati` bilan aynan bir xil (etalon sonlari to'plami umumiy,
    kod ustunlari varaqqa xos); farqi faqat varaq bo'yicha guruhlash.
    """
    etalon = set()
    for rows in (et_varaqlar or {}).values():
        for qator in rows:
            for katak in qator:
                f = _float(katak)
                if f is not None:
                    etalon.add(round(f, 4))

    natija = []
    for nom, rows in (pt_varaqlar or {}).items():
        kod = _kod_ustunlari(rows)
        band = narxli = 0
        for qator in rows:
            if not any(_matnli(c) for c in qator):
                continue
            if _jami_qatormi(qator):
                continue
            band += 1
            for j, katak in enumerate(qator):
                if j in kod:
                    continue
                f = _float(katak)
                if (f is not None and _narxsimon(f)
                        and round(f, 4) not in etalon):
                    narxli += 1
                    break
        natija.append((nom, band, narxli))
    return natija


def jiddiy_varaqli(et_varaqlar, pt_varaqlar):
    """#12-V3 (2026-09-08): Q3 ni ENG YAXSHI VARAQ bo'yicha o'lchash — FAQAT
    tegilgan varaqlar ko'pchilik bo'lsa.

    Nega: buyurtmachi shabloni ko'p varaqli katalog bo'lganda (35 varaqli
    smeta, 13 varaqli ресурс) ishtirokchi o'ziga tegishli varaqlarni
    to'ldiradi, global maxraj esa hammasini qo'shib «7% to'ldirilgan» deydi
    (korpus 34380: 743 band narxlangan, maxraj 10 179).

    Himoya: BITTA varaqni to'ldirib qolganini tashlab ketgan hujjat o'tmasin
    (19239: 9 varaqdan 1; 91143: 6 dan 1) — TEGILGAN (kamida bitta yangi
    narxsimon soni bor) bandli varaqlar soni tegilmaganlardan KAM bo'lsa
    varaqli o'lchov ishlamaydi. Muqova/jamlanma varag'i doim «tegilmagan»
    bo'lgani uchun «hech bir varaq tegilmagan bo'lmasin» (V2) foyda bermadi;
    shartsiz variant (V1) B da 2 regressiya berdi. V3 korpusda: B zonasi
    0/1 819, foyda 6/156, yomonlashuv 0 (`korpus/regressiya/xom/q12v3_olchov.log`).

    Qaytaradi: (ok, band, narxli, varaq_nomi, (tegilgan, bandli)).
    """
    v = narx_nisbati_varaqli(et_varaqlar, pt_varaqlar)
    bandli = [x for x in v if x[1] > 0]
    tegilgan = [x for x in bandli if x[2] > 0]
    tegilmagan = len(bandli) - len(tegilgan)
    hisob = (len(tegilgan), len(bandli))
    if not tegilgan or len(tegilgan) < tegilmagan:
        return False, 0, 0, None, hisob
    nom, band, narxli = max(tegilgan, key=lambda x: x[2] / x[1])
    talab = max(NARX_MIN_BAND, math.ceil(NARX_NISBATI * band))
    if (band < NARX_MIN_BAND and narxli * 2 >= band
            and yangi_narxsimon_soni(et_varaqlar, pt_varaqlar) >= NARX_MIN_BAND):
        talab = max(1, math.ceil(NARX_NISBATI * band))     # #9 bilan bir xil
    return narxli >= talab, band, narxli, nom, hisob


def jiddiy_toldirilganmi(et_varaqlar, pt_varaqlar):
    """Q3 ni ishtirokchining o'z hujjatida qo'llash.

    Shart: narxlangan bandlar ≥ NARX_MIN_BAND VA nisbat ≥ NARX_NISBATI.
    Global maxraj yiqilsa — #12-V3 `jiddiy_varaqli` (eng yaxshi varaq, faqat
    tegilgan varaqlar ko'pchilik bo'lsa); u holda qaytgan band/narxli o'sha
    VARAQNIKI. Zaxira faqat False → True yo'nalishida ishlaydi.
    """
    band, narxli = narx_nisbati(et_varaqlar, pt_varaqlar)
    if band == 0:
        return False, band, narxli
    # DIQQAT: `min(talab, band)` bilan yumshatilmaydi — aks holda 2 bandli
    # hujjat 2 ta qiymat bilan «100% to'ldirilgan» bo'lib o'tib ketardi.
    # Buyurtmachi: «shunchaki 3-4 ta qiymat bo'lmasligi kerak».
    talab = max(NARX_MIN_BAND, math.ceil(NARX_NISBATI * band))
    if band < NARX_MIN_BAND:
        # #9 (2026-09-07): 1-4 bandli shakl (jamlanma / объектная смета) —
        # NARX_MIN_BAND=5 ga yetish JISMONAN mumkin emas (id 49512: 2 bandli,
        # 2 mlrd so'mlik to'liq jadval rad etilgan). Chegara OLIB TASHLANMAYDI,
        # katak-soni sharti bilan yumshatiladi — FAQAT uch shart birga:
        #   bandlarning >=50% i narxlangan  VA  hujjatda kamida NARX_MIN_BAND ta
        #   YANGI narxsimon katak bor (2 bandli 2 qiymat baribir o'tmaydi —
        #   2026-08-04 da yopilgan teshik yopiq qoladi).
        # Korpusda o'lchangan: B rad zonasi 0/1 819, foyda 8/156.
        if (narxli * 2 >= band
                and yangi_narxsimon_soni(et_varaqlar, pt_varaqlar) >= NARX_MIN_BAND):
            talab = max(1, math.ceil(NARX_NISBATI * band))
    if narxli >= talab:
        return True, band, narxli
    # #12-V3 (2026-09-08): global maxraj yiqildi — eng yaxshi varaq bo'yicha,
    # faqat tegilgan varaqlar ko'pchilik bo'lsa. Rad bo'lsa GLOBAL sonlar
    # qaytadi (dalil/izoh ilgarigidek).
    v_ok, v_band, v_narxli, _nom, _hisob = jiddiy_varaqli(et_varaqlar, pt_varaqlar)
    if v_ok:
        return True, v_band, v_narxli
    return False, band, narxli


def _nolmas_yangi_son(et_varaqlar, pt_varaqlar):
    """Ishtirokchi faylidagi etalonda YO'Q, NOLDAN FARQLI sonlar soni.

    `yangi_narxsimon_soni` dan farqi: `_narxsimon` filtri yo'q — kichik
    miqdor/koeffitsiyent ham sanaladi. NOL sanalmaydi: bu o'lchov «hujjatda
    umuman biror haqiqiy qiymat bormi» savoliga javob beradi (#11-b qo'riqchisi
    va `bosh_hujjat_qabulmi`); 4 ustun × 11 qator faqat 0 (83571/108998) — bo'sh
    hujjat. 2026-09-18 buyurtmachi qarori (O'RTACHA YO'L): narx ustuni
    o'lchovlarida 0 to'ldirilgan katak («9 nol + 1 narx» → qabul), LEKIN
    hujjatda bironta ham nolmas yangi son bo'lmasa → RAD.
    """
    etalon = set()
    for rows in (et_varaqlar or {}).values():
        for qator in rows:
            for katak in qator:
                f = _float(katak)
                if f is not None:
                    etalon.add(round(f, 4))
    n = 0
    for rows in (pt_varaqlar or {}).values():
        for qator in rows:
            for katak in qator:
                f = _float(katak)
                if f is not None and f != 0 and round(f, 4) not in etalon:
                    n += 1
    return n


def bosh_hujjat_qabulmi(et_varaqlar, pt_varaqlar):
    """#7 (2026-09-07): ACCEPT yo'lidagi BO'SH hujjat — hech qanday yangi qiymat yo'q.

    `validate_one` 1 qaytarsa `hukm` chaqirilmaydi, ya'ni ACCEPT yo'lida
    Q1/Q3 himoyasi yo'q edi: etalon oldindan to'ldirilgan bo'lsa yoki
    ishtirokchi faqat matn/nol yozsa — qabul (auditda 185 tasdiqlangan).

    True FAQAT quyidagilar birga bajarilsa:
      yangi narxsimon son = 0  ∧  narxlangan band = 0  ∧  nolmas yangi son = 0
    va uchta ISTISNO ishlamasa (ular True bo'lsa — False, ya'ni qabul qoladi):
      • etalon o'zi narxlangan (prefilled, `narx_nisbati({}, et)`) — ishtirokchidan
        qo'shadigan narsa talab qilib bo'lmaydi (767 etalon / 2 296 fayl);
      • buyurtmachi bo'sh qoldirgan kataklar son bilan to'ldirilgan
        (`bosh_ustun_toldirilganmi`) — narx emas, kun/miqdor so'ralgan bo'lishi mumkin;
      • bironta nolmas yangi son bor — ustuvorlik qoidasi: ikkilanishda qabul.

    Butun korpusda o'lchangan (kod o'zgartirmasdan): B qabul zonasi 0/2 779,
    foyda NOORIN_QABUL 131/185. Bu HUKM emas, «hech narsa yo'q»lik o'lchovi.
    """
    if not et_varaqlar or not pt_varaqlar:
        return False
    if narx_nisbati({}, et_varaqlar)[1] >= NARX_MIN_BAND:
        return False                               # prefilled etalon
    if bosh_ustun_toldirilganmi(et_varaqlar, pt_varaqlar)[0]:
        return False                               # bo'sh kataklar to'ldirilgan
    if yangi_narxsimon_soni(et_varaqlar, pt_varaqlar):
        return False
    if narx_nisbati(et_varaqlar, pt_varaqlar)[1]:
        return False
    return _nolmas_yangi_son(et_varaqlar, pt_varaqlar) == 0


def hukm(rol, et_varaqlar, pt_varaqlar, fayl_nomi, eski_res=None):
    """5-statusli hukm. `eski_res` berilsa validate_one qayta chaqirilmaydi.

    Qaytaradi: {"ichki": ..., "tashqi": 0|1|2, "res": eski natija,
                "yangi_raqamlar": int|None, "review_sabab": str|None}
    """
    res = eski_res if eski_res is not None else validate_one(
        rol, et_varaqlar, pt_varaqlar, fayl_nomi)
    kodlar = [f["code"] for f in res.get("findings", [])
              if f.get("severity") not in ("note", "technical")]

    yangi = band = narxli = None
    review_sabab = None
    if res["status"] == STATUS_FILLED:
        ichki = _ev.ACCEPT_FILLED
    elif res["status"] == STATUS_DEFECT:
        ichki = _ev.eski_natijadan_ichki(res["status"], kodlar)

        # ── #11-b (2026-09-07): NOL = QIYMAT EMAS ─────────────────────────
        # Hujjatda bironta NOLMAS yangi son bo'lmasa (narxsimon ham, kichik
        # ham) — pastdagi REVIEW→1 flip'larining HECH BIRI ishlamaydi.
        # Sabab: `narx_ustuni_toldirilganmi` da `_narxsimon` filtri yo'q va
        # u nollarni «narx» deb sanab, 4 ustun × 11 qatorga faqat 0 yozilgan
        # hujjatni (83571/108998) qabul qilgan edi. To'liq `_narxsimon`
        # filtri RAD ETILDI (B da 8 regressiya — ming-so'mli shakllar),
        # «faqat nollar» varianti ham (90704: narx ustuni nol, lekin 12 272
        # nolmas yangi son bor). Bu shart faqat HAQIQATAN bo'sh hujjatga tegadi.
        # Korpusda o'lchangan: B qabul zonasi 0/2 779, foyda NOORIN_QABUL 25.
        # Prefilled etalon bu yerga kelmaydi (validate_one unga 1 beradi).
        if (_nolmas_yangi_son(et_varaqlar, pt_varaqlar) == 0
                and yangi_narxsimon_soni(et_varaqlar, pt_varaqlar) == 0
                and narx_nisbati(et_varaqlar, pt_varaqlar)[1] == 0):
            return {"ichki": ichki, "tashqi": _ev.TASHQI[ichki], "res": res,
                    "yangi_raqamlar": 0, "band": None, "narxli_band": 0,
                    "review_sabab": None}

        # ── PRICE_SPARSE ni BEKOR QILISH sharti (2026-08-24 auditi) ──────
        #
        # Q3 nisbati (PRICE_SPARSE) MAXRAJI noto'g'ri bo'lishi mumkin:
        #   • buyurtmachining umumiy resurs katalogi bo'yicha o'lchanadi
        #     («Коэффициент риска», «va h.k.» — ularni narxlab bo'lmaydi);
        #   • narxlanmaydigan varaqlar hisobga qo'shiladi.
        # Auditda shu sabab 31 ta hujjat noo'rin rad etilgani tasdiqlandi.
        #
        # Shuning uchun: ishtirokchining O'Z hujjatidagi narx ustuni
        # TO'G'RI maxraj bilan o'lchanganda yetarli to'ldirilgan bo'lsa,
        # PRICE_SPARSE hukmi bekor qilinadi. Q3 mohiyati saqlanadi —
        # `narx_ustuni_toldirilganmi` o'zi ≥3 katak VA ≥15% talab qiladi.
        # PRICE_ONLY_TOTAL va aynan nusxa bu yo'ldan O'TMAYDI.
        if "PRICE_SPARSE" in kodlar and "PRICE_ONLY_TOTAL" not in kodlar:
            narx_ok, n_toldi, n_band = narx_ustuni_toldirilganmi(
                et_varaqlar, pt_varaqlar)
            if narx_ok:
                foiz = (n_toldi / n_band * 100) if n_band else 0
                return {"ichki": _ev.REVIEW_AMBIGUOUS, "tashqi": 1, "res": res,
                        "yangi_raqamlar": None, "band": n_band,
                        "narxli_band": n_toldi,
                        "review_sabab": (
                            f"narx ustuni to'ldirilgan ({n_toldi}/{n_band}, "
                            f"{foiz:.0f}%) — Q3 nisbati noto'g'ri maxrajdan "
                            f"kelib chiqqan")}
            # P1 (2026-09-11, buyurtmachi qarori) — ISHTIROKCHINING O'Z
            # HUJJATIDAGI Q3 (`jiddiy_toldirilganmi`: >=5 band ∧ >=15%, #9
            # kichik shakl va #12-V3 varaqli maxraj bilan) shu yo'lda HAM
            # so'raladi. Ilgari (#9b) faqat band < 5 da so'ralardi; band >= 5
            # uchun yagona qutqaruvchi `narx_ustuni_toldirilganmi` edi — u
            # (a) etalon narx ustuni to'la bo'lsa (buyurtmachi o'z hisobini
            # qoldirgan: 183117 — 19 banddan 10 tasi to'la) darhol False,
            # (b) sarlavhasiz narx ustunida (186026 — 128 banddan 84 tasi
            # narxlangan) hech narsa topmaydi. `validate_one` ning PRICE_SPARSE
            # hisobi esa ETALON qatorlariga moslashtirilgan («117 banddan 2 tasi»)
            # — 2026-08-12 dagi D3 maxraj muammosining shu yo'ldagi qoldig'i.
            #
            # Tarix: #8 (2026-09-07) shu qoida edi va B da 12 «to'g'ri rad»
            # ochgani uchun rad etilgan; 2026-09-11 backtest'ida buyurtmachi o'sha
            # B hujjatlarini (Форма 4: 5/10, 6/15, 7/16 narxlangan) ko'rib
            # «to'ldirilgan» dedi — 19 B qatori odam ko'rigiga o'tkazildi
            # (`xom/p1_b_qayta_yorliqla.py`). O'lchov (`xom/q_p1_*`): A +5,
            # yo'qotish 0; 17 191 lik backtest'da 15 rad→qabul, teskarisi 0.
            # SHEET_UNFILLED bilan birga kelsa TEGILMAYDI (#14-h bilan bir xil:
            # butun jadval shablon holatida qoldirilgan bo'lsa hujjat tashlab
            # ketilgan — 19239 naqshi).
            if "SHEET_UNFILLED" not in set(kodlar):
                jiddiy_k, band_k, narxli_k = jiddiy_toldirilganmi(et_varaqlar, pt_varaqlar)
                if jiddiy_k:
                    global_band = narx_nisbati(et_varaqlar, pt_varaqlar)[0]
                    varaq_k = (jiddiy_varaqli(et_varaqlar, pt_varaqlar)[3]
                               if band_k != global_band else None)
                    foiz = (narxli_k / band_k * 100) if band_k else 0
                    return {"ichki": _ev.REVIEW_AMBIGUOUS, "tashqi": 1, "res": res,
                            "yangi_raqamlar": None, "band": band_k,
                            "narxli_band": narxli_k,
                            "review_sabab": (
                                f"ishtirokchi hujjatining {narxli_k}/{band_k} bandi "
                                f"({foiz:.0f}%) narxlangan"
                                + (f" («{varaq_k}» jadvali bo'yicha)" if varaq_k else "")
                                + " — PRICE_SPARSE etalon qatorlariga moslashtirilgan "
                                  "nisbatdan kelib chiqqan")}

        faqat_struktura = (kodlar
                           and not (set(kodlar) & _BOSH_HUJJAT)
                           and set(kodlar) <= _REVIEW_STRUKTURA)
        if faqat_struktura:
            # `yangi` faqat DALIL uchun yoziladi; qarorni buyurtmachi bergan
            # ikki shart chiqaradi (nisbat ≥15% VA narxlangan band ≥5).
            yangi = yangi_narxsimon_soni(et_varaqlar, pt_varaqlar)
            jiddiy, band, narxli = jiddiy_toldirilganmi(et_varaqlar, pt_varaqlar)
            # #12-V3 izi: band GLOBAL maxrajdan farq qilsa — varaqli o'lchov
            # ishlagan, izohda qaysi jadval ekani ko'rsatiladi (audit izi).
            varaq_nomi = None
            if jiddiy and band != narx_nisbati(et_varaqlar, pt_varaqlar)[0]:
                varaq_nomi = jiddiy_varaqli(et_varaqlar, pt_varaqlar)[3]
            if not jiddiy:
                # Buyurtmachi narx emas, boshqa qiymat so'ragan bo'lishi
                # mumkin (masalan «Kun») — etalonda bitta bo'sh ustun bo'lsa
                # va u to'ldirilgan bo'lsa, hujjat to'liq topshirilgan.
                jiddiy, narxli, band = bosh_ustun_toldirilganmi(
                    et_varaqlar, pt_varaqlar)
            if not jiddiy:
                # Eng ishonchli belgi: etalonning O'Z narx ustuni
                # to'ldirilganmi (2026-08-24 auditi — 31 noo'rin rad).
                jiddiy, narxli, band = narx_ustuni_toldirilganmi(
                    et_varaqlar, pt_varaqlar)
            # #14-h: band-nisbat o'lchovi smeta shakllarida NOTO'G'RI KAM
            # chiqadi (narx tavsif qatoridan pastda). Yuqoridagi uchala
            # o'lchov ham «jiddiy emas» desa-yu, etalonda YO'Q narxsimon
            # sonlar `HUKM_KOP_SON` dan ko'p bo'lsa — hujjat to'ldirilgan.
            # SHEET_UNFILLED bo'lsa TEGILMAYDI: jadval shablon holatida
            # qoldirilgan bo'lsa hujjat tashlab ketilgan (izohga qarang).
            kop_son = False
            if (not jiddiy and "SHEET_UNFILLED" not in set(kodlar)
                    and yangi >= HUKM_KOP_SON):
                jiddiy = kop_son = True
                band, narxli = narx_nisbati(et_varaqlar, pt_varaqlar)
            if jiddiy:
                ichki = _ev.REVIEW_AMBIGUOUS
                foiz = (narxli / band * 100) if band else 0
                if kop_son:
                    review_sabab = (
                        f"tuzilma etalonga mos emas "
                        f"({', '.join(sorted(set(kodlar)))}), lekin hujjatda "
                        f"{yangi} ta yangi narx qiymati bor "
                        f"(band nisbati {narxli}/{band} — smeta shaklida narx "
                        f"tavsif qatoridan pastda)")
                else:
                    review_sabab = (
                        f"tuzilma etalonga mos emas "
                        f"({', '.join(sorted(set(kodlar)))}), lekin ishtirokchi "
                        f"hujjatining {narxli}/{band} bandi ({foiz:.0f}%) narxlangan "
                        + (f"(«{varaq_nomi}» jadvali bo'yicha, tegilgan jadvallar "
                           f"ko'pchilik) " if varaq_nomi else "")
                        + "— buyurtmachi shabloni boshqa shaklda bo'lishi mumkin")
    else:
        ichki = _ev.TECHNICAL_ERROR

    return {"ichki": ichki, "tashqi": _ev.TASHQI[ichki], "res": res,
            "yangi_raqamlar": yangi, "band": band, "narxli_band": narxli,
            "review_sabab": review_sabab}


# ---------------------------------------------------------------------------
# SHAKL-ERKIN HUKM — buyurtmachi shabloni tanilmaganda ham verdikt beriladi
# ---------------------------------------------------------------------------
#
# Buyurtmachi ko'rsatmasi (2026-08-19): «tender uchun notanish ko'rinishdagi
# buyurtmachi fayli kelsa ham HAR DOIM qabul qilib, uni ishtirokchi fayllari
# bilan solishtirish kerak». Ya'ni shablonni tanimaslik verdiktsiz qoldirish
# uchun asos EMAS.
#
# Bu funksiya HECH QANDAY sarlavha/rol/ustun aniqlashga tayanmaydi — faqat
# xom katak qiymatlari bilan ishlaydi:
#   1. aynan nusxa (Q1)             -> rad
#   2. yangi narx yo'q               -> rad
#   3. narx bor, lekin siyrak (<15%) -> rad
#   4. qolganida                     -> qabul
#
# Shu tufayli u ISTALGAN shakldagi hujjatga javob bera oladi.

# Bandsiz (tavsif qatorisiz) hujjatlar uchun zaxira chegara: shuncha yangi
# narxsimon son bo'lsa to'ldirilgan hisoblanadi. Ustuvorlik qoidasi bo'yicha
# past olingan — ikkilanishda qabul tomonga.
SHAKL_ERKIN_MIN_SON = 5

# #14-son (2026-09-10, buyurtmachi ko'rsatmasi: «buyurtmachi faylida nima
# bo'lsa ham ishtirokchi fayliga solishtirilsin»). Shakl tanilmagan yo'lda
# band-nisbat o'lchovi smeta shakllarida NOTO'G'RI KAM chiqadi: narx tavsif
# qatoridan pastdagi qatorlarda turadi, shuning uchun 11 204 ta yangi nolmas
# soni bor to'liq smeta «183/4 618 band» ko'rinib PRICE_SPARSE olardi (20k
# sinovi: 3470, 4582, 6089, 5248, 6831 — 2026-08-20 da to'g'ri qabul bo'lgan
# edi). Etalonda YO'Q narxsimon sonlar shuncha bo'lsa — hujjat to'ldirilgan,
# REVIEW izi bilan qabul. Chegara taqsimotdan: tasdiqlangan to'g'ri radlarda
# eng ko'pi 94 (drift qatori 351), 20k eski radlarda 393; noo'rin radlarda
# 576+. O'lchov (`korpus/regressiya/xom/q14b_olchov.log`): N=500 da B rad
# zonasi 0, A +5, 20k da +5 eski qabul qaytadi, bironta eski rad ochilmaydi.
# Faqat shakl-erkin yo'lga ta'sir qiladi; Q1 (nusxa) undan oldin tekshiriladi.
SHAKL_ERKIN_KOP_SON = 500

IZOH_QABUL = "Hujjat to'g'ri to'ldirilgan."
IZOH_NUSXA = ("Hujjat buyurtmachi shabloni bilan bir xil — ishtirokchi "
              "tomonidan narx kiritilmagan.")
IZOH_NARX_YOQ = ("Hujjatda narx kiritilmagan — buyurtmachi shablonidagi "
                 "bandlarga qiymat qo'yilmagan.")

#: Matn qatlami tiklanmagan hujjat uchun TEXNIK kod (rad emas!) — pastdagi
#: `matn_ishonchsiz` izohiga qarang.
KOD_MATN_TIKLANMADI = "TEXT_UNRECOVERED"
IZOH_MATN_TIKLANMADI = (
    "Hujjatning matn qismini ochib bo'lmadi (fayl yo'lda buzilgan) va "
    "ichida narx qiymatlari ham topilmadi. Faylni qayta yuklang.")


def _bir_xilmi(et_varaqlar, pt_varaqlar):
    """Ma'lumot sohasi AYNAN bir xilmi (Q1 — nusxani qaytarish)."""
    if not et_varaqlar or not pt_varaqlar:
        return False
    if len(et_varaqlar) != len(pt_varaqlar):
        return False

    def _iz(varaqlar):
        return [[[normk(c) for c in qator] for qator in qatorlar]
                for qatorlar in varaqlar.values()]

    return _iz(et_varaqlar) == _iz(pt_varaqlar)


def _hukm_matnsiz(et_varaqlar, pt_varaqlar):
    """MATN qatlami tiklanmagan hujjat: FAQAT sonlar bo'yicha hukm.

    NEGA ALOHIDA YO'L (2026-09-07 ko'rigi). Tiklash `sharedStrings.xml` ni
    bo'sh o'rinbosar bilan almashtirganda hujjatning butun MATNI yo'qoladi:
    sarlavhalar ham, band tavsiflari ham. Oddiy yo'l esa aynan shularga
    tayanadi — `_matnli()` bo'sh matnni tavsif deb sanamaydi, natijada
    192 310 bandli haqiqiy smeta «2 bandli» bo'lib qolib, «bandlarga qiymat
    qo'yilmagan» degan YOLG'ON rad chiqardi (nazorat tajribasi: sog'lom,
    to'g'ri to'ldirilgan hujjatdan lug'atni olib tashlash verdiktni 1 dan 2
    ga o'tkazadi, dvigatel hujjatda 29 398 ta yangi son ko'rib turganiga
    qaramay).

    SONLAR esa BUZILMAYDI — ular varaq XML ida `<v>` ichida yotadi, lug'atga
    bog'liq emas. Shuning uchun bu yerda faqat «etalonda bo'lmagan yangi
    narxsimon son» mezoni ishlaydi:

      • yetarlicha yangi son bor  -> QABUL (REVIEW izi bilan);
      • yo'q                      -> RAD EMAS, TEXNIK holat. Narx MATN
        sifatida kiritilgan bo'lishi ham mumkin («1 234 567» satr) — u
        lug'at bilan birga yo'qolgan, ya'ni rad uchun asosimiz yo'q.
        Ustuvorlik qoidasi: noo'rin rad noo'rin qabuldan yomonroq.
    """
    yangi = yangi_narxsimon_soni(et_varaqlar, pt_varaqlar)
    if yangi >= SHAKL_ERKIN_MIN_SON:
        return {"ichki": _ev.REVIEW_AMBIGUOUS, "tashqi": 1, "res": None,
                "yangi_raqamlar": yangi, "band": None, "narxli_band": None,
                "kodlar": [], "comment_uz": IZOH_QABUL,
                "review_sabab": (f"hujjatning matn qismi tiklanmadi; sonlar "
                                 f"bo'yicha {yangi} ta yangi narx qiymati bor"),
                "shakl_erkin": True, "matn_ishonchsiz": True}
    return {"ichki": _ev.TECHNICAL_ERROR, "tashqi": 0, "res": None,
            "yangi_raqamlar": yangi, "band": None, "narxli_band": None,
            "kodlar": [KOD_MATN_TIKLANMADI],
            "comment_uz": IZOH_MATN_TIKLANMADI,
            "review_sabab": None, "shakl_erkin": True, "matn_ishonchsiz": True}


def hukm_shakl_erkin(et_varaqlar, pt_varaqlar, bir_xil_fayl=False,
                     matn_ishonchsiz=False):
    """Shakl tanilmaganda ham verdikt. Qaytaradi `hukm()` bilan bir xil kalitlar.

    `bir_xil_fayl` — fayl xeshi etalon xeshi bilan bir xil (aniq nusxa).
    `et_varaqlar` bo'sh/None bo'lsa (etalon umuman yo'q) — solishtiruv
    asosisiz, faqat ishtirokchi hujjatidagi narxlar hisobga olinadi.

    `matn_ishonchsiz` — hujjat TIKLASH orqali o'qilgan va uning MATN lug'ati
    (`sharedStrings.xml`) faylda umuman yo'q edi (`reader.read_file` ning
    `hisobot["matn_tiklanmadi"]` belgisi). Bunday hujjatda sonlar to'liq,
    matn esa YO'Q — quyidagi barcha MATNGA tayangan o'lchovlar ma'nosiz
    bo'ladi va ular bo'yicha hukm chiqarish YOLG'ON bo'lardi.
    """
    et_varaqlar = et_varaqlar or {}

    if bir_xil_fayl or _bir_xilmi(et_varaqlar, pt_varaqlar):
        return {"ichki": _ev.REJECT_NO_PRICE, "tashqi": 2, "res": None,
                "yangi_raqamlar": 0, "band": None, "narxli_band": 0,
                "kodlar": ["IDENTICAL_COPY"], "comment_uz": IZOH_NUSXA,
                "review_sabab": None, "shakl_erkin": True}

    if matn_ishonchsiz:
        return _hukm_matnsiz(et_varaqlar, pt_varaqlar)

    # ASOSIY MEZON: buyurtmachi bo'sh qoldirgan kataklar son bilan
    # to'ldirilganmi (narx bo'lishi shart emas — kun, miqdor ham bo'ladi).
    toldi, n_toldi, n_slot = bosh_ustun_toldirilganmi(et_varaqlar, pt_varaqlar)
    if not toldi:
        # Qatorlar surilgan/varaq qayta nomlangan bo'lsa yuqoridagi o'lchov
        # 0 beradi — ustun bo'yicha ishlaydigan zaxira mezon.
        toldi, n_toldi, n_slot = narx_ustuni_toldirilganmi(
            et_varaqlar, pt_varaqlar)
    if toldi:
        return {"ichki": _ev.REVIEW_AMBIGUOUS, "tashqi": 1, "res": None,
                "yangi_raqamlar": None, "band": n_slot, "narxli_band": n_toldi,
                "kodlar": [], "comment_uz": IZOH_QABUL,
                "review_sabab": (f"buyurtmachi shablonidagi bo'sh kataklar "
                                 f"to'ldirilgan ({n_toldi}/{n_slot})"),
                "shakl_erkin": True}

    band, narxli = narx_nisbati(et_varaqlar, pt_varaqlar)
    yangi = yangi_narxsimon_soni(et_varaqlar, pt_varaqlar)

    if yangi >= SHAKL_ERKIN_KOP_SON:
        # #14-son: band-nisbatdan qat'i nazar — etalonda yo'q narxsimon
        # sonlar shuncha ko'p bo'lsa, bu to'ldirilgan hujjat (yuqoridagi izoh).
        return {"ichki": _ev.REVIEW_AMBIGUOUS, "tashqi": 1, "res": None,
                "yangi_raqamlar": yangi, "band": band, "narxli_band": narxli,
                "kodlar": [], "comment_uz": IZOH_QABUL,
                "review_sabab": (f"buyurtmachi shabloni tanilmadi; hujjatda "
                                 f"{yangi} ta yangi narx qiymati bor "
                                 f"(band nisbati {narxli}/{band} — smeta "
                                 f"shaklida narx tavsif qatoridan pastda)"),
                "shakl_erkin": True}

    if band == 0:
        # Tavsif qatori topilmadi (masalan hujjat butunlay raqamli jadval) —
        # bandga bo'linmasdan, yangi narxlar soniga qaraymiz.
        if yangi >= SHAKL_ERKIN_MIN_SON:
            return {"ichki": _ev.REVIEW_AMBIGUOUS, "tashqi": 1, "res": None,
                    "yangi_raqamlar": yangi, "band": 0, "narxli_band": 0,
                    "kodlar": [], "comment_uz": IZOH_QABUL,
                    "review_sabab": (f"buyurtmachi shabloni tanilmadi; hujjatda "
                                     f"{yangi} ta yangi narx qiymati bor"),
                    "shakl_erkin": True}
        return {"ichki": _ev.REJECT_NO_PRICE, "tashqi": 2, "res": None,
                "yangi_raqamlar": yangi, "band": 0, "narxli_band": 0,
                "kodlar": ["PRICE_EMPTY"], "comment_uz": IZOH_NARX_YOQ,
                "review_sabab": None, "shakl_erkin": True}

    talab = max(NARX_MIN_BAND, math.ceil(NARX_NISBATI * band))
    if narxli >= talab:
        foiz = narxli / band * 100
        return {"ichki": _ev.REVIEW_AMBIGUOUS, "tashqi": 1, "res": None,
                "yangi_raqamlar": yangi, "band": band, "narxli_band": narxli,
                "kodlar": [], "comment_uz": IZOH_QABUL,
                "review_sabab": (f"buyurtmachi shabloni tanilmadi; ishtirokchi "
                                 f"hujjatining {narxli}/{band} bandi "
                                 f"({foiz:.0f}%) narxlangan"),
                "shakl_erkin": True}

    # P1 (2026-09-11): shakl tanilmagan yo'lda ham ishtirokchining O'Z
    # hujjatidagi Q3 (#9 kichik shakl + #12-V3 varaqli maxraj) so'raladi —
    # 6 varaqli smetada eng yaxshi varaq 5/22 narxlangan, 4/5 varaq tegilgan
    # (181665) global 21/295 = 7% bilan rad etilardi. Bir xil dalilga ikki
    # yo'lda ikki xil javob chiqmasin (hukm yo'li bilan bir xil qoida).
    jiddiy_e, band_e, narxli_e = jiddiy_toldirilganmi(et_varaqlar, pt_varaqlar)
    if jiddiy_e:
        varaq_e = jiddiy_varaqli(et_varaqlar, pt_varaqlar)[3] if band_e != band else None
        foiz = (narxli_e / band_e * 100) if band_e else 0
        return {"ichki": _ev.REVIEW_AMBIGUOUS, "tashqi": 1, "res": None,
                "yangi_raqamlar": yangi, "band": band_e, "narxli_band": narxli_e,
                "kodlar": [], "comment_uz": IZOH_QABUL,
                "review_sabab": (f"buyurtmachi shabloni tanilmadi; ishtirokchi "
                                 f"hujjatining {narxli_e}/{band_e} bandi ({foiz:.0f}%) narxlangan"
                                 + (f" («{varaq_e}» jadvali bo'yicha)" if varaq_e else "")),
                "shakl_erkin": True}

    if narxli == 0:
        izoh, kod = IZOH_NARX_YOQ, "PRICE_EMPTY"
    elif narxli < NARX_MIN_BAND:
        # Nisbat yetarli bo'lishi mumkin (masalan 4/9 = 44%), lekin mutlaq
        # son kam — «shunchaki 3-4 ta qiymat» qabul qilinmaydi. Izohda
        # foiz emas, SONI aytiladi, aks holda matn chalg'itadi.
        izoh = (f"Hujjatda atigi {narxli} ta banda narx kiritilgan — "
                f"bu tender taklifi uchun yetarli emas.")
        kod = "PRICE_SPARSE"
    else:
        foiz = narxli / band * 100
        izoh = (f"Hujjatdagi {band} ta banddan atigi {narxli} tasiga "
                f"({foiz:.0f}%) narx kiritilgan — narxlar to'liq kiritilmagan.")
        kod = "PRICE_SPARSE"
    return {"ichki": _ev.REJECT_NO_PRICE, "tashqi": 2, "res": None,
            "yangi_raqamlar": yangi, "band": band, "narxli_band": narxli,
            "kodlar": [kod], "comment_uz": izoh, "review_sabab": None,
            "shakl_erkin": True}
