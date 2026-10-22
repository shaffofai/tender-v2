# -*- coding: utf-8 -*-
"""Buyurtmachi fayllarini sherik API sidan so'rash (2026-10-22) — bazasiz.

  • URL `TENDER_API_URL` dan kelib chiqadi (`set-result` → `get-customer-files`),
    `BUYURTMACHI_API_URL=0` — o'chiq;
  • javob turlari: ma'lumot / bo'sh / 404 / 401 / tarmoq xatosi;
  • kelgan fayllar sherik POST qilganlar bilan bir yo'ldan (`intake` → `store`):
    havola to'liq, host oq ro'yxati, boshqa tender rad;
  • bitta tender intervalda bir marta so'raladi; 401 — o'zgarmas `error` satri;
  • pipeline: API shablon bersa fayl status 4 olmaydi, bermasa — oldingidek.
Haqiqiy bazadagi yo'l (fon qidiruvi, tiriltirish) — `test_shablon_topilmadi.py`.
"""
import os
import sys

import httpx
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import config, download as _w, templates as templates_db   # noqa: E402
from app.api import store                                            # noqa: E402
from app.worker import buyurtmachi, pipeline                         # noqa: E402

URL = "https://api.shaffofxarid.uz/api/integration/ai/get-customer-files"

#: Sherikning HAQIQIY javobi (2026-10-22, tender 296436), havolalar qisqartirilgan.
JAVOB = {"success": True, "message": "success", "data": [
    {"file_id": 2967839, "type": "excel1", "link": "296436/excel1/56/ce5f.xlsx", "tender_id": 296436},
    {"file_id": 2967840, "type": "excel2", "link": "296436/excel2/56/0157.xls", "tender_id": 296436},
    {"file_id": 2967841, "type": "excel3", "link": "296436/excel3/56/3d15.xlsx", "tender_id": 296436}]}


def _sozlama(**k):
    return config.BuyurtmachiApi(**{"url": URL, "login": "l", "parol": "p", "timeout": 5,
                                    "interval": 1800, "kun": 7, **k})


@pytest.fixture(autouse=True)
def muhit(monkeypatch):
    buyurtmachi._tozala()
    monkeypatch.setattr(config, "buyurtmachi_api", _sozlama)
    monkeypatch.setattr(_w, "FILE_BASE_URL", "https://apisitender.mc.uz/storage/")
    monkeypatch.setattr(_w, "RUXSAT_HOSTLAR", ("apisitender.mc.uz",))
    yield
    buyurtmachi._tozala()


def _mijoz(javob, sorovlar=None):
    """Soxta sherik: `javob(request)` → httpx.Response."""
    def h(req):
        if sorovlar is not None:
            sorovlar.append(req)
        return javob(req)
    buyurtmachi._Mijoz._c = httpx.Client(transport=httpx.MockTransport(h), auth=("l", "p"))


class _Conn:
    def __init__(self):
        self.amallar = []

    def commit(self):
        self.amallar.append("commit")

    def rollback(self):
        self.amallar.append("rollback")


def _loglar(monkeypatch):
    satrlar = []
    monkeypatch.setattr(buyurtmachi, "log", lambda msg, level="info", hodisa=None, qoshimcha=None:
                        satrlar.append((level, msg, hodisa, qoshimcha)))
    return satrlar


# ── sozlama ─────────────────────────────────────────────────────────────────

def test_url_set_result_dan_kelib_chiqadi(monkeypatch):
    monkeypatch.delenv("BUYURTMACHI_API_URL", raising=False)
    monkeypatch.setenv("TENDER_API_URL", "https://api.shaffofxarid.uz/api/integration/ai/set-result")
    assert config._buyurtmachi_url() == URL
    monkeypatch.setenv("BUYURTMACHI_API_URL", "0")
    assert config._buyurtmachi_url() == ""
    monkeypatch.setenv("BUYURTMACHI_API_URL", "https://boshqa.uz/files/")
    assert config._buyurtmachi_url() == "https://boshqa.uz/files"
    monkeypatch.delenv("BUYURTMACHI_API_URL")
    monkeypatch.setenv("TENDER_API_URL", "https://api.shaffofxarid.uz/boshqa-yol")
    assert config._buyurtmachi_url() == ""          # taxmin qilinmaydi


@pytest.mark.parametrize("sozlama", [
    {"url": ""},                                     # o'chiq
    {"url": "http://api.shaffofxarid.uz/x"},         # parol ochiq ketardi
    {"login": ""},
])
def test_ochiq_emas_holatlarda_sorov_yoq(monkeypatch, sozlama):
    monkeypatch.setattr(config, "buyurtmachi_api", lambda: _sozlama(**sozlama))
    sorovlar = []
    _mijoz(lambda r: httpx.Response(200, json=JAVOB), sorovlar)
    satrlar = _loglar(monkeypatch)
    assert buyurtmachi.shablonlarni_ol(_Conn(), 296436) == 0
    assert buyurtmachi.shablonlarni_ol(_Conn(), 296437) == 0
    assert buyurtmachi.supur(_Conn()) == 0
    assert sorovlar == []
    assert len(satrlar) == (0 if not sozlama.get("url", URL) else 1)   # bir marta aytiladi


