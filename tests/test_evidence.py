# -*- coding: utf-8 -*-
"""Evidence qatlami invariantlari (P1) — baza KERAK EMAS (sof funksiyalar)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tender_engine import evidence as ev


def _yozuv(ichki, findings=None):
    return ev.yozuv_tayyorla(
        fayl_id=42, link="277662/excel2/1/a.xlsx", tender_id=277662,
        typ="excel2", fayl_hash="f" * 64, etalon_tpl_id=140,
        etalon_hash="e" * 64, rol="resurs", ichki_status=ichki,
        findings=findings)


def test_tashqi_mapping():
    """Ichki 5 status -> tashqi 0/1/2 (QAYTA_QURISH Q-C, 2026-08-11 qarori)."""
    assert _yozuv(ev.ACCEPT_FILLED)["tashqi_status"] == 1
    assert _yozuv(ev.REJECT_NO_PRICE)["tashqi_status"] == 2
    assert _yozuv(ev.STRUCTURE_VIOLATION)["tashqi_status"] == 2
    assert _yozuv(ev.TECHNICAL_ERROR)["tashqi_status"] == 0
    assert _yozuv(ev.REVIEW_AMBIGUOUS)["tashqi_status"] == 1
    # Q1 darvozasi (2026-09-18): aynan nusxa — alohida tashqi status 3
    assert _yozuv(ev.IDENTICAL_COPY)["tashqi_status"] == 3 == ev.STATUS_IDENTICAL


def test_review_izi_avtomatik():
    """REVIEW->1 IZSIZ bo'lishi mumkin emas — sinf='review' avtomatik."""
    assert _yozuv(ev.REVIEW_AMBIGUOUS)["sinf"] == "review"
    for boshqa in (ev.ACCEPT_FILLED, ev.REJECT_NO_PRICE,
                   ev.STRUCTURE_VIOLATION, ev.TECHNICAL_ERROR, ev.IDENTICAL_COPY):
        assert _yozuv(boshqa)["sinf"] is None


def test_notanish_status_xato():
    with pytest.raises(ValueError):
        _yozuv("REVIEW")     # to'liq nom emas — yozilmasin


def test_kod_note_ajratiladi():
    findings = [
        {"code": "PRICE_EMPTY", "severity": "error"},
        {"code": "COLUMN_ADDED", "severity": "note"},
        {"code": "ETALON_MISSING", "severity": "technical"},
        {"code": "SHEET_DELETED"},                    # severity yo'q — kod
    ]
    y = _yozuv(ev.REJECT_NO_PRICE, findings)
    assert y["kodlar"] == ["PRICE_EMPTY", "SHEET_DELETED"]
    assert y["notelar"] == ["COLUMN_ADDED"]


def test_eski_natijadan_ichki():
    """Eski dvigatel verdikti -> ichki status (soya davri mapping)."""
    assert ev.eski_natijadan_ichki(1, []) == ev.ACCEPT_FILLED
    assert ev.eski_natijadan_ichki(2, ["PRICE_EMPTY"]) == ev.REJECT_NO_PRICE
    assert ev.eski_natijadan_ichki(2, ["PRICE_SPARSE"]) == ev.REJECT_NO_PRICE
    assert ev.eski_natijadan_ichki(2, ["SHEET_DELETED"]) == ev.STRUCTURE_VIOLATION
    assert ev.eski_natijadan_ichki(2, ["COLUMN_COUNT"]) == ev.STRUCTURE_VIOLATION
    assert ev.eski_natijadan_ichki(
        2, ["PRICE_EMPTY", "SHEET_DELETED"]) == ev.STRUCTURE_VIOLATION
    assert ev.eski_natijadan_ichki(0, []) == ev.TECHNICAL_ERROR
    # Q1 darvozasi (2026-09-18): status 3 -> IDENTICAL_COPY (ilgari TECHNICAL_ERROR
    # bo'lib ketardi — audit izi noto'g'ri sinflanardi)
    assert ev.eski_natijadan_ichki(3, ["AYNAN_NUSXA"]) == ev.IDENTICAL_COPY
    assert ev.eski_natijadan_ichki(4, []) == ev.TECHNICAL_ERROR   # notanish — texnik


def _evidence_ddl():
    """Migratsiyaning validation_evidence bo'limi: (DDL, MIGRATSIYA) matni.

    Ilgari bu `evidence.DDL` / `evidence.MIGRATSIYA` edi; endi sxemaning
    yagona manbai — `app/db/migrations/0001_boshlangich_sxema.sql`.
    """
    ildiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    yol = os.path.join(ildiz, "app", "db", "migrations", "0001_boshlangich_sxema.sql")
    with open(yol, encoding="utf-8") as fh:
        matn = fh.read()
    bolim = matn.split("-- ─── 3) validation_evidence", 1)[1].split("\n", 1)[1]
    i = bolim.index("ALTER TABLE validation_evidence")
    return bolim[:i], bolim[i:]


def test_ddl_bizning_jadvalga_cheklangan():
    """DDL faqat validation_evidence ga tegadi — sherik jadvallariga EMAS."""
    import re
    ddl = re.sub(r"--[^\n]*", "", _evidence_ddl()[0]).lower()      # izohlar sanalmaydi
    for taqiq in ("alter table", " files", " templates", "drop ", "truncate"):
        assert taqiq not in ddl, taqiq
    assert "create table if not exists validation_evidence" in ddl
    # har CREATE faqat bizning obyektlarga
    for m in re.finditer(r"create\s+(?:table|index)[^(]*?(\S+)\s*(?:\(|on)", ddl):
        assert "validation_evidence" in m.group(0) or m.group(1).startswith("ve_")
    # CHECK ro'yxatlarida yangi holat va 3 bor (Q1 darvozasi, 2026-09-18)
    assert "'identical_copy'" in ddl
    assert re.search(r"tashqi_status\s+in\s*\(\s*0,\s*1,\s*2,\s*3\s*\)", ddl)


def test_migratsiya_faqat_check_va_faqat_bizning_jadval():
    """MIGRATSIYA: faqat validation_evidence, faqat CHECK cheklovlari (DROP CONSTRAINT
    IF EXISTS + ADD CONSTRAINT); jadval/ustun/ma'lumotga tegmaydi, sherik jadvallariga EMAS."""
    import re
    m = re.sub(r"--[^\n]*", "", _evidence_ddl()[1]).lower()
    for taqiq in (" files", " templates", "drop table", "drop column", "truncate",
                  "delete ", "update "):
        assert taqiq not in m, taqiq
    for satr in re.findall(r"alter\s+table\s+(\S+)", m):
        assert satr == "validation_evidence", satr
    assert m.count("drop constraint if exists") == 2
    assert m.count("add constraint") == 2
    assert "'identical_copy'" in m and re.search(r"in\s*\(\s*0,\s*1,\s*2,\s*3\s*\)", m)
    # DDL va MIGRATSIYA bir xil cheklov nomlarini ishlatadi — ikki marta yaratilmaydi
    ddl, migr = _evidence_ddl()
    for nom in ("validation_evidence_ichki_status_check", "validation_evidence_tashqi_status_check"):
        assert nom in ddl and nom in migr
