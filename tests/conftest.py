# -*- coding: utf-8 -*-
"""Testlar uchun umumiy muhit.

`app.config` mahalliy `.env` ni o'qiydi — unda HAQIQIY `TENDER_API_URL` va
kalitlar bo'lishi mumkin. Buyurtmachi fayllari API si (worker, 2026-10-22)
standart bo'yicha shundan kelib chiqadi, ya'ni shablonsiz holatni sinagan
har test sherikning JONLI API siga so'rov yuborardi. Testlarda u O'CHIQ;
uni sinaydigan testlar (`test_buyurtmachi_api.py`) soxta mijoz bilan yoqadi.
"""
import os

os.environ.setdefault("BUYURTMACHI_API_URL", "0")
