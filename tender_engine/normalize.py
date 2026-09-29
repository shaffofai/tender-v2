# -*- coding: utf-8 -*-
"""Matn normallashtirish — butun dvigatel uchun YAGONA manba.

Ilgari ikki nusxa bor edi: `validator.py` (rol aniqlash va `validate_one`
qoidalari ishlatardi) va shu modul (`decision` qatlami ishlatardi). Ular
AYNAN bir xil EMAS edi, shuning uchun bu yerda ikkala xulq ham saqlangan:

  * asosiy yig'ish (`fold_asosiy`, `normk_asosiy`, `kw`) — 16 juftlik.
    `roles` (rol aniqlash), `structure` va `rules` (validate_one) shunga
    tayanadi — eski `validator._fold` / `_normk` / `profiles._kw` aynan shu.
  * kengaytirilgan yig'ish (`fold`, `normk`) — Unicode confusables dan yana
    13 juftlik (`_KENGAYTMA`). Faqat `decision` qatlami ishlatadi.

Ularni BIRLASHTIRMANG: rol aniqlash yoki qoidalarga kengaytirilgan jadvalni
berish verdiktlarni o'zgartiradi (г→r, ш→w, ь→b kabi harflar kalit
so'zlarda uchraydi). Bunday o'zgarish alohida qaror va korpus o'lchovini
talab qiladi.

MUHIM INVARIANT: kirill-lotin ko'rinishdosh harflar `fold` bilan yechiladi
(NFKC BUNI QOPLAMAYDI); har yangi kalit so'z `kw()` orqali o'tishi SHART,
aks holda hech qachon mos kelmaydi.
"""

import re

_BOSHLIQ_RE = re.compile(r"\s+")

# Kirill harflarining LOTIN ko'rinishdoshlari (confusables).
#
# Eski `.xls` fayllarda sarlavhalar aralash klaviatura bilan yozilgan:
# «ЦEHA» da Ц kirill, E va H esa LOTIN — ko'zga bir xil, kod uchun boshqa
# belgi. Shuning uchun ikkala matnni ham bitta ko'rinishga keltiramiz:
# kirill ko'rinishdoshlar lotinga aylantiriladi. Aralash yozilgan matn ham,
# sof kirill matn ham AYNAN bir xil natija beradi.
#
# қ, ғ, ў, ҳ, ц, ш, ж kabi ko'rinishdoshi yo'q harflar tegilmaydi.
#
# 2026-08-12: jadval RASMIY manbadan kengaytirildi — Unicode UTS #39
# `confusables.txt` (Unicode 17.0.0, 2025-07-22, 6 565 ta mapping).
# Undan kirill→lotin 1:1 juftliklari ajratib olindi (`_KENGAYTMA`).
#
# Nozik joy: bizning quvurda matn AVVAL kichik harfga o'tkaziladi, keyin fold
# qilinadi. Unicode esa asl registrda ishlaydi («Н» ≅ «H», lekin «н» ≠ «h»),
# shuning uchun juftliklar UPPERCASE bo'yicha topilib, natija kichik harfda
# yoziladi — mavjud `н→h` xuddi shu mantiqdan kelib chiqqan.
#
# Tekshirilgan (scratchpad/fold_xavf.py): rasmiy ma'lumot bizning 16 ta
# mappingga ZID EMAS (0 ziddiyat), o'zbek harflari қ ғ ў ҳ ga TEGMAYDI,
# 125 ta kalit so'z orasida yangi qorishuv 0, narx↔hajm kesishmasi 0.
# `ё→e` rasmiy ma'lumotda yo'q — bizniki saqlanadi.
_ASOSIY = {
    "а": "a", "в": "b", "е": "e", "ё": "e", "к": "k", "м": "m", "н": "h",
    "о": "o", "р": "p", "с": "c", "т": "t", "у": "y", "х": "x",
    "і": "i", "ј": "j", "ѕ": "s",
}
_KENGAYTMA = {
    "г": "r",    # CYRILLIC SMALL LETTER GHE
    "ш": "w",    # CYRILLIC SMALL LETTER SHA
    "ь": "b",    # CYRILLIC SMALL LETTER SOFT SIGN («ctoиmoctь» ↔ «ctoиmoctb»)
    "ѡ": "w", "ѵ": "v", "ү": "y", "һ": "h", "ҽ": "e", "ӏ": "i",
    "ԁ": "d", "ԍ": "g", "ԛ": "q", "ԝ": "w",
}
_KORINISHDOSH = str.maketrans({**_ASOSIY, **_KENGAYTMA})
_KORINISHDOSH_ASOSIY = str.maketrans(_ASOSIY)


def fold(text: str) -> str:
    """Lotin/kirill ko'rinishdosh harflarni bitta ko'rinishga keltiradi."""
    return text.translate(_KORINISHDOSH)


def fold_asosiy(text: str) -> str:
    """`fold`, lekin faqat asosiy 16 juftlik bilan — rol aniqlash va
    `validate_one` qoidalari uchun (eski `validator._fold`)."""
    return text.translate(_KORINISHDOSH_ASOSIY)


def norm(value) -> str:
    """Return lowercase, whitespace-collapsed string; empty string for None.

    Excel sarlavhalarida satr uzilishi va uzilmas probel (NBSP) odatiy hol —
    «Стоимость\\nв текущих ценах» kabi. Ular oddiy probelga keltiriladi,
    aks holda ko'p so'zli kalit so'zlar hech qachon mos kelmaydi.
    """
    if value is None:
        return ""
    # \u200b — ko'rinmas nol-kenglikli probel
    s = str(value).replace("\xa0", " ").replace("\u200b", "")
    return _BOSHLIQ_RE.sub(" ", s).strip().lower()


def normk(value) -> str:
    """`norm` + ko'rinishdosh harflarni yig'ish — kalit so'z solishtiruvi uchun.

    Bu funksiya bilan solishtiriladigan kalit so'zlar ham `fold` dan
    o'tkazilgan bo'lishi SHART, aks holda mos kelmaydi.
    """
    return fold(norm(value))


def normk_asosiy(value) -> str:
    """`norm` + asosiy yig'ish — kalit so'z solishtiruvi uchun (eski
    `validator._normk`). Solishtiriladigan kalit so'zlar `kw()` dan o'tgan
    bo'lishi SHART."""
    return fold_asosiy(norm(value))


def is_numeric_nonzero(value) -> bool:
    """
    Return True if value is a non-None, non-zero number.
    Also handles numeric strings like '1 234,56' common in Russian-locale files.
    """
    if value is None:
        return False
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return value != 0
    # Try parsing numeric strings
    try:
        cleaned = str(value).replace("\xa0", "").replace(" ", "").replace(",", ".")
        return float(cleaned) != 0
    except (ValueError, TypeError):
        return False


def _kw(items):
    """Kalit so'zlarni katak matni bilan bir xil ko'rinishga keltiradi.

    Kataklar `normk_asosiy` (kichik harf + ko'rinishdosh harflar yig'ilgan)
    orqali o'tadi — kalit so'zlar ham aynan shunday bo'lishi shart.
    """
    if isinstance(items, str):
        return fold_asosiy(items.lower())
    return [_kw(x) for x in items]


kw = _kw
