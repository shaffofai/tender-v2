# -*- coding: utf-8 -*-
"""Kiruvchi API — tender tizimi fayl ma'lumotini shu yerga yozadi.

    POST /api-v2/tender-v2/check     (Basic Auth)
    GET  /health
    GET  /api-v2/tender-v2/jurnal    (Basic Auth — O'Z kalitlari: JURNAL_*)

    auth.py    Basic Auth (vaqt bo'yicha barqaror solishtirish)
    intake.py  yukni tekis ro'yxatga keltirish va har elementni tekshirish
    store.py   bazaga yozish (bitta ulanish + qulf)
    models.py  javob shakllari — FAQAT hujjat uchun (/docs, DOCS_ENABLED=1)
    jurnal.py  so'rov jurnali: oraliq qatlam (har so'rovni yozadi) va o'qish
    app.py     FastAPI ilovasi: marshrutlar, lifespan, hisoblagichlar
"""
