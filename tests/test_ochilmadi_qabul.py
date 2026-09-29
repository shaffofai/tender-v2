# -*- coding: utf-8 -*-
"""#2 (2026-09-07): ochilmagan fayl — QABUL + «Hujjatni o'qib bo'lmadi.»

Buyurtmachi qarori: avval o'qishga urinilsin (tiklash zanjiri), baribir
ochilmasa — texnik nosozlik RAD sababi EMAS (OLTIN QOIDA). Ilgari 92 fayl
status=2 olib, ishtirokchi dasturchi xatosini o'qirdi.

Qotiriladigan shartlar:
  1. status = 1 (qabul), 2 emas;
  2. izoh AYNAN «Hujjatni o'qib bo'lmadi.» — xato matni ishtirokchiga
     ko'rinmaydi (u faqat findingning detail qismida, audit uchun);
  3. FILE_UNREADABLE kodi SAQLANADI — evidence'da «nima uchun qabul» izi;
  4. yordamchi hech qanday kirishda yiqilmaydi (xato None/uzun bo'lsa ham).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.worker import gates
from tender_engine.validate import STATUS_FILLED, STATUS_DEFECT


def test_status_qabul():
    res = gates._ochilmadi_res("f.xlsx", "resurs", "BadZipFile: File is not a zip file")
    assert res["status"] == STATUS_FILLED
    assert res["status"] != STATUS_DEFECT
    assert res["role"] == "resurs" and res["file"] == "f.xlsx"


def test_izoh_aynan_va_xato_matni_sizib_chiqmaydi():
    xato = "'Chartsheet' object has no attribute 'max_row'"
    res = gates._ochilmadi_res("f.xlsx", None, xato)
    assert res["comment_uz"] == gates.IZOH_OQILMADI == "Hujjatni o'qib bo'lmadi."
    assert "Chartsheet" not in res["comment_uz"]       # ishtirokchi ko'rmaydi
    assert "max_row" not in res["comment_uz"]


def test_kod_evidence_uchun_saqlanadi():
    res = gates._ochilmadi_res("f.xlsx", "narxlar", "xato")
    kodlar = [f["code"] for f in res["findings"]]
    assert kodlar == ["FILE_UNREADABLE"]
    # `note`/`technical` EMAS — aks holda evidence `kodlar` dan tushib qolardi
    assert res["findings"][0].get("severity") not in ("note", "technical")
    assert "xato" in res["findings"][0]["detail_uz"]    # audit tafsiloti bor


def test_har_qanday_xato_kirishida_yiqilmaydi():
    for xato in (None, "", "x" * 5000, ValueError("v")):
        res = gates._ochilmadi_res("f.xls", "jamlanma", xato)
        assert res["status"] == STATUS_FILLED
        assert len(res["findings"][0]["detail_uz"]) < 200
