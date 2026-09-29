# -*- coding: utf-8 -*-
"""Tekshiruvning kirish nuqtasi — `validate_one` va etalon o'qish.

Buyurtmachi etaloni bilan ishtirokchi faylini solishtiradi. Har bir fayl
uchun natija:
  {
    "role": "jamlanma|narxlar|resurs|loyiha|unknown",
    "status": 1 | 2 | 0,      # 1 = TO'LDIRILGAN, 2 = KAMCHILIK, 0 = texnik
    "findings": [ {code, sheet, detail_uz}, ... ],
    "comment_uz": "<o'zbekcha xulosa>"
  }

(Ilgari `set_validator.py` ning quyi qismi — ko'chirilgan, o'zgartirilmagan.
Rol qoidalari `tender_engine/rules/` da.)
"""

import collections
import os

from tender_engine.reader import read_file
from tender_engine.roles import (
    JAMLANMA_HEADER_ANCHORS,
    LOYIHA_NAME_KEYWORDS,
    LOYIHA_PRICE_KEYWORDS,
    NARXLAR_NAME_KEYWORDS,
    detect_role,
    loyiha_varagi,
)
from tender_engine.rules.common import (
    _BOSHLIQ_RE,
    _find_row_with_any,
    _parse_resource_sheet,
)
from tender_engine.rules.jamlanma import validate_jamlanma
from tender_engine.rules.loyiha import validate_loyiha
from tender_engine.rules.narxlar import validate_narxlar
from tender_engine.rules.resurs import validate_resurs


STATUS_FILLED = 1      # to'ldirilgan
STATUS_DEFECT = 2      # kamchilik
STATUS_TECHNICAL = 0   # tekshirib bo'lmadi — hujjat aybdor EMAS


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

_VALIDATORS = {
    "jamlanma": validate_jamlanma,
    "narxlar": validate_narxlar,
    "resurs": validate_resurs,
    "loyiha": validate_loyiha,
}


def _etalon_oqiladimi(role, etalon_sheets):
    """Shu ROL qoidasi berilgan ETALONNI umuman tahlil qila oladimi?

    Tender shakllari xilma-xil: bir xil `type` ostida bizga notanish
    ko'rinishlar ham keladi (masalan o'zbekcha-lotin «UMUMIY JADVAL»).
    Bunday etalonda sarlavhani topa olmasak, ishtirokchi faylini ham
    tekshira olmaymiz.

    OLTIN QOIDA: bu BIZNING cheklovimiz, hujjatning kamchiligi emas —
    shuning uchun `status = 2` (rad) berilmaydi, qator navbatda qoladi.
    """
    if not etalon_sheets:
        return True          # etalonsiz tekshiruv boshqa joyda ushlanadi
    varaqlar = list(etalon_sheets.values())
    if role == "jamlanma":
        # D+ (2026-08-04): narx ustuni nomi ham langar — «1|2|3» raqamlangan
        # Форма-4 shakllarida tavsif ustuni nomsiz bo'ladi
        return _find_row_with_any(varaqlar[0], JAMLANMA_HEADER_ANCHORS) is not None
    if role == "narxlar":
        return _find_row_with_any(varaqlar[0], NARXLAR_NAME_KEYWORDS) is not None
    if role == "loyiha":
        nom = loyiha_varagi(etalon_sheets)
        rows = etalon_sheets[nom] if nom else varaqlar[0]
        return (_find_row_with_any(rows, LOYIHA_NAME_KEYWORDS) is not None
                or _find_row_with_any(rows, LOYIHA_PRICE_KEYWORDS) is not None)
    if role == "resurs":
        return any(_parse_resource_sheet(r) for r in varaqlar)
    return True


def validate_one(role, etalon_sheets, part_sheets, filename):
    """Bitta faylni roli bo'yicha tekshiradi va standart natija dict qaytaradi."""
    fn = os.path.basename(filename)

    if role in _VALIDATORS and not _etalon_oqiladimi(role, etalon_sheets):
        f = [{"code": "ETALON_UNPARSED", "sheet": None, "severity": "technical",
              "detail_uz": f"Buyurtmachi shabloni «{role}» shakliga mos emas — "
                           f"sarlavha topilmadi, tekshirib bo'lmadi."}]
        return {"file": fn, "role": role, "status": STATUS_TECHNICAL,
                "findings": f, "comment_uz": ""}

    if role not in _VALIDATORS:
        return {
            "file": fn, "role": role, "status": STATUS_DEFECT,
            "findings": [{"code": "UNKNOWN_ROLE", "sheet": None,
                          "detail_uz": "Fayl turini aniqlab bo'lmadi."}],
            "comment_uz": _render_comment(fn, [{"code": "UNKNOWN_ROLE", "sheet": None,
                          "detail_uz": "Fayl turini aniqlab bo'lmadi."}]),
        }
    findings = _VALIDATORS[role](etalon_sheets, part_sheets)
    defects = [f for f in findings if f.get("severity") != "note"]
    status = STATUS_DEFECT if defects else STATUS_FILLED
    return {
        "file": fn,
        "role": role,
        "status": status,
        "findings": findings,
        "comment_uz": _render_comment(fn, findings),
    }


_MAX_KAMCHILIK = 4      # izohda to'liq ko'rsatiladigan kamchiliklar soni


