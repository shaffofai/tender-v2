# -*- coding: utf-8 -*-
"""Butun tizim: API → worker → yuboruvchi, haqiqiy PostgreSQL da.

    E2E_ADMIN_URL=postgresql://postgres:postgres@127.0.0.1:5432/postgres pytest tests/e2e

`E2E_ADMIN_URL` — superuser (baza va rol yaratadi). Ssenariy o'zining
`tender_v2_e2e` bazasini yaratadi va oxirida o'chiradi; boshqa bazaga tegmaydi.

Har qadam xulqi (HTTP javob, chiqish kodi, jadvallar, sxema, huquqlar, yuklab
olishlar, yetkazilgan xabarlar) `kutilgan.json` dagi barmoq izi bilan
solishtiriladi. Xulq ATAYLAB o'zgarsa:

    E2E_YANGILA=1 E2E_ADMIN_URL=... pytest tests/e2e

va o'zgarishni commit'da tushuntiring. Farqni ko'rish uchun E2E_CHIQISH=fayl.json
— to'liq natija (matn bilan) shu faylga yoziladi.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ADMIN = os.environ.get("E2E_ADMIN_URL", "").strip()
KUTILGAN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kutilgan.json")

pytestmark = pytest.mark.skipif(not ADMIN, reason="E2E_ADMIN_URL berilmagan (PostgreSQL kerak)")


def test_butun_tizim():
    import ssenariy

    natija = ssenariy.yurgiz(ADMIN)
    chiqish = os.environ.get("E2E_CHIQISH")
    if chiqish:
        with open(chiqish, "w", encoding="utf-8") as fh:
            json.dump(natija, fh, ensure_ascii=False, indent=1)
    izlar = ssenariy.izlar(natija)
    if os.environ.get("E2E_YANGILA"):
        with open(KUTILGAN, "w", encoding="utf-8") as fh:
            json.dump({"izoh": "tests/e2e/ssenariy.py — har qadam xulqining sha256 izi",
                       "qadamlar": izlar}, fh, ensure_ascii=False, indent=1)
            fh.write("\n")
        pytest.skip("kutilgan.json yangilandi")

    with open(KUTILGAN, encoding="utf-8") as fh:
        kutilgan = json.load(fh)["qadamlar"]
    assert [n for n, _ in izlar] == [n for n, _ in kutilgan], "qadamlar ro'yxati o'zgargan"
    farq = [n for (n, a), (_, b) in zip(izlar, kutilgan) if a != b]
    assert not farq, ("xulq o'zgargan qadamlar:\n  " + "\n  ".join(farq)
                      + "\nTo'liq natija: E2E_CHIQISH=/tmp/natija.json bilan qayta yurgizing")
