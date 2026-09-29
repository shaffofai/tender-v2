# -*- coding: utf-8 -*-
"""Chiquvchi yuboruvchi — `yuborish_navbati` (outbox) dagi xabarlarni tender
tizimiga yuboradi:  POST .../api/integration/ai/set-result  (Basic Auth)

    {"results": [{"file_id": ..., "status": 1|2|3|4, "comment": "..."}]}

    statuses.py   ichki status → tender statusi (xarita, OLTIN QOIDA)
    delivery.py   HTTP mijoz, yuk, bitta xabarni yuborish
    outbox.py     navbatni band qilish, natijani yozish (backoff), qayta ochish
    technical.py  o'lik fayllar uchun texnik xabar (H1/H2/H3 himoyalari)
    cycle.py      bitta aylanish
    cli.py        `python yuboruvchi.py` (--davomiy, --holat, --health, --qayta-och)

Muvaffaqiyat mezoni — FAQAT HTTP 200 (buyurtmachi tasdiqladi).
"""