def _render_comment(_filename, findings, limit=1000):
    """Findinglarni ishtirokchiga tushunarli o'zbekcha izohga aylantiradi.

    Har bir kamchilik O'Z MATNI bilan yoziladi — qaysi jadval va qaysi USTUN
    ekani ko'rinadi, masalan:
        «УК АР» jadvalida qo'shilgan «стоимость на единицу» ustuni bo'sh —
        qiymatlar kiritilmagan.

    Kamchilik ko'p bo'lsa birinchi `_MAX_KAMCHILIK` tasi yoziladi, qolgani
    soni bilan qisqartiriladi (20 jadvalli Физобъемлар uchun ham izoh
    cho'zilib ketmasin).

    `_filename` ATAYLAB ishlatilmaydi — fayl nomi baza qatorida (`link`,
    `type`) allaqachon bor. Parametr chaqiruvchilar bilan moslik uchun.
    severity='note' statusga ta'sir qilmaydi va izohga kiritilmaydi.
    """
    defects = [f for f in findings if f.get("severity") != "note"]
    if not defects:
        return "Hujjat to'g'ri to'ldirilgan."

    matnlar = []
    korilgan = set()
    for f in defects:
        # Excel sarlavhalarida satr uzilishi bo'lishi odatiy holat
        # («Стоимость\nв текущих ценах»). Izoh bitta qatorda o'qilsin.
        d = _BOSHLIQ_RE.sub(" ", (f.get("detail_uz") or "")).strip()
        if d and d not in korilgan:
            korilgan.add(d)
            matnlar.append(d)

    text = " ".join(matnlar[:_MAX_KAMCHILIK])
    qolgan = len(matnlar) - _MAX_KAMCHILIK
    if qolgan > 0:
        text += f" Shuningdek yana {qolgan} ta shunday kamchilik aniqlandi."
    if len(text) > limit:
        text = text[:limit - 1].rstrip() + "…"
    return text


# ---------------------------------------------------------------------------
# To'plam (3 fayl) tekshiruvi
# ---------------------------------------------------------------------------

def load_sheets(path):
    """Faylni o'qib {sheet: rows} qaytaradi; xato bo'lsa (None, xabar)."""
    try:
        return read_file(path), None
    except Exception as exc:
        return None, str(exc)


# ── ETALON PARSE KESHI (2026-09-02) ───────────────────────────────────────
# Diskdagi kesh faqat QAYTA YUKLASHNI to'xtatadi — parse har safar qaytadan
# bajarilardi. Bitta etalon o'nlab ishtirokchi fayliga ishlatiladi va yirik
# smeta shabloni (3 MB, minglab qator) ~30-60 soniya parse bo'ladi: 143 000
# faylli yurishda bu soatlab behuda ish.
#
# Navbat `id` tartibida keladi, bir tenderning fayllari esa yonma-yon —
# shuning uchun kichik LRU deyarli har doim tegadi. Kalitga fayl mtime va
# hajmi kiradi: etalon almashtirilsa kesh o'z-o'zidan yangilanadi.
_ETALON_PARSE_KESH = collections.OrderedDict()
_ETALON_PARSE_KESH_MAX = int(os.environ.get("ETALON_PARSE_CACHE", "6"))


def load_etalon_sheets(path):
    """`load_sheets`, lekin natija kichik LRU da saqlanadi.

    FAQAT ETALON uchun — ishtirokchi fayllari takrorlanmaydi, ularni
    keshlashning ma'nosi yo'q va xotirani behuda band qilardi.
    """
    try:
        st = os.stat(path)
        kalit = (os.path.abspath(path), int(st.st_mtime), st.st_size)
    except OSError as exc:
        return None, str(exc)

    kesh = _ETALON_PARSE_KESH.get(kalit)
    if kesh is not None:
        _ETALON_PARSE_KESH.move_to_end(kalit)
        return kesh[0], kesh[1]

    varaqlar, xato = load_sheets(path)
    # Xatoni ham keshlaymiz: o'qilmaydigan etalon DETERMINIK, qayta-qayta
    # urinish faqat vaqt sarflaydi (ETALON_UNPARSED yo'li bilan bir xil).
    _ETALON_PARSE_KESH[kalit] = (varaqlar, xato)
    while len(_ETALON_PARSE_KESH) > max(1, _ETALON_PARSE_KESH_MAX):
        _ETALON_PARSE_KESH.popitem(last=False)
    return varaqlar, xato


def validate_participant_file(part_path, etalon_path, role=None):
    """Bitta ishtirokchi faylini mos etalon bilan tekshiradi.

    role berilmasa etalondan (yoki fayldan) aniqlanadi.
    """
    fn = os.path.basename(part_path)
    pt_sheets, err = load_sheets(part_path)
    if pt_sheets is None:
        f = [{"code": "FILE_UNREADABLE", "sheet": None,
              "detail_uz": f"Faylni ochib bo'lmadi: {err}. Faylni qayta yuklang."}]
        return {"file": fn, "role": role or "unknown", "status": STATUS_DEFECT,
                "findings": f, "comment_uz": _render_comment(fn, f)}

    et_sheets = {}
    if etalon_path:
        et_sheets, _ = load_sheets(etalon_path)
        et_sheets = et_sheets or {}

    if role is None:
        role = detect_role(et_sheets) if et_sheets else "unknown"
        if role == "unknown":
            role = detect_role(pt_sheets)

    return validate_one(role, et_sheets, pt_sheets, fn)
