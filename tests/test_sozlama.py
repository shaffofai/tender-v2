# -*- coding: utf-8 -*-
"""`app/config.py` — sozlamalar ESKI kod bilan AYNAN bir xil o'qilsin.

Refaktor (2026-09-29) sozlamalarni bitta joyga yig'di. Har qiymatning
tahlili eski ifoda bilan bir xil bo'lishi SHART — masalan
`DOWNLOAD_VERIFY_TLS=False` (bosh harf bilan) TLS tekshiruvini O'CHIRMAYDI,
chunki eski kod faqat "0", "false", "no" ni tanirdi.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import config  # noqa: E402


@pytest.fixture(autouse=True)
def _toza(monkeypatch):
    for k in ("JOBS_TABLE", "TEMPLATES_TABLE", "JOBS_PK", "JOBS_GROUP_COL",
              "ETALON_DISK_FALLBACK", "DATABASE_URL", "DB_HOST", "DB_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    config.tozala()
    yield
    config.tozala()


def test_eski_mos_qiymatlar_qabul_qilinadi(monkeypatch):
    monkeypatch.setenv("JOBS_TABLE", "files")
    monkeypatch.setenv("TEMPLATES_TABLE", "templates")
    monkeypatch.setenv("JOBS_PK", "id")
    monkeypatch.setenv("JOBS_GROUP_COL", "file_id")
    monkeypatch.setenv("ETALON_DISK_FALLBACK", "0")
    config.baza()


@pytest.mark.parametrize("nom,qiymat", [
    ("JOBS_TABLE", "jobs"), ("JOBS_TABLE", "files; drop table files"),
    ("TEMPLATES_TABLE", "shablonlar"), ("JOBS_PK", "uuid"), ("JOBS_GROUP_COL", "bidder_id"),
    ("ETALON_DISK_FALLBACK", "1"), ("ETALON_DISK_FALLBACK", "yes"),
])
def test_olib_tashlangan_sozlama_jim_qolmaydi(monkeypatch, nom, qiymat):
    monkeypatch.setenv(nom, qiymat)
    with pytest.raises(config.SozlamaXatosi) as exc:
        config.baza()
    assert exc.value.code != 0 and nom in str(exc.value)


def test_dsn_bolaklardan(monkeypatch):
    monkeypatch.setenv("DB_HOST", "db.local")
    monkeypatch.setenv("DB_PASSWORD", "p")
    assert config.baza().database_url == "postgresql://postgres:p@db.local:5432/postgres"
    monkeypatch.setenv("DATABASE_URL", "postgresql://a:b@c/d")
    config.tozala()
    assert config.baza().database_url == "postgresql://a:b@c/d"


@pytest.mark.parametrize("qiymat,kutilgan", [
    ("1", True), ("0", False), ("false", False), ("no", False),
    ("False", True), ("NO", True), ("off", True),       # eski kod faqat 0/false/no ni tanirdi
])
def test_verify_tls_eski_tahlil(monkeypatch, qiymat, kutilgan):
    monkeypatch.setenv("DOWNLOAD_VERIFY_TLS", qiymat)
    assert config.yuklash().download_verify_tls is kutilgan


@pytest.mark.parametrize("qiymat,kutilgan", [
    ("1", True), (" 0 ", False), ("false", False), ("no", False), ("False", True),
])
def test_texnik_yuborish_eski_tahlil(monkeypatch, qiymat, kutilgan):
    monkeypatch.setenv("TEXNIK_YUBORISH", qiymat)
    assert config.yuboruvchi().texnik_yuborish is kutilgan


def test_max_urinish_3_bilan_qisiladi(monkeypatch):
    monkeypatch.setenv("YUBORISH_MAX_URINISH", "7")      # yn_urinish_chk (0..3)
    assert config.yuboruvchi().yuborish_max_urinish == 3


def test_noto_g_ri_son_aniq_xabar(monkeypatch):
    monkeypatch.setenv("MAX_ATTEMPTS", "besh")
    with pytest.raises(config.SozlamaXatosi) as exc:
        config.worker()
    assert "MAX_ATTEMPTS" in str(exc.value)


def test_ruxsat_hostlar_tahlili(monkeypatch):
    monkeypatch.setenv("RUXSAT_HOSTLAR", " ApiSiTender.mc.uz , ,127.0.0.1 ")
    assert config.yuklash().ruxsat_hostlar == ("apisitender.mc.uz", "127.0.0.1")


def test_etalon_kesh_yoli_eski_ifoda(monkeypatch):
    """Bo'sh qiymat — loyiha ildizining o'zi (eski `templates_db` ifodasi), `yol()` emas."""
    monkeypatch.setenv("ETALON_CACHE_DIR", "")
    assert config.shablon().etalon_cache_dir == os.path.normpath(config.ROOT)
    monkeypatch.setenv("ETALON_CACHE_DIR", "kesh")
    config.tozala()
    assert config.shablon().etalon_cache_dir == os.path.join(config.ROOT, "kesh")


def test_guruhlar_mustaqil(monkeypatch):
    """Yuboruvchining noto'g'ri sozlamasi worker va API guruhlarini yiqitmaydi."""
    monkeypatch.setenv("TENDER_TIMEOUT", "yarim")
    config.worker()
    config.api()
    with pytest.raises(config.SozlamaXatosi):
        config.yuboruvchi()
