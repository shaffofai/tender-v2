# -*- coding: utf-8 -*-
"""Rol aniqlash — har bir fayl ROLI va uning tekshiruv profili.

(Ilgari `profiles.py`; `tender_engine/roles.py` unga fasad edi. Endi kod
shu yerda — ko'chirilgan, o'zgartirilmagan.)

4 rol:
  - jamlanma : "1. Жамланма жадвал" (Форма 4)   — QAT'IY tuzilma
  - narxlar  : "3. Асосий нарх ... нархлар жадвали" — QATORLAR ERKIN
  - resurs   : "Физобъемлар" / "КАП_РЕМОНТ" (ko'p varaqli smeta) — ADDITIVE/ARALASH
  - loyiha   : «5-ILOVA» / SMETA HISOBI

Tarixiy №1 noo'rin-rad manbai rol adashuvi bo'lgan (buyurtmachi slotga
noto'g'ri shakl yuklaydi) — shuning uchun bu modul alohida va sinovlar
bilan qattiq qoplanishi kerak. TYPE_ROL zaxirasi `app/templates.py` da.

Bu modul faqat aniqlash va konstantalar bilan shug'ullanadi; tekshiruv
qoidalari `tender_engine/rules/` da.
"""

from tender_engine.normalize import _kw, normk_asosiy as _norm


# ---------------------------------------------------------------------------
# Narx (price) kalit so'zlari — rolga qarab
# ---------------------------------------------------------------------------

# Jamlanma jadvalidagi yagona narx ustuni.
# Eng aniq nomdan eng umumiysiga qarab tartiblangan — qidiruvda birinchi
# mos kelgani olinadi.
#
# TARTIB MUHIM: yangi kalit HAR DOIM ro'yxatning OXIRIGA qo'shiladi —
# shunda hozir narx ustuni topilayotgan etalonlarda tanlov o'zgarmaydi
# (regressiyasizlik kafolati).
JAMLANMA_PRICE_KEYWORDS = _kw([
    "жами қиймати", "жами киймати",             # o'zbekcha-kirill
    "стоимость в текущих ценах", "стоимость",   # ruscha (Форма 4)
    "қиймати", "киймати",
    "jami qiymati", "qiymati", "summasi",       # o'zbekcha-lotin
    # ── 2026-08-04: notanish shablonlar tahlilidan (faqat OXIRIGA!) ──────
    "narxi joriy narxlarda",      # o'zbekcha-lotin Форма 4 (K3 klasteri)
    "жами (минг",                 # «Жами (минг.сўм)» — yolg'iz «жами» MUMKIN EMAS
    "цена (в сумах)",             # yolg'iz «цена» emas — qavs bilan shaklga xos
    "основная зарплата, т.сум",   # yo'l smetasi shakli (K14)
    "to'g'ridan-to'g",            # «to'g'ridan-to'g(')ri xarajat» — ikkala imlo
    "narxi",                      # lotin variantlar zaxirasi
])

# Narxlar jadvalidagi narx ustuni
NARXLAR_PRICE_KEYWORDS = _kw([
    "бирлик учун нарх",                       # o'zbekcha-kirill
    "на единицу измерения",                   # ruscha (ВЕДОМОСТЬ ОБЪЕМОВ РАБОТ)
    "birlik uchun narx",                      # o'zbekcha-lotin
    "нарх", "цена", "narx", "narxi",
])

# Sarlavha qatorini topish uchun "nom" ustunining langarlari (ikki tilda).
# `цена`/`сумма` emas — aynan tavsif ustuni, chunki u har doim bor.
JAMLANMA_NAME_KEYWORDS = _kw([
    "харажатлар ном",     # «...номи» ham, «...номланиши» ham (2026-08-04)
    "наименование расходов", "xarajatlar nomi",
    # ── 2026-08-04: 87 notanish shablon tahlilidan. Har biri UZUN va
    # shaklga xos — umumiy so'z («наименование») QAT'IYAN qo'shilmaydi,
    # aks holda har qanday smeta jamlanma bo'lib qoladi.
    "иш турлари номланиши",       # yolg'iz «иш турлари» EMAS (loyiha roli bilan to'qnashadi)
    "харажатларни номлаш",
    "ishlarning nomi",
    "месторасположения дорог",
    "н а и м е н о в а н и е з а т р а т",   # harflar orasi probelli yozilish
])