# ── bitta so'rov ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("javob, kutilgan", [
    (httpx.Response(200, json=JAVOB), ("ok", 3)),
    (httpx.Response(200, json={"success": True, "data": []}), ("yoq", 0)),
    (httpx.Response(200, json={"success": True, "data": None}), ("yoq", 0)),
    (httpx.Response(404, json={"success": False}), ("yoq", 0)),
    (httpx.Response(401, json={"success": False, "message": "Unauthorized"}), ("xato", "HTTP 401")),
    (httpx.Response(500, text="oops"), ("xato", "HTTP 500")),
    (httpx.Response(200, json={"success": False, "message": "x"}), ("xato", "kutilmagan")),
    (httpx.Response(200, json={"success": True, "data": {"a": 1}}), ("xato", "kutilmagan")),
    (httpx.Response(200, text="<html>"), ("xato", "javob JSON emas")),
])
def test_sora_javob_turlari(javob, kutilgan):
    sorovlar = []
    _mijoz(lambda r: javob, sorovlar)
    holat, data = buyurtmachi.sora(296436)
    assert holat == kutilgan[0]
    if holat == "xato":
        assert data.startswith(kutilgan[1])
    else:
        assert len(data) == kutilgan[1]
    assert str(sorovlar[0].url) == f"{URL}/296436"
    assert sorovlar[0].headers["authorization"].startswith("Basic ")


def test_sora_tarmoq_xatosi():
    def yiqil(req):
        raise httpx.ConnectError("ulanib bo'lmadi", request=req)
    _mijoz(yiqil)
    holat, matn = buyurtmachi.sora(1)
    assert holat == "xato" and matn.startswith("ConnectError")


# ── yozish ──────────────────────────────────────────────────────────────────

def test_shablonlar_intake_va_store_orqali_yoziladi(monkeypatch):
    yomon = [{"file_id": 1, "type": "excel1", "link": "296999/excel1/5/a.xlsx", "tender_id": 296999},
             {"file_id": 2, "type": "excel1", "link": "https://evil.example/a.xlsx", "tender_id": 296436},
             {"file_id": 3, "type": "excel9", "link": "296436/excel9/5/a.xlsx", "tender_id": 296436},
             "satr"]
    _mijoz(lambda r: httpx.Response(200, json={**JAVOB, "data": JAVOB["data"] + yomon}))
    yozilgan, tiriltirish = [], []
    monkeypatch.setattr(store, "elementni_yoz",
                        lambda conn, el: yozilgan.append(el) or ("qabul_qilindi", len(yozilgan), None))
    monkeypatch.setattr(buyurtmachi, "shablon_kelganini_tekshir", lambda conn: tiriltirish.append(1) or 5)
    satrlar = _loglar(monkeypatch)
    conn = _Conn()

    assert buyurtmachi.shablonlarni_ol(conn, 296436) == 3
    assert [(e["file_id"], e["type"], e["role"], e["tender_id"]) for e in yozilgan] == [
        (2967839, "excel1", "consulting", 296436), (2967840, "excel2", "consulting", 296436),
        (2967841, "excel3", "consulting", 296436)]
    assert yozilgan[1]["link"] == "https://apisitender.mc.uz/storage/296436/excel2/56/0157.xls"
    assert tiriltirish == [1]                     # shu tenderning status 4 fayllari darhol
    assert conn.amallar[0] == "commit"            # HTTP dan oldin tranzaksiya yopiq
    ogoh = [s for s in satrlar if s[0] == "warning"]
    assert len(ogoh) == 1 and "4 ta element yozilmadi" in ogoh[0][1]
    assert "boshqa tender" in ogoh[0][1] and "host ruxsat etilmagan" in ogoh[0][1]
    olindi = [s for s in satrlar if s[2] == "shablon_olindi"]
    assert olindi and olindi[0][3] == {"tender_id": 296436, "turlar": ["excel1", "excel2", "excel3"]}


def test_takror_va_xato_yangi_emas(monkeypatch):
    _mijoz(lambda r: httpx.Response(200, json=JAVOB))
    javoblar = iter([("takror", 1, None), ("xato", None, "file_id ... files jadvalida"),
                     ("qabul_qilindi", 3, None)])
    monkeypatch.setattr(store, "elementni_yoz", lambda conn, el: next(javoblar))
    monkeypatch.setattr(buyurtmachi, "shablon_kelganini_tekshir", lambda conn: 0)
    _loglar(monkeypatch)
    assert buyurtmachi.shablonlarni_ol(_Conn(), 296436) == 1


