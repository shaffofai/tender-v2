# -*- coding: utf-8 -*-
"""Kiruvchi API — tender tizimi fayl ma'lumotini shu yerga yozadi.

    POST /api-v2/tender-v2/check     (Basic Auth)
    GET  /health

    auth.py    Basic Auth (vaqt bo'yicha barqaror solishtirish)
    intake.py  yukni tekis ro'yxatga keltirish va har elementni tekshirish
    store.py   bazaga yozish (bitta ulanish + qulf)
    models.py  javob shakllari — FAQAT hujjat uchun (/docs, DOCS_ENABLED=1)
    app.py     FastAPI ilovasi: marshrutlar, lifespan, hisoblagichlar
"""