# 7-taklif D+ (2026-08-04): jamlanma SARLAVHA QATORINI topish uchun
# kengaytirilgan langarlar. Ba'zi Форма-4 shakllarida tavsif ustuni nomsiz,
# yagona ajratuvchi matn — NARX ustunining nomi. Bu ro'yxat FAQAT qator
# topishda (`_etalon_oqiladimi`, sarlavha izlash) ishlatiladi, USTUN
# INDEKSINI aniqlashda EMAS: narx nomi bilan nom ustuni izlansa
# name_idx == price_idx bo'lib, to'ldirilganlik nisbati narx ustunini o'zi
# bilan solishtiradi va BO'SH hujjat ham qabul bo'lib qolardi.
JAMLANMA_HEADER_ANCHORS = JAMLANMA_NAME_KEYWORDS + _kw([
    "стоимость в текущих ценах",
    "narxi joriy narxlarda",
])
NARXLAR_NAME_KEYWORDS = _kw([
    "компонент номи", "наименование компонента", "komponent nomi",
])

# ── LOYIHA («5-ILOVA» / «SMETA HISOBI») ────────────────────────────────────
# 2026-07-31 da paydo bo'lgan `loyiha_excel` turi. O'zbekcha-LOTIN yozuvdagi
# smeta shakli, bitta varaq:
#     T/r | Ish turlari | Asos | Narxning kelib chiqishi | Qiymat (ming so'm)
# Kelishilgan siyosat: FAQAT «Qiymat» ustuni to'ldirilganmi tekshiriladi —
# ustun soni, nomlari va qatorlar soni muhim emas.
LOYIHA_NAME_KEYWORDS = _kw([
    "ish turlari", "иш турлари", "ish turi",
])
LOYIHA_PRICE_KEYWORDS = _kw([
    "qiymat", "қиймат", "киймат", "summa", "сумма",
])

# Resurs (Физобъемлар/КАП_РЕМОНТ) fayllaridagi narx ustunlari (substring bilan qidiriladi).
# Bular etalonda BO'SH turishi yoki ishtirokchi tomonidan QO'SHILISHI mumkin.
RESURS_PRICE_KEYWORDS = _kw([
    "стоимость",          # СТОИМОСТЬ / Сметная стоимость / Стоимость
    "цена",               # ЦЕНА (ВСЕГО) / Цена
    "сумма",              # Сумма
    "предложение претендента",   # ishtirokchi narx blokining sarlavhasi
    "narx", "qiymat", "summa", "baho",   # o'zbekcha-lotin
])

# Excel formulasi xato matnlari — narx katagida bo'lsa KAMCHILIK
ERROR_VALUE_MARKERS = _kw([
    "#value!", "#ref!", "#div/0!", "#n/a", "#name?", "#null!", "#num!", "#дел/0!",
])

# ---------------------------------------------------------------------------
# Rol aniqlash uchun sarlavha imzolari
# ---------------------------------------------------------------------------