def test_bir_tender_intervalda_bir_marta(monkeypatch):
    sorovlar = []
    _mijoz(lambda r: httpx.Response(200, json={"success": True, "data": []}), sorovlar)
    _loglar(monkeypatch)
    for _ in range(3):                            # excel1/2/3 birga, keyin qayta yuklash
        assert buyurtmachi.shablonlarni_ol(_Conn(), 296436) == 0
    assert buyurtmachi.shablonlarni_ol(_Conn(), 296449) == 0
    assert [str(r.url).rsplit("/", 1)[1] for r in sorovlar] == ["296436", "296449"]


def test_401_birinchi_satri_ozgarmas_error(monkeypatch):
    _mijoz(lambda r: httpx.Response(401, json={"success": False, "message": "Unauthorized"}))
    satrlar = _loglar(monkeypatch)
    buyurtmachi.shablonlarni_ol(_Conn(), 296436)
    buyurtmachi.shablonlarni_ol(_Conn(), 296449)
    assert [s[0] for s in satrlar] == ["error", "error"]
    birinchi = {s[1].splitlines()[0] for s in satrlar}
    assert len(birinchi) == 1 and "kalit rad etildi" in birinchi.pop()   # bot bitta xabar deb biladi


def test_kutilmagan_xato_yutiladi_baza_uzilishi_emas(monkeypatch):
    import psycopg
    _mijoz(lambda r: httpx.Response(200, json=JAVOB))
    _loglar(monkeypatch)

    def yiqil(conn, el):
        raise RuntimeError("kutilmagan")
    monkeypatch.setattr(store, "elementni_yoz", yiqil)
    conn = _Conn()
    assert buyurtmachi.shablonlarni_ol(conn, 296436) == 0
    assert conn.amallar[-1] == "rollback"

    def uzildi(conn, el):
        raise psycopg.OperationalError("server closed the connection")
    monkeypatch.setattr(store, "elementni_yoz", uzildi)
    buyurtmachi._H.soroldi.clear()                # interval to'sig'isiz qayta so'rasin
    with pytest.raises(psycopg.OperationalError):
        buyurtmachi.shablonlarni_ol(_Conn(), 296436)


# ── pipeline ────────────────────────────────────────────────────────────────

def _etalon_yoq_keyin_bor(monkeypatch, api_beradi):
    """1-qidiruv EtalonYoq; API `api_beradi` ta shablon yozsa — 2-qidiruv topadi."""
    holat = {"bor": False, "api": []}

    def etalon_ol(conn, tender_id, typ):
        if not holat["bor"]:
            raise templates_db.EtalonYoq("yo'q")
        return {"id": 9, "role": "narxlar", "path": "/tmp/e.xlsx", "file_id": 2967840}

    def shablonlarni_ol(conn, tender_id):
        holat["api"].append(tender_id)
        holat["bor"] = api_beradi > 0
        return api_beradi

    monkeypatch.setattr(templates_db, "etalon_ol", etalon_ol)
    monkeypatch.setattr(templates_db, "tekshirilgan_deb_belgila", lambda conn, i: False)
    monkeypatch.setattr(buyurtmachi, "shablonlarni_ol", shablonlarni_ol)
    yoq = []
    monkeypatch.setattr(pipeline, "shablon_topilmadi_yoz", lambda *a: yoq.append(a[1]["uuid"]) or True)
    monkeypatch.setattr(pipeline, "_texnik_iz", lambda *a, **k: None)
    monkeypatch.setattr(pipeline, "log", lambda *a, **k: None)
    return holat, yoq


def test_pipeline_api_shablon_bersa_status4_yoq(monkeypatch):
    holat, yoq = _etalon_yoq_keyin_bor(monkeypatch, api_beradi=3)
    ish = {"uuid": "77", "tender_id": 296436, "type": "excel2", "link": "x", "bidder_id": 1}
    assert pipeline._etalonni_top(None, ish, "tok", "/tmp/p.xlsx") == \
        ("narxlar", "/tmp/e.xlsx", "2967840", None)
    assert holat["api"] == [296436] and yoq == []


def test_pipeline_api_bermasa_oldingidek_status4(monkeypatch):
    holat, yoq = _etalon_yoq_keyin_bor(monkeypatch, api_beradi=0)
    ish = {"uuid": "78", "tender_id": 296436, "type": "excel3", "link": "x", "bidder_id": 1}
    assert pipeline._etalonni_top(None, ish, "tok", "/tmp/p.xlsx") is None
    assert holat["api"] == [296436] and yoq == ["78"]


def test_pipeline_notanish_type_uchun_api_sorlanmaydi(monkeypatch):
    holat, yoq = _etalon_yoq_keyin_bor(monkeypatch, api_beradi=3)
    monkeypatch.setattr(pipeline, "texnik_qoyib_yubor", lambda *a, **k: None)
    ish = {"uuid": "79", "tender_id": 296436, "type": "excel7", "link": "x", "bidder_id": 1}
    assert pipeline._etalonni_top(None, ish, "tok", "/tmp/p.xlsx") is None
    assert holat["api"] == [] and yoq == []
