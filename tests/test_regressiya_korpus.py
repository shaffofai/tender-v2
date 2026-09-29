# -*- coding: utf-8 -*-
"""REGRESSIYA DARVOZASI — A+B korpusi har `pytest` da avtomatik yuradi.

Buyurtmachi sharti (2026-09-07): yangi algoritm eski xatolarni tuzatsin,
LEKIN ilgari TO'G'RI ishlagan verdiktni teskarisiga o'zgartirmasin.

  B (saqlanishi shart) — bittasi ham o'zgarsa test YIQILADI. Bu qat'iy.
  A (tuzatilishi shart) — hisobot beriladi; hozircha yiqitmaydi, chunki
    tuzatishlar bosqichma-bosqich kiritiladi. Har bosqich tugagach
    `A_KUTILGAN_MIN` ko'tariladi va shu qiymatdan pastga tushish ham
    regressiya hisoblanadi.

Korpus yo'q bo'lsa (masalan CI da fayllar yuklanmagan) — test o'tkazib
yuboriladi, yiqilmaydi.

Tez rejim: `REGRESSIYA_TEZ=1 pytest tests/test_regressiya_korpus.py` — har
kesimdan 30 tadan (smoke). To'liq rejim standart.
"""
import json
import os
import random
import sys

import pytest

LOYIHA = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, LOYIHA)
sys.path.insert(0, os.path.join(LOYIHA, "korpus", "regressiya"))

KORPUS = os.path.join(LOYIHA, "korpus", "regressiya", "korpus.json")

# A dan kamida shuncha o'tishi SHART — har tuzatish bosqichidan keyin
# ko'tariladi (0 = hali bitta ham tuzatish kiritilmagan).
# Bosqichlar (2026-09-07, SODIQ yurgizgich bilan o'lchangan):
#   baseline (#1 requeue dan keyin) : 178/538  — TUZATILGAN 153 + OCHILMADI 23 + sintetik 2
#   #2 ochilmadi -> qabul+izoh      : 178/538  (sintetik 2/2 shu yerda)
#   #6 S1b mazmun-tenglik darvozasi : 197/538  — S1_QAYTA_SAQLANGAN 19/19
#   #7 ACCEPT yo'lida bo'sh-hujjat  : 305/538  — NOORIN_QABUL 108/185
#   #9 kichik shakl chegarasi        : 306/538  — NOORIN_RAD +1 (7 tasi PRICE_SPARSE
#                                      dispatch'i tufayli yetib bormaydi — #9b)
#   #11-b REVIEW yo'lida «nol = qiymat emas»: 322/529 — NOORIN_QABUL 133/185;
#        A2 dagi 9 ta «tuzatilgan» qator aslida bo'sh nusxa ekan (narxli 0,
#        hamma son etalonda bor) → odam ko'rigi (buyurtmachi qarori 2026-09-07)
#   #9b PRICE_SPARSE + band<5 → jiddiy_toldirilganmi so'raladi: 329/529 —
#        NOORIN_RAD 8/156 (#9 ning dispatch'ga yetmagan 7 tasi shu yerda)
#   #10 jamlanma/narxlar varaq tanlovi (2026-09-08): 332/529 — NOORIN_RAD 10/156,
#        NOORIN_QABUL 134/185 (o'lchov: 17633, 29149 + 1 NOORIN_QABUL — aynan)
#   #12-V3 varaqli maxraj, tegilgan varaqlar ko'pchilik bo'lsa (2026-09-08):
#        338/529 — NOORIN_RAD 16/156 (o'lchov: 6 fayl — aynan)
#   #14-son shakl-erkin: yangi narxsimon son >= 500 -> qabul (2026-09-10):
#        343/529 — NOORIN_RAD 21/156 (o'lchov: 5 fayl — aynan)
#   #14-h  `hukm` yo'lida ham (2026-09-10b): 358/529 — NOORIN_RAD +15.
#        Chegara 300 (`HUKM_KOP_SON`), SHEET_UNFILLED CHETLANADI. O'lchov
#        (`xom/q14h_olchov`): 300 da B regressiya 0 va A foyda 15 (eng past
#        xavfsiz chegara 275 edi — 300 ayni foydani beradi, zaxirasi ko'proq);
#        200 da B da 4 ta to'g'ri rad ochilardi. Istisnosiz 19239 (bitta varaq
#        to'ldirilib 8 tasi tegilmagan, 680 yangi son) ochilib ketardi.
# (+75 A odam ko'rigi: 19 kelishmovchilik + 47 matnsiz + 9 A2; +2 B odam ko'rigi:
#  83571/108998 faqat 0 yozilgan — hisobga kirmaydi.)
# Har bosqichda B hukmli = 4 598/4 598, regressiya 0. Pastga tushish = regressiya.
#   NOL SIYOSATI (2026-09-15, buyurtmachi: «0 narx dalili emas», «9 nol + 1 narx → RAD»):
#        `bosh_ustun_holati` va `narx_ustuni_toldirilganmi` nolni sanamaydi.
#        Darvoza 10 ta QABUL→RAD ko'rsatdi — bisect hammasini nolga bog'ladi
#        (1–2 narx + 7…3 903 nol). Yorliqlar eski «nol=qiymat» tushunchasida
#        edi → 10 qator odam ko'rigiga (`xom/nol_qayta_yorliqla.py`, zaxira
#        korpus.json.bak_nol): B 4 579 → 4 574, A hukmli 529 → 524.
#        A: −5 NOORIN_RAD_TUZATILGAN (odam ko'rigiga) +2 NOORIN_QABUL (nol
#        bilan o'tgan noo'rin qabullar endi rad) = 360/524. 133486 chegara
#        holati (61 narxsimon son, bitta nol ≥3 shiftini buzgan) — odam ko'rsin.
#   2026-09-17: NOL siyosati BEKOR (buyurtmachi: «0 bilan to'ldirsa ham qabul»),
#        10 yorliq tiklandi (xom/nol_yorliq_tikla.py): B 4 574 → 4 579, A 524 → 529.
#        Kutilgan: 09-14 holati (363/529, B 0) yoki undan yaxshi.
A_KUTILGAN_MIN = 363          # nol bekor (2026-09-17): 09-14 dagi 363/529 qaytdi, B 4 579/4 579
A_HUKMLI_JAMI = 529          # nisbat uchun maxraj (smoke rejimi)