# Har bir rol uchun sarlavha qatorida BO'LISHI kerak bo'lgan kalit so'zlar.
# (normallashtirilgan, substring solishtiruvi)
#
# DIQQAT: bir xil shakl ikki tilda keladi — o'zbekcha-kirill (`Жами қиймати`)
# va ruscha (`Стоимость в текущих ценах`). Ikkalasini ham bilishimiz shart,
# aks holda rol noto'g'ri aniqlanib, faylga BOSHQA siyosat qo'llanadi.
# Har bir ro'yxat — ALTERNATIV imzo; ichidagi kalit so'zlarning HAMMASI
# uchrasa, imzo mos kelgan hisoblanadi.
JAMLANMA_SIGNATURES = _kw([
    ["харажатлар номи"],            # o'zbekcha: Жамланма жадвал
    ["жами қиймати"],
    ["жами киймати"],
    ["наименование расходов"],      # ruscha: Форма 4 / СВОДНАЯ ТАБЛИЦА
    ["сводная таблица"],
    ["сводный расчет"],
    ["жамланма жадвал"],
    ["jamlanma jadval"],
    # ── 2026-08-04: notanish shablonlar tahlilidan. Ko'p varaqli Форма-4
    # etalonlari «ko'p varaq → resurs» zaxirasiga tushib ketmasligi uchun.
    ["иш турлари номланиши"],
    ["umumiy jadval"],                       # СВОДНАЯ ТАБЛИЦА ning lotinchasi
    ["сводная объектная смета"],
    ["р а с ч е т о б щ е й с т а р т о в о й"],
])
NARXLAR_SIGNATURES = _kw([
    ["компонент номи"],             # o'zbekcha: Нархлар жадвали
    ["бирлик учун нарх"],
    ["наименование компонента"],    # ruscha: ВЕДОМОСТЬ ОБЪЕМОВ РАБОТ
    ["основных ценообразователей"],
    ["нархлар жадвали"],
    ["narxlar jadvali"],
])
LOYIHA_SIGNATURES = _kw([
    ["smeta hisobi"],                            # SMETA HISOBI — shakl nomi
    ["narxning kelib chiqishi"],                 # faqat shu shaklda uchraydi
    ["ish turlari", "qiymat"],
    ["смета хисоби"],                            # kirill yozuvdagi varianti
    ["нархнинг келиб чикиши"],
])
RESURS_SIGNATURE = _kw([
    "наименование работ и затрат",
    "наименование работ и ресурсов",
    "обоснование",
    "наименование затрат",   # СТАРТ varag'i
    "наименование ресурса",
    "локальная ресурсная",       # ЛОКАЛЬНАЯ РЕСУРСНАЯ ВЕДОМОСТЬ
    "локальный сметный",         # ЛОКАЛЬНЫЙ СМЕТНЫЙ РАСЧЕТ
    "локальная смета",
    "шифр номера нормативов",    # smeta ustuni — faqat resurs shaklida bor
    "ед.изм",                    # eski .xls smeta sarlavhasi (ЕД.ИЗМ / КОЛ-ВО)
])

# Eski nom bilan moslik (tashqi kod ishlatgan bo'lsa)
JAMLANMA_SIGNATURE = _kw(["харажатлар номи", "жами қиймати"])
NARXLAR_SIGNATURE = _kw(["компонент номи", "бирлик учун нарх"])

# Resurs sarlavhasining boshlanish (struktura) kalit so'zlari — sarlavha
# qatorini topish uchun.
RESURS_HEADER_ANCHORS = _kw([
    "№№", "n п.п.", "n п.п", "№ п/п", "№пп", "№ пп", "т/р", "t/r",
])

# To'ldirilganlik chegarasi (validator.py bilan bir xil)
FILL_THRESHOLD = 0.30


# ---------------------------------------------------------------------------
# Rol aniqlash
# ---------------------------------------------------------------------------

def _scan_first_rows(sheet_rows, limit=15):
    """Varaqning birinchi `limit` qatoridagi barcha kataklarni normallashgan
    matn ro'yxati sifatida qaytaradi."""
    out = []
    for row in sheet_rows[:limit]:
        for cell in row:
            n = _norm(cell)
            if n:
                out.append(n)
    return out


def _sheet_has_signature(cells, signature):
    """`signature` dagi HAR bir kalit so'z `cells` ichida substring sifatida
    uchrasa True."""
    return all(any(kw in c for c in cells) for kw in _kw(signature))


def _sheet_has_any(cells, keywords):
    return any(any(kw in c for c in cells) for kw in _kw(keywords))


def _matches_any(cells, signatures):
    """Alternativ imzolardan HECH BO'LMASA bittasi to'liq mos kelsa True."""
    return any(_sheet_has_signature(cells, sig) for sig in signatures)


