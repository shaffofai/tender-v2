# -*- coding: utf-8 -*-
"""NOL SIYOSATI (2026-09-17/18, buyurtmachi qarori — O'RTACHA YO'L) — regressiya korpusi.

Buyurtmachi jadvali (aynan):

    hamma katakka 0 (int)        -> RAD    (hujjatda bironta ham nolmas son yo'q — #11-b)
    hamma katakka 0.0            -> RAD
    hamma katakka "0" (matn)     -> RAD
    5 nol + 5 narx               -> QABUL
    9 nol + 1 narx               -> QABUL  (0 = to'ldirilgan katak, bitta haqiqiy narx bor)
    "1200000" matn               -> QABUL
    "1 200 000,00"               -> QABUL
    "   " probel                 -> RAD    (bo'sh = kiritilmagan)
    aynan nusxa                  -> status 3 (Q1 darvozasi, 2026-09-18)

Tarix: 2026-09-15 «0 = qiymat emas» (9 nol + 1 narx → RAD) kiritilgan edi;
2026-09-17 bekor qilindi; «hamma 0 → qabul» varianti o'lchanib RAD ETILDI (A da
27 tasdiqlangan noo'rin qabul ochilardi) → 2026-09-18 O'RTACHA YO'L = 09-14
holati: narx ustuni o'lchovlarida 0 to'ldirilgan katak, LEKIN `_nolmas_yangi_son`
nolni sanamaydi — faqat nollar yozilgan hujjat bo'sh hujjat.

Zanjir `korpus/regressiya/yurgiz.jonli_zanjir` — jobs_worker bilan aynan
bir xil yo'l (darvoza -> validate_one -> shakl-erkin -> hukm). Bazasiz.
"""
import os
import shutil
import sys

import pytest
from openpyxl import Workbook

LOYIHA = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, LOYIHA)
sys.path.insert(0, os.path.join(LOYIHA, "korpus", "regressiya"))

# `yurgiz` — korpus/regressiya/ (repo TASHQARISIDA, jamoa muhitida). Regressiya
# darvozasi bilan bir xil qoida: yo'qligi JIM o'tkazib yuborilmaydi —
# ataylab o'tkazish uchun KORPUS_SHART_EMAS=1 (test_regressiya_korpus.py ga qarang).
try:
    import yurgiz                              # noqa: E402
except ImportError:
    if os.environ.get("KORPUS_SHART_EMAS", "").strip().lower() in ("1", "true", "yes", "ha"):
        pytest.skip("korpus/regressiya/yurgiz.py yo'q (KORPUS_SHART_EMAS=1)",
                    allow_module_level=True)
    raise
from tender_engine import decision as d        # noqa: E402
from tender_engine.reader import read_file     # noqa: E402

BANDLAR = [
    "Затраты на эксплуатацию машин", "Затраты на строительные материалы",
    "Затраты на кабельно-проводниковую продукцию", "Затраты на конструкции",
    "Затраты на оборудование", "Основная заработная плата",
    "Отчисление на социальное страхование", "Прочие затраты и расходы",
    "Затраты на страхование", "Коэффициент риска",
]
NARXLAR = [1200000, 850000, 430000, 990000, 15000,
           72000, 310000, 66000, 8800, 12000]


