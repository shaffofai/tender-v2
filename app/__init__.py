# -*- coding: utf-8 -*-
"""Tender-v2 xizmat qatlami: kiruvchi API, tekshiruv worker'i, chiquvchi
yuboruvchi va ularning umumiy qismlari (sozlama, baza, yuklab olish, log).

Tekshiruv MANTIG'I bu yerda EMAS — u `tender_engine/` da. Bu paket faylni
olib keladi, dvigatelga beradi va verdiktni saqlab, tender tizimiga yetkazadi.

    app/config.py      sozlamalar (yagona manba)
    app/log.py         loglash
    app/download.py    yuklab olish + host oq ro'yxati (SSRF himoyasi)
    app/templates.py   buyurtmachi shablonlari (`templates`) va etalon keshi
    app/db/            sxema (qat'iy), migratsiyalar, minimal huquqlar
    app/worker/        navbat, darvozalar, tekshiruv quvuri, sikllar, CLI
    app/api/           kiruvchi API (tender tizimi fayl ma'lumotini yozadi)
    app/sender/        chiquvchi yuboruvchi (verdiktlar tender tizimiga)
    app/tools/         operator vositalari (korish, kuzatuv, ishga_tushir)
"""

# `.env` HAR QANDAY sozlama o'qilishidan OLDIN yuklansin (dvigatel chegaralari ham).
from app import config  # noqa: F401,E402