def loyiha_varagi(sheets, limit=8):
    """«SMETA HISOBI» shakli joylashgan varaq NOMINI qaytaradi (yoki None).

    Ayrim `loyiha_excel` fayllarida bu shakldan oldin «4 ИЛОВА»
    (КАЛЕНДАРЬ ИШ ЖАДВАЛИ) varag'i turadi — shuning uchun birinchi varaqqa
    emas, hammasiga qaraladi. Tekshiruv ham aynan shu varaq bo'yicha
    o'tkaziladi.
    """
    if not sheets:
        return None
    for nom in list(sheets.keys())[:limit]:
        if _matches_any(_scan_first_rows(sheets[nom]), LOYIHA_SIGNATURES):
            return nom
    return None


def detect_role(sheets):
    """
    {sheet_name: rows} berilganda faylning rolini aniqlaydi.

    Returns: 'jamlanma' | 'narxlar' | 'resurs' | 'unknown'
    """
    if not sheets:
        return "unknown"

    sheet_names = list(sheets.keys())
    cells = _scan_first_rows(sheets[sheet_names[0]])

    # 0) LOYIHA («5-ILOVA» / SMETA HISOBI) — imzosi eng o'ziga xos, shuning
    #    uchun birinchi tekshiriladi.
    #
    #    HAMMA varaq ko'riladi: ba'zi fayllarda oldinda «4 ИЛОВА»
    #    (КАЛЕНДАРЬ ИШ ЖАДВАЛИ) turadi va faqat birinchi varaqqa qarasak,
    #    fayl ko'p varaqli deb `resurs` ga tushib ketardi.
    if loyiha_varagi(sheets) is not None:
        return "loyiha"
    if len(sheet_names) == 1 and "ilova" in _norm(sheet_names[0]) and \
            _sheet_has_any(cells, LOYIHA_NAME_KEYWORDS):
        return "loyiha"

    # 1) Aniq imzolar — eng ishonchli belgi, varaq sonidan ustun turadi.
    if _sheet_has_any(cells, RESURS_SIGNATURE):
        return "resurs"
    if _matches_any(cells, JAMLANMA_SIGNATURES):
        return "jamlanma"
    if _matches_any(cells, NARXLAR_SIGNATURES):
        return "narxlar"

    # 2) Ko'p varaqli bo'lsa — deyarli aniq resurs (Физобъемлар/КАП_РЕМОНТ).
    #    Imzo boshqa varaqda bo'lishi mumkin, shu sababli qolganini ham ko'ramiz.
    if len(sheet_names) >= 2:
        for nom in sheet_names[1:6]:
            if _sheet_has_any(_scan_first_rows(sheets[nom]), RESURS_SIGNATURE):
                return "resurs"
        return "resurs"

    # 3) Zaxira: sarlavha ustunlari bo'yicha zaif taxmin.
    #    `№ п/п` kabi umumiy langarlarga TAYANMAYMIZ — u har uchala shaklda
    #    ham uchraydi, ilgari shu sababli hamma fayl «resurs» bo'lib ketgan.
    if _sheet_has_any(cells, _kw(["стоимость в текущих ценах", "жами киймат"])):
        return "jamlanma"
    if _sheet_has_any(cells, _kw(["на единицу измерения", "бирлик учун"])):
        return "narxlar"

    return "unknown"


def is_price_keyword(name, role="resurs"):
    """Berilgan (normallashgan) ustun nomi narx ustunimi?"""
    n = _norm(name)
    if not n:
        return False
    if role == "jamlanma":
        kws = JAMLANMA_PRICE_KEYWORDS
    elif role == "narxlar":
        kws = NARXLAR_PRICE_KEYWORDS
    elif role == "loyiha":
        kws = LOYIHA_PRICE_KEYWORDS
    else:
        kws = RESURS_PRICE_KEYWORDS
    return any(kw in n for kw in kws)


def has_error_value(value):
    """Katak qiymati Excel xato matnimi (#VALUE! va h.k.)?"""
    n = _norm(value)
    if not n:
        return False
    return any(marker in n for marker in ERROR_VALUE_MARKERS)