def _forma4(yol, narxlar):
    """Форма-4 (jamlanma): `№ п/п | Наименование расходов | Стоимость ...`."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append([None, None, "Форма 4"])
    ws.append([])
    ws.append(["СВОДНАЯ ТАБЛИЦА", "СВОДНАЯ ТАБЛИЦА", "СВОДНАЯ ТАБЛИЦА"])
    ws.append([])
    ws.append(["№ п/п", "Наименование расходов", "Стоимость в текущих ценах"])
    ws.append([1, 2, 3])
    for i, (b, n) in enumerate(zip(BANDLAR, narxlar), 1):
        ws.append([i, b, n])
    ws.append([None, "ИТОГО", None])
    wb.save(yol)
    return yol


@pytest.fixture(scope="module")
def etalon(tmp_path_factory):
    p = tmp_path_factory.mktemp("nol")
    return _forma4(str(p / "etalon.xlsx"), [None] * 10)


HOLATLAR = [
    # nom,                narxlar,                                            kutilgan
    ("hamma_0_int",       [0] * 10,                                            2),
    ("hamma_0_float",     [0.0] * 10,                                          2),
    ("hamma_0_matn",      ["0"] * 10,                                          2),
    ("5_nol_5_narx",      [0, 0, 0, 0, 0] + NARXLAR[5:],                       1),
    ("9_nol_1_narx",      [0] * 9 + [NARXLAR[0]],                              1),
    ("narx_matn",         [str(n) for n in NARXLAR],                           1),
    ("narx_mingli_vergul", [f"{n:,}".replace(",", " ") + ",00" for n in NARXLAR], 1),
    ("probel",            ["   "] * 10,                                        2),
]


@pytest.mark.parametrize("nom,narxlar,kutilgan", HOLATLAR,
                         ids=[h[0] for h in HOLATLAR])
def test_buyurtmachi_jadvali(tmp_path, etalon, nom, narxlar, kutilgan):
    pt = _forma4(str(tmp_path / f"{nom}.xlsx"), narxlar)
    n = yurgiz.jonli_zanjir("jamlanma", etalon, pt, f"{nom}.xlsx")
    assert n["status"] == kutilgan, (
        f"{nom}: kutilgan={kutilgan}, chiqdi={n['status']} "
        f"[{n['ichki']} {n['qoida']} {n['kodlar']}]")


def test_aynan_nusxa_status_3(tmp_path, etalon):
    """Q1 darvozasi (2026-09-18): aynan nusxa — alohida status 3 (RAD 2 emas)."""
    pt = str(tmp_path / "nusxa.xlsx")
    shutil.copyfile(etalon, pt)
    n = yurgiz.jonli_zanjir("jamlanma", etalon, pt, "nusxa.xlsx")
    assert n["status"] == 3 and "AYNAN_NUSXA" in n["kodlar"]
    assert n["ichki"] == "IDENTICAL_COPY" and n["qoida"] == "darvoza:sha256"


# ── O'lchov darajasida: nol «to'ldirilgan» katak sanaladi ─────────────────

def test_narx_ustuni_toldirilganmi_nolni_sanaydi(tmp_path, etalon):
    et = read_file(etalon)
    pt = read_file(_forma4(str(tmp_path / "n.xlsx"), [0] * 10))
    ok, toldi, band = d.narx_ustuni_toldirilganmi(et, pt)
    assert ok and toldi == 10, (ok, toldi, band)


def test_narx_ustuni_toldirilganmi_9nol_1narx(tmp_path, etalon):
    et = read_file(etalon)
    pt = read_file(_forma4(str(tmp_path / "n.xlsx"), [0] * 9 + [1200000]))
    ok, toldi, band = d.narx_ustuni_toldirilganmi(et, pt)
    assert ok and toldi == 10, (ok, toldi, band)


def _sodda(yol, qiymatlar):
    """Bitta sarlavhali sodda jadval — `bosh_ustun_holati` uchun.

    Sarlavha qatori SHART: etalonda narx katagi `None` bo'lgani uchun o'quvchi
    qator oxiridagi bo'sh katakni tashlab yuboradi va matritsa kengligi 1
    bo'lib qoladi. Sarlavhada matn bo'lsa kenglik 2 bo'ladi.
    """
    wb = Workbook()
    ws = wb.active
    ws.append(["Наименование расходов", "Стоимость"])
    for b, v in zip(BANDLAR, qiymatlar):
        ws.append([b, v])
    wb.save(yol)
    return yol


def test_bosh_ustun_holati_nolni_sanaydi(tmp_path):
    et = read_file(_sodda(str(tmp_path / "et.xlsx"), [None] * 10))
    pt0 = read_file(_sodda(str(tmp_path / "p0.xlsx"), [0] * 10))
    pt1 = read_file(_sodda(str(tmp_path / "p1.xlsx"), NARXLAR))
    bosh_n, toldi0, slot0 = d.bosh_ustun_holati(et, pt0)
    assert bosh_n >= 1 and slot0 == 10, (bosh_n, toldi0, slot0)
    assert toldi0 == 10, "nol «to'ldirilgan» sanalmadi"
    _, toldi1, _ = d.bosh_ustun_holati(et, pt1)
    assert toldi1 == 10


def test_shakl_erkin_hamma_nol_09_14_holati(tmp_path):
    """OCHIQ BO'SHLIQ (hujjatlangan, 2026-09-18): shakl-erkin yo'lda (shablon
    tanilmaganda) #11-b qo'riqchisi YO'Q — hamma katak 0 bo'lsa
    `bosh_ustun_toldirilganmi` orqali QABUL (09-14 daraxtida o'lchab tasdiqlandi:
    tashqi=1, «bo'sh kataklar to'ldirilgan (10/10)»). O'rtacha yo'l = 09-14
    holati, shuning uchun bu test hozirgi xulqni QOTIRADI; qo'riqchini shu yo'lga
    ham qo'shish — alohida qadam (decision.py ga tegadi, A+B o'lchovi + tasdiq).
    Yopilganda bu testni `tashqi == 2` ga o'zgartiring."""
    et = read_file(_sodda(str(tmp_path / "et.xlsx"), [None] * 10))
    pt = read_file(_sodda(str(tmp_path / "p0.xlsx"), [0] * 10))
    h = d.hukm_shakl_erkin(et, pt)
    assert h["tashqi"] == 1 and h["ichki"] == "REVIEW_AMBIGUOUS", h


def test_nolmas_yangi_son_nolni_sanamaydi(tmp_path):
    """#11-b asosi: faqat nollar → 0; bitta haqiqiy narx → 1."""
    et = read_file(_sodda(str(tmp_path / "et.xlsx"), [None] * 10))
    assert d._nolmas_yangi_son(et, read_file(_sodda(str(tmp_path / "p0.xlsx"), [0] * 10))) == 0
    assert d._nolmas_yangi_son(et, read_file(_sodda(str(tmp_path / "p1.xlsx"), [0] * 9 + [1200000]))) == 1


def test_siyosat_versiyasi_kotarildi():
    assert d.SIYOSAT_VERSIYA >= "s2026-09-17"
