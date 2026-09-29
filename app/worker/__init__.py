# -*- coding: utf-8 -*-
"""Tekshiruv worker'i — `files` dagi status=0 qatorlarni olib, havoladagi
Excel faylni yuklab, buyurtmachi etaloniga solishtirib, natijani yozadi.

    queue.py     navbat: kashfiyot, band qilish, backoff, kutish, o'lik qatorlar
    gates.py     darvozalar: aynan nusxa (Q1), mazmun-nusxa (S1b), ochilmadi (#2),
                 bo'sh hujjat (#7) — har biri bitta kichik funksiya
    audit.py     validation_evidence yozuvlari (verdikt dalili)
    verdict.py   verdikt + chiquvchi xabar (outbox) — bitta tranzaksiyada
    pipeline.py  `ishni_bajar` — bitta faylning butun yo'li
    loop.py      `python jobs_worker.py` sikli (`--once`)
    run.py       `python main.py` — ishlab chiqarish sikli (Docker)
    health.py    `--health` va yozish huquqlari tekshiruvi
    cli.py       `python jobs_worker.py` buyruqlari

Natija:
  status = 1  → hujjat shablonga mos to'ldirilgan
  status = 2  → kamchilik aniqlandi, `comment` ga qaysi kataklar bo'shligi yoziladi
  status = 3  → buyurtmachi shabloni bilan AYNAN bir xil (sha256) — Q1 darvozasi
  status = 0  → TEXNIK sabab (tarmoq, etalon topilmadi) — qayta uriniladi
  status = 4  → TEXNIK, TASLIM (urinishlar tugadi / doimiy xato) — rad EMAS

OLTIN QOIDA: texnik xato ASLO status=2 bermaydi.
"""