# B da REGRESSIYA — 0 dan boshqa qiymat QABUL QILINMAYDI.
B_REGRESSIYA_MAX = 0


# ── Darvoza JIM O'CHMASIN (2026-09-14) ────────────────────────────────────
# Ilgari korpus topilmasa `pytest.skip` edi: fayllari yo'q mashinada butun
# regressiya darvozasi indamay o'chib qolardi va `pytest` YASHIL ko'rinardi.
# Endi bu XATO. Ataylab o'tkazib yuborish uchun KORPUS_SHART_EMAS=1.
KORPUS_SHART = os.environ.get("KORPUS_SHART_EMAS", "").strip().lower() \
    not in ("1", "true", "yes", "ha")

# Fayllari yo'qolgan qatorlar ulushi shundan oshsa — darvoza ishonchsiz.
# Qisman yo'qolish eng xavflisi: test yashil qoladi, lekin qamrov jimgina
# kamayadi (masalan B ning yarmi tekshirilmay qoladi).
KORPUS_MIN_ULUSH = 0.98


def _yoq(sabab):
    """Korpus ishlatib bo'lmaydi — `KORPUS_SHART` ga qarab xato yoki skip."""
    if KORPUS_SHART:
        pytest.fail(
            f"REGRESSIYA DARVOZASI O'CHIQ: {sabab}\n"
            f"  Korpus: {KORPUS}\n"
            f"  Tiklash: korpus/regressiya/qur.py\n"
            f"  Ataylab o'tkazib yuborish: KORPUS_SHART_EMAS=1 pytest ...")
    pytest.skip(sabab)


