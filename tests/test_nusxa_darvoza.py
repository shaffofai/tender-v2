# -*- coding: utf-8 -*-
"""Q1 DARVOZASI (AYNAN_NUSXA) — bayt-tenglik hech qachon yanglishmasin.

Darvoza — tenglik o'lchovi, hukm emas: sha256 teng bo'lgandagina ishlaydi,
har qanday farqda (bitta bayt ham) jim chetlanadi. Bu testlar aynan shu
ikki tomonlama kafolatni qotiradi:
  - aynan nusxa O'TIB KETMASIN (status 3 + xesh audit izida);
  - farqli fayl darvozada USHLANMASIN (None — oddiy oqim davom etadi).

2026-09-18 (buyurtmachi qarori): aynan nusxa RAD (2) emas, ALOHIDA status 3,
izoh «Buyurtmachi fayli bilan aynan bir xil». To'liq sha256 findings
(`detail_uz`) da — evidence va jobs_validation_log ga tushadi.
"""
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.worker import gates
from tender_engine.validate import STATUS_DEFECT, STATUS_FILLED


def _yasa(tmp_path, nom, mazmun):
    yol = tmp_path / nom
    yol.write_bytes(mazmun)
    return str(yol)


def test_ayni_nusxa_status_3(tmp_path):
    et = _yasa(tmp_path, "etalon.xlsx", b"PK\x03\x04 shablon mazmuni " * 100)
    pt = str(tmp_path / "ishtirokchi.xlsx")
    shutil.copyfile(et, pt)

    res = gates._aynan_nusxa_res("ishtirokchi.xlsx", "resurs", pt, et)
    assert res is not None
    assert res["status"] == gates.STATUS_IDENTICAL == 3
    assert res["status"] not in (STATUS_DEFECT, STATUS_FILLED)
    assert res["findings"][0]["code"] == "AYNAN_NUSXA"
    assert res["comment_uz"] == "Buyurtmachi fayli bilan aynan bir xil"
    # TO'LIQ sha256 audit izida (findings) bo'lishi shart — bahssiz dalil
    assert gates._sha256_fayl(et) in res["findings"][0]["detail_uz"]
    assert res["role"] == "resurs"


def test_status_3_evidence_sinfi():
    """Audit izi: status 3 → IDENTICAL_COPY (TECHNICAL_ERROR emas), tashqi 3."""
    from tender_engine import evidence as ev
    assert ev.eski_natijadan_ichki(gates.STATUS_IDENTICAL, ["AYNAN_NUSXA"]) == ev.IDENTICAL_COPY
    assert ev.TASHQI[ev.IDENTICAL_COPY] == gates.STATUS_IDENTICAL


def test_bitta_bayt_farq_otkazadi(tmp_path):
    """Bitta bayt farq — nusxa EMAS; darvoza aralashmaydi (None)."""
    mazmun = bytearray(b"PK\x03\x04 shablon mazmuni " * 100)
    et = _yasa(tmp_path, "etalon.xlsx", bytes(mazmun))
    mazmun[500] ^= 0xFF
    pt = _yasa(tmp_path, "ishtirokchi.xlsx", bytes(mazmun))

    assert gates._aynan_nusxa_res("f.xlsx", "resurs", pt, et) is None


def test_hajm_farqi_otkazadi(tmp_path):
    et = _yasa(tmp_path, "etalon.xlsx", b"A" * 1000)
    pt = _yasa(tmp_path, "ishtirokchi.xlsx", b"A" * 1001)
    assert gates._aynan_nusxa_res("f.xlsx", None, pt, et) is None


def test_fayl_yoq_bolsa_jim(tmp_path):
    """O'qish xatosi texnik holat — darvoza jim chetlanadi, yiqilmaydi."""
    et = _yasa(tmp_path, "etalon.xlsx", b"bor")
    assert gates._aynan_nusxa_res("f.xlsx", None,
                               str(tmp_path / "yoq.xlsx"), et) is None


def test_rol_nomalum_bolsa_ham_ishlaydi(tmp_path):
    """Etalon roli aniqlanmagan (ETALON_UNPARSED) holatda ham bayt-tenglik
    fakti o'zgarmaydi — rol None bilan ham verdikt chiqishi kerak."""
    et = _yasa(tmp_path, "etalon.xlsx", b"notanish shakl " * 50)
    pt = str(tmp_path / "ishtirokchi.xlsx")
    shutil.copyfile(et, pt)

    res = gates._aynan_nusxa_res("f.xlsx", None, pt, et)
    assert res is not None and res["status"] == gates.STATUS_IDENTICAL
    assert res["role"] is None


def test_hukm_qatlami_aylantirmasin():
    """AYNAN_NUSXA `decision._REVIEW_STRUKTURA` ga KIRMASLIGI shart —
    aks holda «narx bor — qabul» qatlami nusxani qabulga aylantirardi.
    (Darvoza hukm qatlamiga yetmasdan qaytadi, bu ikkinchi himoya.)"""
    from tender_engine import decision
    assert "AYNAN_NUSXA" not in decision._REVIEW_STRUKTURA
