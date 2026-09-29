# -*- coding: utf-8 -*-
"""tender_engine invariantlari — ko'chirish tugagach (2026-09-29).

Eski `validator.py` / `profiles.py` / `set_validator.py` dvigatelga to'liq
ko'chirildi, fasadlar olib tashlandi. Bu testlar ko'chirishning ikki
kafolatini qo'riqlaydi: (1) har funksiyaning BITTA nusxasi bor; (2) ikki
xil harf yig'ish (asosiy — rol/qoidalar, kengaytirilgan — decision)
ATAYLAB alohida saqlanadi (`tender_engine/normalize.py` izohiga qarang).
Ko'chirish paytidagi ekvivalentlik isboti: har Unicode kod nuqtasi bo'yicha
eski va yangi funksiyalar AYNAN bir xil natija bergan.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tender_engine import reader, normalize, roles, structure, decision, validate
from tender_engine.rules import common


# Qaltis kirishlar: homoglif, NBSP, nol-kenglik probel, satr uzilishi,
# ruscha son formati, bool/None/son chetlari
_QALTIS = [
    None, "", " ", 0, 1, -1, 0.0, 2.5, True, False,
    "ЦEHA", "CУMMA", "OБOCHOBAHИE", "EД.ИЗM", "KOЛ-BO",
    "Стоимость\nв текущих ценах", "Т/Р", "№ п/п",
    "narx\xa0joriy", "ish​haqi", "  ko'p   probel  ",
    "1 234,56", "1\xa0234,56", "12.5", "0,0", "#VALUE!", "#DIV/0!",
    "Жами қиймати", "қ ғ ў ҳ ц ш ж",
]


def test_asosiy_yigish_faqat_16_juftlik():
    """Rol aniqlash va qoidalar ASOSIY jadval bilan ishlaydi (eski
    `validator._fold`): kengaytma harflariga (г, ш, ь ...) TEGMAYDI."""
    assert len(normalize._ASOSIY) == 16
    for k, v in normalize._ASOSIY.items():
        assert normalize.fold_asosiy(k) == v
    for k in normalize._KENGAYTMA:
        assert normalize.fold_asosiy(k) == k, k
        assert normalize.fold(k) == normalize._KENGAYTMA[k], k
    for v in _QALTIS:
        assert normalize.normk_asosiy(v) == normalize.fold_asosiy(normalize.norm(v)), repr(v)


def test_normk_eskisining_kengaytmasi():
    """`tender_engine` yig'ish jadvali ATAYLAB kengroq (`_KENGAYTMA`): г→r,
    ш→w, ь→b va h.k. Shuning uchun ekvivalentlik emas, KENGAYTMA invarianti
    tekshiriladi: eski yig'ishdan o'tgan matn yangisida ayni natijani beradi.
    Bu buzilsa yangi jadval eskisini qaytaradigan emas, ALMASHTIRADIGAN
    bo'lib qoladi — kalit so'zlar jimgina mos kelmay qo'yadi.
    """
    for v in _QALTIS:
        assert normalize.normk(v) == normalize.normk(normalize.normk_asosiy(v)), repr(v)
    for s in ("ЦEHA", "цена", "aралаш ТЕКСТ", "қўшимча ҳаражат", "ИТОГО"):
        assert normalize.fold(s) == normalize.fold(normalize.fold_asosiy(s)), repr(s)


def test_jami_qatori_har_uch_tilda_tanaladi():
    """`decision._JAMI` kalitlari `normk` fazosida saqlanishi SHART.

    Xom holda yozilganda normk("ИТОГО")="иtoro" ro'yxatdagi "итого" ga mos
    kelmay, jami qatorlari BAND deb sanalardi — «faqat jami qatoriga narx
    yozish» (Q3 / PRICE_ONLY_TOTAL) himoyasi teshilardi.
    """
    for s in ("ИТОГО", "Итого:", "ИТОГО по смете", "Жами", "ЖАМИ:",
              "ВСЕГО", "Всего по разделу", "Jami", "JAMI:"):
        assert decision._jami_qatormi([s]), s
    for s in ("Стоимость", "Наименование работ", "Ish turlari", ""):
        assert not decision._jami_qatormi([s]), s


def test_jami_qatori_bandga_sanalmaydi():
    """Jami qatorining qo'shilishi nisbatga TA'SIR QILMASLIGI kerak —
    na maxrajga (band), na suratga (narxlangan band)."""
    et_asos = {"S": [["Nomi", "Narx"], ["ish 1", None], ["ish 2", None]]}
    pt_asos = {"S": [["Nomi", "Narx"], ["ish 1", 150000], ["ish 2", None]]}
    et_jami = {"S": et_asos["S"] + [["ИТОГО", None]]}
    pt_jami = {"S": pt_asos["S"] + [["ИТОГО", 150000]]}
    assert (decision.narx_nisbati(et_jami, pt_jami)
            == decision.narx_nisbati(et_asos, pt_asos))


def test_normalize_kw_ayni_obyekt():
    assert normalize.kw is roles._kw is normalize._kw


def test_reader_chegara_konstantalari():
    assert issubclass(reader.ExcelTooLargeError, ValueError)
    assert validate.read_file is reader.read_file              # bitta o'qish qatlami


def test_roles_haqiqiy_kod():
    assert roles.detect_role.__module__ == "tender_engine.roles"


def test_structure_bitta_nusxa():
    assert structure.varaq_moslash is structure._varaq_moslash
    assert structure.lcs_col_map is structure._lcs_col_map
    assert common._varaq_moslash is structure._varaq_moslash      # qoidalar shu nusxani ishlatadi


def test_decision_ayni_obyekt():
    assert decision.validate_one is validate.validate_one
    assert decision.STATUS_FILLED is validate.STATUS_FILLED
    assert decision.STATUS_DEFECT is validate.STATUS_DEFECT
    assert decision.STATUS_TECHNICAL is validate.STATUS_TECHNICAL