def _korpus():
    if not os.path.isfile(KORPUS):
        _yoq("korpus/regressiya/korpus.json topilmadi")
    k = json.load(open(KORPUS, encoding="utf-8"))
    kutilgan = [q for q in k["qatorlar"] if q.get("holat") == "OK"]
    qatorlar = [q for q in kutilgan
                if os.path.isfile(os.path.join(LOYIHA, "korpus", "regressiya",
                                               q["fayl"]))]
    if not qatorlar:
        _yoq(f"korpus bo'sh — {len(kutilgan)} qatordan bironta faylning "
             f"o'zi ham topilmadi")
    ulush = len(qatorlar) / max(1, len(kutilgan))
    if ulush < KORPUS_MIN_ULUSH:
        _yoq(f"korpus fayllarining {(1 - ulush) * 100:.1f}% i yo'qolgan "
             f"({len(qatorlar)}/{len(kutilgan)} topildi) — darvoza qamrovi "
             f"ishonchsiz")
    if os.environ.get("REGRESSIYA_TEZ"):
        rnd = random.Random(20260907)
        kesim = {}
        for q in qatorlar:
            kesim.setdefault((q["qism"], q.get("sabab") or q.get("kesim")), []).append(q)
        qatorlar = []
        for g in kesim.values():
            rnd.shuffle(g)
            qatorlar.extend(g[:30])
    return qatorlar


def _yurgiz(qatorlar):
    import yurgiz
    import multiprocessing as mp
    j = int(os.environ.get("REGRESSIYA_JARAYON", "8"))
    with mp.Pool(j) as pool:
        return list(pool.imap_unordered(yurgiz._bitta, qatorlar, 4))


@pytest.fixture(scope="module")
def natijalar():
    return _yurgiz(_korpus())


def test_B_regressiya_nol(natijalar):
    """SAQLANISHI SHART: eski to'g'ri verdikt o'zgarmasin — 0 ta istisno."""
    # `kutilgan is None` — odam ko'rigiga chiqarilgan B qatori (masalan
    # 83571/108998: faqat 0 yozilgan shubhali qabul) — regressiya EMAS.
    B = [n for n in natijalar if n["qism"] == "B" and n["holat"] == "OK"
         and n["kutilgan"] is not None]
    assert B, "B korpusi bo'sh"
    regress = [n for n in B if n["status"] != n["kutilgan"]]
    yomon = [n for n in regress if n["kutilgan"] == 1 and n["status"] == 2]
    xabar = "\n".join(
        f"  {n['id']}: {n['sabab']}  kutilgan={n['kutilgan']} -> {n['status']}  "
        f"[{n['qoida']}]" for n in regress[:20])
    assert len(regress) <= B_REGRESSIYA_MAX, (
        f"REGRESSIYA: B dan {len(regress)}/{len(B)} ta verdikt o'zgardi "
        f"(shundan QABUL->RAD: {len(yomon)}). TO'XTA.\n{xabar}")


def test_B_yurgizgich_xatosiz(natijalar):
    """Yurgizgich B da yiqilmasin (fayl o'qilmadi = korpus buzilgan)."""
    xato = [n for n in natijalar if n["qism"] == "B" and n["holat"] != "OK"]
    assert not xato, "\n".join(f"  {n['id']}: {n['holat']}" for n in xato[:10])


def test_A_kamida_kutilgancha_otdi(natijalar):
    """TUZATILISHI SHART: har bosqichdan keyin ko'tariladigan minimal chegara."""
    # `kutilgan is None` = ODAM KO'RIGI (hozirgi kod va mustaqil metrika
    # kelishmagan) — «to'g'ri» deb taxmin qilinmaydi, hisobga kirmaydi.
    A = [n for n in natijalar if n["qism"] == "A" and n["holat"] == "OK"
         and n["kutilgan"] is not None]
    otdi = [n for n in A if n["status"] == n["kutilgan"]]
    if os.environ.get("REGRESSIYA_TEZ"):
        # Smoke rejimida har sinfdan 30 ta — mutlaq son ma'nosiz, NISBAT
        # tekshiriladi (baseline 223/583 = 0.38; 3 foiz bag'rikenglik).
        nisbat = len(otdi) / max(1, len(A))
        asos = A_KUTILGAN_MIN / A_HUKMLI_JAMI
        assert nisbat >= asos - 0.03, (
            f"A (smoke): {len(otdi)}/{len(A)} = {nisbat:.2f}, baseline "
            f"{asos:.2f} — oldingi bosqich tuzatishi yo'qolgan")
    else:
        assert len(otdi) >= A_KUTILGAN_MIN, (
            f"A: {len(otdi)}/{len(A)} o'tdi, kamida {A_KUTILGAN_MIN} kutilgan edi — "
            f"oldingi bosqich tuzatishi yo'qolgan")
