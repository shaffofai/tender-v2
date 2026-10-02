# -*- coding: utf-8 -*-
"""So'rov jurnali (`sorov_jurnali`) — bazasiz testlar.

BOSH QOIDA tekshiriladi: jurnal asosiy ishni HECH QACHON to'xtatmaydi,
kuttirmaydi va o'zgartirmaydi.

  • modulni import qilish oqim ham, ulanish ham ochmaydi;
  • navbat chegaralangan (qator soni VA hajm), ortig'i tashlanadi va sanaladi;
  • NUL / yolg'iz surrogat bitta qator tufayli to'plamni yiqitmaydi;
  • oraliq qatlam javobni baytma-bayt o'zgartirmaydi, 401 da tana yozilmaydi,
    ishlovchi yiqilsa 500 yoziladi, `/health` → 200 yozilmaydi;
  • signal ishlovchisi ICHIDAN log yozilsa jarayon osilmaydi;
  • tekshirilmagan so'rovlar oqimi daqiqalik chegaradan oshsa tashlanadi;
  • `/jurnal` o'z kalitlari bilan, xatolari `{"xato": ...}`.

Haqiqiy INSERT (ilova roli bilan) — `tests/e2e` da («jurnal rows» qadami).
Bu yerda hech narsa bazaga ULANMAYDI: yozuvchi oqim yurgizilmaydi, navbatning
o'zi «soxta qabul qiluvchi» bo'lib xizmat qiladi.
"""
import asyncio
import base64
import contextlib
import os
import re
import subprocess
import sys
import threading
import time

import httpx
import psycopg
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

ILDIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ILDIZ)

from app import config, jurnal                       # noqa: E402
from app.api import jurnal as jurnal_api            # noqa: E402
from app.api.jurnal import JurnalOraliq             # noqa: E402
from app.db import migrate, schema                   # noqa: E402
from app.log import log, setup_logging               # noqa: E402

LOGIN, PAROL = "sherik", "sherik-kaliti"
J_LOGIN, J_PAROL = "jurnalchi", "j" * 24


def basic(login, parol):
    return "Basic " + base64.b64encode(f"{login}:{parol}".encode()).decode()


TOGRI = basic(LOGIN, PAROL)


class _Mijoz:
    """ASGI ilovasini tarmoqsiz chaqiradi — `httpx.ASGITransport` ustidagi sinxron qobiq.

    Lifespan ISHLAMAYDI: jurnal oqimi yurmaydi, bazaga ulanish ochilmaydi.
    `istisno=False` — ishlovchi yiqilsa mijoz haqiqiy serverdagidek 500 oladi.
    """

    def __init__(self, ilova, istisno=False):
        self.ilova, self.istisno = ilova, istisno

    def request(self, method, url, **kw):
        async def yubor():
            transport = httpx.ASGITransport(app=self.ilova, raise_app_exceptions=self.istisno)
            async with httpx.AsyncClient(transport=transport,
                                         base_url="http://testserver") as mijoz:
                return await mijoz.request(method, url, **kw)
        return asyncio.run(yubor())

    def get(self, url, **kw):
        return self.request("GET", url, **kw)

    def post(self, url, **kw):
        return self.request("POST", url, **kw)


@pytest.fixture
def navbat(monkeypatch):
    """Jurnal «yoqilgan», lekin yozuvchi oqimSIZ: yozuvlar navbatda qoladi.

    Log ulagichi ham ulanadi (so'rov ichidagi `log()` satrlari uchun); test
    tugagach hammasi avvalgi holiga qaytadi.
    """
    for nom in ("JURNAL_YOQILGAN", "JURNAL_LOGIN", "JURNAL_PAROL"):
        monkeypatch.delenv(nom, raising=False)
    config.tozala()
    eski = dict(jurnal._HOLAT)
    jurnal._NAVBAT.clear()
    jurnal._HOLAT.update(faol=True, manba="api", bayt=0, tashlandi=0, chegaradan=0,
                         daqiqa=None, daqiqada=0, xabar="soz")
    ulagich = jurnal._LogUlagich()
    setup_logging().addHandler(ulagich)
    yield jurnal._NAVBAT
    for h in list(setup_logging().handlers):
        if isinstance(h, jurnal._LogUlagich):
            setup_logging().removeHandler(h)
    jurnal._NAVBAT.clear()
    jurnal._HOLAT.clear()
    jurnal._HOLAT.update(eski)
    config.tozala()


def _qatorlar(navbat, tur=None):
    return [q for q, _ in navbat if tur is None or q["tur"] == tur]


def _ishlat(kod, timeout=60):
    env = dict(os.environ)
    env.pop("DATABASE_URL", None)
    env.update(PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run([sys.executable, "-c", kod], cwd=ILDIZ, env=env,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout)


# ═══════════════════════════════════════════════════════════════════════════
# Import — hech qanday nojo'ya ta'sirsiz
# ═══════════════════════════════════════════════════════════════════════════

def test_import_oqim_ochmaydi():
    """Kirish nuqtalarini import qilish oqim yurgizmaydi.

    `setup_logging()` healthcheck buyruqlarida (har 60 s da yangi jarayon),
    `migrate` da va operator vositalarida ham ishlaydi — jurnal o'sha yerdan
    yoqilsa, har biri bazaga alohida ulanish ochardi.
    """
    p = _ishlat("import threading, api_server, main, jobs_worker, yuboruvchi; "
                "print(threading.active_count())")
    assert p.returncode == 0, p.stderr[-600:]
    assert p.stdout.strip() == "1"


def test_import_psycopg_ni_yuklamaydi():
    """psycopg faqat YOZISH paytida yuklanadi — `korish.py` / `ishga_tushir.py`
    «psycopg o'rnatilmagan» xabarini o'zlari aytishi uchun."""
    p = _ishlat("import sys; from app import jurnal; print('psycopg' in sys.modules)")
    assert p.returncode == 0, p.stderr[-600:]
    assert p.stdout.strip() == "False"


def test_boshla_faqat_uch_joyda():
    """`jurnal.boshla` — API lifespan, worker sikli, yuboruvchi sikli. Boshqa
    HECH QAYERDA (ayniqsa `app/log.py` da) emas."""
    topildi = []
    for papka, _, fayllar in os.walk(os.path.join(ILDIZ, "app")):
        for f in fayllar:
            if f.endswith(".py"):
                yol = os.path.join(papka, f)
                n = open(yol, encoding="utf-8").read().count("jurnal.boshla(")
                topildi += [os.path.relpath(yol, ILDIZ).replace(os.sep, "/")] * n
    assert sorted(topildi) == ["app/api/app.py", "app/sender/cli.py", "app/worker/run.py"]
    assert "jurnal" not in open(os.path.join(ILDIZ, "app", "log.py"), encoding="utf-8").read()


# ═══════════════════════════════════════════════════════════════════════════
# Navbat — chegara
# ═══════════════════════════════════════════════════════════════════════════

def test_navbat_qator_soni_bilan_chegaralangan(navbat):
    for i in range(jurnal.NAVBAT_MAX_QATOR + 25):
        jurnal.qoy({"tur": "log", "manba": "api", "xabar": str(i)})
    assert len(navbat) == jurnal.NAVBAT_MAX_QATOR
    assert jurnal._HOLAT["tashlandi"] == 25
    assert navbat[-1][0]["xabar"] == str(jurnal.NAVBAT_MAX_QATOR - 1)   # YANGISI tashlanadi


def test_navbat_hajm_bilan_chegaralangan(navbat):
    """2 000 qator × ikkita 64 KiB tana = 250 MB — qator soni xotirani chegaralamaydi."""
    tana = b"x" * 65536
    for _ in range(200):
        jurnal.qoy({"tur": "sorov", "manba": "api", "sorov_tanasi": tana, "javob_tanasi": tana})
    assert len(navbat) < 70
    assert sum(hajm for _, hajm in navbat) <= jurnal.NAVBAT_MAX_BAYT
    assert jurnal._HOLAT["tashlandi"] == 200 - len(navbat)


def test_navbat_boshagach_hisob_nolga_qaytadi(navbat):
    jurnal.qoy({"tur": "log", "manba": "api", "xabar": "a" * 1000})
    assert jurnal._HOLAT["bayt"] >= 1000
    jurnal._olindi(1)
    assert jurnal._HOLAT["bayt"] == 0 and not navbat


def test_yoqilmagan_jurnal_hech_narsa_olmaydi(navbat):
    jurnal._HOLAT["faol"] = False
    jurnal.qoy({"tur": "log", "manba": "api", "xabar": "x"})
    log("[sinov] jurnal o'chiq")
    assert not navbat


def test_tekshirilmagan_sorovlar_daqiqalik_chegara(navbat, monkeypatch):
    """Kalitsiz har kim yubora oladigan qatorlar (`sorov` / `kirish`,
    `tasdiqlangan` TRUE emas) — daqiqasiga 600 ta; ortig'i tashlanadi va
    sanaladi. Tekshirilgan so'rovlar va log satrlari CHEKLANMAYDI."""
    soat = [1000.0]
    monkeypatch.setattr(jurnal, "_soat", lambda: soat[0])
    assert jurnal.TEKSHIRILMAGAN_MAX == 600
    for i in range(350):                              # 350 ta 401 → 700 qator
        jurnal.qoy({"tur": "sorov", "manba": "api", "tasdiqlangan": False, "xabar": str(i)})
        jurnal.qoy({"tur": "kirish", "manba": "api", "tasdiqlangan": False, "xabar": str(i)})
    assert len(navbat) == 600 and jurnal._HOLAT["tashlandi"] == 100
    assert jurnal._HOLAT["chegaradan"] == 100
    assert navbat[-1][0]["xabar"] == "299"            # YANGISI tashlanadi

    # chegara to'lgan paytda ham: tekshirilgan so'rov, log va amal — yoziladi
    jurnal.qoy({"tur": "sorov", "manba": "api", "tasdiqlangan": True, "xabar": "sherik"})
    jurnal.qoy({"tur": "log", "manba": "api", "xabar": "log satri"})
    jurnal.qoy({"tur": "amal", "manba": "worker", "hodisa": "requeue"})
    assert [q["xabar"] for q, _ in list(navbat)[600:602]] == ["sherik", "log satri"]
    assert len(navbat) == 603 and jurnal._HOLAT["tashlandi"] == 100
    # `tasdiqlangan` yo'q (None) — tekshirilMAGAN
    jurnal.qoy({"tur": "sorov", "manba": "api", "xabar": "belgisiz"})
    assert len(navbat) == 603 and jurnal._HOLAT["tashlandi"] == 101

    soat[0] = 1019.9                                  # hali o'sha daqiqa (960..1020 s)
    jurnal.qoy({"tur": "kirish", "manba": "api", "tasdiqlangan": False})
    assert len(navbat) == 603
    soat[0] = 1020.0                                  # yangi daqiqa — hisob noldan
    for i in range(601):
        jurnal.qoy({"tur": "sorov", "manba": "api", "tasdiqlangan": False, "xabar": f"yangi {i}"})
    assert len(navbat) == 603 + 600 and jurnal._HOLAT["tashlandi"] == 103


def test_chegaradan_tashlanganlar_jurnalga_yoziladi(navbat, monkeypatch):
    """Chegara tufayli tashlanganlar o'sha `jurnal_tashlandi` qatorida —
    umumiy son ichida va alohida (`chegaradan`)."""
    monkeypatch.setattr(jurnal, "_soat", lambda: 0.0)
    for i in range(jurnal.TEKSHIRILMAGAN_MAX + 7):
        jurnal.qoy({"tur": "sorov", "manba": "api", "tasdiqlangan": False, "xabar": str(i)})
    jurnal._HOLAT["tashlandi"] += 2                   # yana 2 tasi — boshqa sabab bilan
    conn = _Ulanish()
    jurnal._boshat(conn)
    assert len(conn.yozildi) == 601 and not navbat
    oxirgi = conn.yozildi[-1]
    assert oxirgi["hodisa"] == "jurnal_tashlandi" and oxirgi["daraja"] == "warning"
    assert oxirgi["qoshimcha"].obj == {"tashlandi": 9, "chegaradan": 7}
    assert oxirgi["xabar"] == ("[jurnal] 9 ta yozuv tashlandi (navbat to'lgan yoki yozib "
                               "bo'lmagan; shundan 7 tasi — tekshirilmagan so'rovlar "
                               "chegarasi, daqiqasiga 600)")
    assert jurnal._HOLAT["tashlandi"] == 0 and jurnal._HOLAT["chegaradan"] == 0
    # faqat chegara tufayli tashlangan bo'lsa — sababi ham faqat o'sha
    for i in range(3):
        jurnal.qoy({"tur": "kirish", "manba": "api", "tasdiqlangan": False})
    jurnal.qoy({"tur": "log", "manba": "api", "xabar": "navbat bo'sh emas"})
    jurnal._boshat(conn)
    assert conn.yozildi[-1]["qoshimcha"].obj == {"tashlandi": 3, "chegaradan": 3}
    assert conn.yozildi[-1]["xabar"] == ("[jurnal] 3 ta yozuv tashlandi (tekshirilmagan "
                                         "so'rovlar chegarasi, daqiqasiga 600)")


def test_chegaradan_tashlanganlar_navbat_bosh_bolsa_ham_yoziladi(navbat, monkeypatch):
    """Bir tekis oqimda (masalan skaner, soniyasiga 150 ta) navbat har soniyada
    bo'shab turadi — chegara esa navbat BO'SH paytda tashlaydi. Soni keyingi
    yozuvni kutmasin: yozuvchi oqim uni o'zi yozadi — ishlab turganda ham,
    to'xtashdagi oxirgi bo'shatishda ham (aks holda chiqishda yo'qolardi)."""
    monkeypatch.setattr(jurnal, "_soat", lambda: 0.0)
    conn = _Ulanish()
    monkeypatch.setattr(psycopg, "connect", lambda *a, **k: conn)
    toxta = threading.Event()
    monkeypatch.setattr(jurnal, "_TOXTA", toxta)
    for i in range(jurnal.TEKSHIRILMAGAN_MAX):
        jurnal.qoy({"tur": "sorov", "manba": "api", "tasdiqlangan": False, "xabar": str(i)})
    jurnal._boshat(conn)                              # chegara to'ldi, navbat bo'shadi
    assert len(conn.yozildi) == 600 and not navbat and jurnal._HOLAT["tashlandi"] == 0

    # 1) oqim ishlab turibdi: navbat bo'sh, faqat tashlanganlar bor
    for i in range(5):
        jurnal.qoy({"tur": "kirish", "manba": "api", "tasdiqlangan": False})
    assert not navbat and jurnal._HOLAT["tashlandi"] == 5
    oqim = threading.Thread(target=jurnal._yozuvchi, daemon=True)
    oqim.start()
    try:
        muddat = time.monotonic() + 5
        while len(conn.yozildi) == 600 and time.monotonic() < muddat:
            time.sleep(0.05)
        ishlab_turib = list(conn.yozildi[600:])       # `toxta` o'rnatilishidan OLDIN
    finally:
        toxta.set()
        oqim.join(5)
    assert not oqim.is_alive()
    assert [q["hodisa"] for q in ishlab_turib] == ["jurnal_tashlandi"], "soni yozilmadi"
    assert ishlab_turib[0]["qoshimcha"].obj == {"tashlandi": 5, "chegaradan": 5}
    assert len(conn.yozildi) == 601

    # 2) to'xtash: oxirgi bo'shatish (`toxtat` → `_TOXTA`), navbat yana bo'sh
    for i in range(3):
        jurnal.qoy({"tur": "sorov", "manba": "api", "tasdiqlangan": False})
    assert not navbat and jurnal._HOLAT["tashlandi"] == 3
    jurnal._yozuvchi()                                # `_TOXTA` o'rnatilgan — bitta o'tish
    assert len(conn.yozildi) == 602
    assert conn.yozildi[-1]["qoshimcha"].obj == {"tashlandi": 3, "chegaradan": 3}
    assert conn.yozildi[-1]["xabar"] == ("[jurnal] 3 ta yozuv tashlandi (tekshirilmagan "
                                         "so'rovlar chegarasi, daqiqasiga 600)")
    assert jurnal._HOLAT["tashlandi"] == 0 and jurnal._HOLAT["chegaradan"] == 0


# ═══════════════════════════════════════════════════════════════════════════
# Tozalash: NUL, surrogat, qirqish, sirlar
# ═══════════════════════════════════════════════════════════════════════════

def test_nul_va_surrogat_olib_tashlanadi():
    """PostgreSQL `text` 0x00 ni, `jsonb` \\u0000 ni saqlamaydi — bitta shunday
    qator (masalan `GET /x%00y`) butun to'plamni yiqitmasin."""
    toza = jurnal.tozalangan({
        "tur": "sorov", "manba": "api", "yol": "/x\x00y", "login": "a\x00b",
        "user_agent": "\ud83d", "sorov_tanasi": b'{"a": "nul\x00"} \xff',
        "xabar": "x\x00\x00y", "sorov_sarlavhalari": {"ho\x00st": "h\x00"},
        "qoshimcha": {"sabab": "\x00", "ichki": [{"k": "v\x00"}], "son": 5, "bor": True}})
    assert toza["yol"] == "/xy" and toza["login"] == "ab" and toza["xabar"] == "xy"
    assert toza["user_agent"] == "?"
    assert toza["sorov_tanasi"] == '{"a": "nul"} �'
    assert toza["sorov_sarlavhalari"] == {"host": "h"}
    assert toza["qoshimcha"] == {"sabab": "", "ichki": [{"k": "v"}], "son": 5, "bor": True}
    assert set(toza) == set(jurnal.USTUNLAR) and toza["yaratildi"] is not None
    for q in toza.values():
        assert "\x00" not in repr(q)


def test_qirqish_chegaralari():
    uzun = "я" * 100_000
    toza = jurnal.tozalangan({
        "tur": "sorov", "manba": "api", "sorov_tanasi": uzun, "javob_tanasi": uzun.encode(),
        "xabar": uzun, "yol": uzun, "sorov_qatori": uzun, "user_agent": uzun, "login": uzun,
        "qoshimcha": {"sabab": uzun}, "sorov_sarlavhalari": {"host": uzun}})
    assert len(toza["sorov_tanasi"]) == len(toza["javob_tanasi"]) == jurnal.TANA_MAX == 65536
    assert len(toza["xabar"]) == jurnal.XABAR_MAX == 8192
    for ustun in ("yol", "sorov_qatori", "user_agent", "login"):
        assert len(toza[ustun]) == jurnal.QISQA_MAX == 2000, ustun
    assert len(toza["qoshimcha"]["sabab"]) == len(toza["sorov_sarlavhalari"]["host"]) == 2000
    assert toza["xabar"].endswith("…")
    assert jurnal.tozalangan({"tur": "log", "manba": "api", "xabar": "qisqa"})["xabar"] == "qisqa"


@pytest.mark.parametrize("matn,sir", [
    ("Authorization: Basic " + "QWxhZGRpbjpvcGVu" + "IHNlc2FtZQ==", "QWxhZGRpbjpvcGVu"),
    ("header basic " + "ZGVtbzpkZW1vZGVtbw==", "ZGVtbzpkZW1v"),
    ("so'rov Bearer " + "abc.def-ghi", "abc.def-ghi"),
    ("https://api.telegram.org/bot" + "123456789:" + "A" * 35 + "/sendMessage", "A" * 35),
    ("token " + "123456789:" + "B" * 35 + " bilan", "B" * 35),
    ("kalit sk-" + "proj-" + "c" * 20, "c" * 20),
    ("kalit AIza" + "D" * 35, "D" * 35),
    ("-----BEGIN RSA PRIVATE KEY-----\nMIIE" + "e" * 40 + "\n-----END RSA PRIVATE KEY-----", "e" * 40),
    ("postgresql://tender_ai:" + "f" * 12 + "@db:5432/tender_v2", "f" * 12),
    ("https://s3.example/a.xlsx?X-Amz-Signature=" + "g" * 16 + "&v=1", "g" * 16),
    ("https://x.example/a?token=" + "h" * 10 + "&page=2", "h" * 10),
    ("https://x.example/a?page=2&api_key=" + "i" * 10, "i" * 10),
    ('{"link":"https:\\/\\/x.example\\/a.xlsx?sig=' + "j" * 10 + '&v=1"}', "j" * 10),
    ('{"link":"https:\\/\\/u:' + "k" * 10 + '@x.example\\/a.xlsx"}', "k" * 10),
    # libpq ulanish satri — `safe_dsn()` bu shaklni yashirmaydi
    ("[api] baza ulanishi tayyor: host=db port=5432 dbname=t user=u password=" + "m" * 12,
     "m" * 12),
    ("host=db password = '" + "n" * 4 + " " + "n" * 4 + "\\'" + "n" * 3 + "' user=u", "n" * 3),
    ("dbname=t password='" + "o" * 5 + " yopilmagan", "o" * 5),
    ('JURNAL_PAROL="' + "p" * 4 + " " + "p" * 6 + '" qatori', "p" * 6),
    ("PGPASSWORD=" + "q" * 9 + " psql -h db", "q" * 9),
    ("user=u password=" + "r" * 4 + "&" + "s" * 6 + " dbname=t", "s" * 6),      # `&` li parol
    ("xato: pwd=" + "t" * 7 + ", passwd=" + "u" * 7, "u" * 7),
    ('{"dsn":"host=db parol=' + "v" * 8 + '","n":1}', "v" * 8),
    # NISBIY havola — sherik yuboradigan odatiy shakl (FILE_BASE_URL ga nisbatan)
    ("8638/excel2/9518/fayl.xls?token=" + "w" * 12 + "&expires=1790000000", "w" * 12),
    ('[{"link": "8638/excel2/9518/fayl.xls?v=1&signature=' + "x" * 12 + '", "file_id": 5}]',
     "x" * 12),
    ("[qabul qilinmadi] ... | element={'link': '8638/a.xls?X-Amz-Signature=" + "y" * 16 + "'}",
     "y" * 16),
    # bir matnda bir necha havola — sir IKKINCHISIDA (faqat birinchisi tozalanmasin)
    ('[{"link": "8638/a.xls?page=1"}, {"link": "8638/b.xls?token=' + "k" * 12 + '"}]', "k" * 12),
])
def test_sirlar_yashiriladi(matn, sir):
    """Qiymatlar ish vaqtida yig'iladi — repozitoriyda kalit ko'rinishidagi satr bo'lmasin."""
    toza = jurnal.sirsiz(matn)
    assert sir not in toza and ("[yashirildi]" in toza or "***@" in toza), toza


@pytest.mark.parametrize("matn", [
    "Basic Auth kerak",
    "login yoki parol noto'g'ri",
    "https://apisitender.mc.uz/storage/8638/excel2/9518/desk-calculation-file.xls",
    "http://127.0.0.1:18731/storage/jam_toldirilgan.xlsx?page=2&design=1",
    "[qabul qilinmadi] file_id: butun son > 0 bo'lishi kerak | element={'file_id': 0}",
    "user@example.uz ga yozing: https://x.example/a?email=user@example.uz",
    "  ✓ file_id=21 status=1",
    "8638/excel2/9518/fayl.xls?page=2&design=1",
    "savol? javob=1 (parol yo'q)",
    "KIRUVCHI_LOGIN/KIRUVCHI_PAROL sozlanmagan — hamma so'rov 401 bo'ladi",
    "jurnalni o'qish sozlanmagan: JURNAL_LOGIN / JURNAL_PAROL (parol kamida 24 belgi)",
    "parol=\nkeyingi satr",
])
def test_oddiy_matn_ozgarmaydi(matn):
    assert jurnal.sirsiz(matn) == matn


def test_parol_atrofidagi_matn_saqlanadi():
    """Faqat QIYMAT yashiriladi — ulanish satrining qolgani, so'rov qatorining
    keyingi parametri va JSON ning davomi joyida qoladi."""
    assert (jurnal.sirsiz("host=db port=5432 user=u password=" + "z" * 9 + " dbname=t")
            == "host=db port=5432 user=u password=[yashirildi] dbname=t")
    assert (jurnal.sirsiz("https://x.example/a?password=" + "z" * 9 + "&page=2")
            == "https://x.example/a?password=[yashirildi]&page=2")
    assert (jurnal.sirsiz('{"dsn":"user=u password=' + "z" * 9 + '","files":[{"file_id":7}]}')
            == '{"dsn":"user=u password=[yashirildi]","files":[{"file_id":7}]}')
    assert (jurnal.sirsiz("8638/a.xls?token=" + "z" * 9 + "&page=2 yuklanmadi")
            == "8638/a.xls?token=[yashirildi]&page=2 yuklanmadi")


def test_saqlash_yolida_sirlar_yashiriladi():
    """`sirsiz` SAQLASH YO'LIDA chaqiriladi: tana (bayt), log matni, JSON
    qiymatlari va kalitlari — hammasi (`tozalangan` → `_matn`). Yuqoridagi
    testlar `sirsiz` ning o'zini tekshiradi; u `_matn` dan olib tashlansa ham
    ular o'tardi."""
    token, parol, imzo, baza_paroli = "SIR1" * 4, "SIR2" * 3, "SIR3" * 4, "SIR4" * 3
    basic_token = "QWxhZGRp" + "bjpvcGVuU2VzYW1l"
    toza = jurnal.tozalangan({
        "tur": "sorov", "manba": "api",
        "yol": "/check/" + "sk-" + "SIR5" * 4,
        "user_agent": "mijoz Bearer " + "SIR6" * 4,
        "sorov_tanasi": ('[{"link": "8638/excel2/9518/fayl.xls?token=%s"}]' % token).encode(),
        "javob_tanasi": ('{"xato": "https://h/f.xls?signature=%s yuklanmadi"}' % imzo).encode(),
        "xabar": ("Authorization: Basic %s | baza: host=db user=u password=%s"
                  % (basic_token, baza_paroli)),
        "sorov_sarlavhalari": {"referer": "https://u:%s@h/sahifa" % parol},
        "qoshimcha": {"u": "https://a:%s@h/" % parol,
                      "ichki": [{"a?token=" + token: "x?token=" + token}]}})
    matn = repr(toza)
    for sir in (token, basic_token[:8], parol, imzo, baza_paroli, "SIR5", "SIR6"):
        assert sir not in matn, (sir, matn)
    assert toza["sorov_tanasi"] == '[{"link": "8638/excel2/9518/fayl.xls?token=[yashirildi]"}]'
    assert toza["xabar"] == ("Authorization: Basic [yashirildi] | "
                             "baza: host=db user=u password=[yashirildi]")
    assert toza["qoshimcha"]["u"] == "https://***@h/"


def test_har_havoladagi_sir_yashiriladi():
    """Sherikning odatiy so'rovida bir necha fayl — har birining O'Z havolasi
    (va rad etilganlar log satrida `element=%r` bilan ketma-ket). Faqat
    birinchi `?` bo'lagi emas, HAR biri tozalanadi — saqlash yo'lida; sirsiz
    parametr va havolalar orasidagi matn joyida qoladi."""
    t1, t2 = "SIR7" * 3, "SIR8" * 3
    tana = ('[{"file_id": 1, "link": "8638/excel2/9518/a.xls?page=1"}, '
            '{"file_id": 2, "link": "8638/excel2/9518/b.xls?token=%s"}, '
            '{"file_id": 3, "link": "8638/excel2/9518/c.xls?v=2&signature=%s"}]' % (t1, t2))
    xabar = ("[qabul qilinmadi] ... | element={'link': '8638/b.xls?token=%s'} "
             "| element={'link': '8638/c.xls?signature=%s'}" % (t1, t2))
    toza = jurnal.tozalangan({"tur": "sorov", "manba": "api", "sorov_tanasi": tana.encode(),
                              "xabar": xabar})
    assert toza["sorov_tanasi"] == (
        '[{"file_id": 1, "link": "8638/excel2/9518/a.xls?page=1"}, '
        '{"file_id": 2, "link": "8638/excel2/9518/b.xls?token=[yashirildi]"}, '
        '{"file_id": 3, "link": "8638/excel2/9518/c.xls?v=2&signature=[yashirildi]"}]')
    assert toza["xabar"] == ("[qabul qilinmadi] ... "
                             "| element={'link': '8638/b.xls?token=[yashirildi]'} "
                             "| element={'link': '8638/c.xls?signature=[yashirildi]'}")


def test_bearer_zich_json_tanasini_yutmaydi():
    """Zich JSON da bo'shliq yo'q — naqsh tokendan keyingi tanani o'chirib yubormasin."""
    toza = jurnal.sirsiz('{"auth":"Bearer ' + "abc.def-123" + '","files":[{"file_id":7}]}')
    assert toza == '{"auth":"Bearer [yashirildi]","files":[{"file_id":7}]}'


def test_sorov_qatori_nom_boyicha():
    assert (jurnal.sorov_qatori_sirsiz("a=1&parol=" + "z" * 8 + "&limit=5&Access_Token=qq")
            == "a=1&parol=[yashirildi]&limit=5&Access_Token=[yashirildi]")
    assert jurnal.sorov_qatori_sirsiz("tur=log&manba=api") == "tur=log&manba=api"
    toza = jurnal.tozalangan({"tur": "sorov", "manba": "api", "sorov_qatori": "secret=abc&x=1"})
    assert toza["sorov_qatori"] == "secret=[yashirildi]&x=1"


@pytest.mark.parametrize("bolak", [
    "?", "&", "=", "?a", "a=", "basic ", "bearer ", "/bot1", "1", "123456:", "sk-", "AIza",
    "://a:", "://a:b", "https://", "https:\\/\\/", "https://a/b?", "-----BEGIN ",
    "-----BEGIN PRIVATE KEY-----", "\\/", ":", "@", " ",
    "password=", "password='", 'password="', "password=\\", "parol = ", "pwd=&", "pwd=&a=",
    "password=a&", "passwd", "?token=", "?a=", "?a&", "'", '"', "\\",
])
def test_naqshlar_yomon_niyatli_matnda_ham_tez(bolak):
    """Yashirish naqshlari CHIZIQLI ishlasin. Bu matnni (yo'l, so'rov qatori)
    tekshirilmagan mijoz yuboradi; naqsh ishlayotganda GIL band — kvadratik
    naqsh bitta so'rov bilan API ni soniyalab to'xtatib qo'yardi
    (`?` × 70 000 — 13 s edi)."""
    matn = (bolak * 70_000)[:70_000]
    for nusxa in (matn, "https://a/b?" + matn, "basic" + matn, "-----BEGIN PRIVATE KEY-----" + matn,
                  "password=" + matn, "password='" + matn, "a.xls?token=" + matn):
        t0 = time.monotonic()
        jurnal.sirsiz(nusxa)
        jurnal.sorov_qatori_sirsiz(nusxa)
        jurnal.tozalangan({"tur": "sorov", "manba": "api", "yol": nusxa, "sorov_qatori": nusxa,
                           "sorov_tanasi": nusxa.encode(), "xabar": nusxa,
                           "qoshimcha": {"sabab": nusxa}})
        assert time.monotonic() - t0 < 1, (bolak, nusxa[:30])


def test_sorov_qatori_avval_qirqiladi():
    """So'rov qatori naqshlardan OLDIN qirqiladi — uzunligini mijoz belgilaydi."""
    toza = jurnal.tozalangan({"tur": "sorov", "manba": "api",
                              "sorov_qatori": "a=1&" * 100_000 + "parol=sir"})
    assert len(toza["sorov_qatori"]) == jurnal.QISQA_MAX and "sir" not in toza["sorov_qatori"]


def test_log_ulagichi(navbat):
    log("[sinov] %d foiz — formatlanmaydi")           # `log()` tayyor matn beradi
    log("[sinov] ogohlantirish", "warning")
    log("[sinov] xato", "error")
    q = _qatorlar(navbat, "log")
    assert [x["daraja"] for x in q] == ["info", "warning", "error"]
    assert q[0]["xabar"] == "[sinov] %d foiz — formatlanmaydi"
    assert all(x["hodisa"] == "log" and x["manba"] == "api" and x["sorov_id"] is None for x in q)
    assert all(x["yaratildi"].tzinfo is not None for x in q)
    assert abs((jurnal.hozir() - q[0]["yaratildi"]).total_seconds()) < 5


def test_log_ulagichi_hech_qachon_yiqilmaydi(navbat, monkeypatch):
    monkeypatch.setattr(jurnal, "qoy", lambda qator: 1 / 0)
    log("[sinov] ulagich yiqilsa ham log yozilaveradi")


# ═══════════════════════════════════════════════════════════════════════════
# Yozuvchi oqim — soxta ulanish bilan
# ═══════════════════════════════════════════════════════════════════════════

class _Ulanish:
    """`execute` ni yozib oladi; `yomon` qiymatli qator bor INSERT yiqiladi.

    `chaqiruv` — INSERT lar soni. Har INSERT yozish qulfi olingan tranzaksiya
    ICHIDA bo'lishi shart (`qulf_soni` — olingan qulflar): tranzaksiyasiz
    yoki qulfsiz INSERT — shu yerning o'zida xato.
    """

    def __init__(self, yomon=None, xato=ValueError):
        self.yomon, self.xato = yomon, xato
        self.yozildi, self.chaqiruv, self.commit_soni, self.rollback_soni = [], 0, 0, 0
        self.closed = False
        self.tranzaksiyada, self.qulflangan, self.qulf_soni, self.izlar = False, False, 0, []

    @contextlib.contextmanager
    def transaction(self):
        assert not self.tranzaksiyada
        self.tranzaksiyada = True
        self.izlar.append("BEGIN")
        try:
            yield
        except BaseException:
            self.izlar.append("ROLLBACK")
            raise
        else:
            self.izlar.append("COMMIT")
        finally:
            self.tranzaksiyada = self.qulflangan = False      # qulf tranzaksiya bilan bo'shaydi

    def execute(self, sql, prm=None):
        if prm is None:
            assert sql == jurnal._QULF and self.tranzaksiyada
            self.qulflangan = True
            self.qulf_soni += 1
            self.izlar.append("QULF")
            return
        assert self.tranzaksiyada and self.qulflangan, "INSERT — qulfsiz yoki tranzaksiyasiz"
        self.izlar.append("INSERT")
        self.chaqiruv += 1
        assert sql.startswith("INSERT INTO sorov_jurnali (yaratildi, tur, ")
        n = len(jurnal.USTUNLAR)
        assert len(prm) % n == 0 and sql.count("%s") == len(prm)
        qatorlar = [dict(zip(jurnal.USTUNLAR, prm[i:i + n])) for i in range(0, len(prm), n)]
        if self.yomon is not None and any(q["xabar"] == self.yomon for q in qatorlar):
            raise self.xato("yomon qator")
        self.yozildi += qatorlar

    def commit(self):
        self.commit_soni += 1

    def rollback(self):
        self.rollback_soni += 1


def test_toplam_bitta_insert_bilan_yoziladi(navbat):
    for i in range(450):
        jurnal.qoy({"tur": "log", "manba": "api", "xabar": str(i), "qoshimcha": {"i": i}})
    conn = _Ulanish()
    jurnal._boshat(conn)
    assert conn.chaqiruv == 3                         # 200 + 200 + 50
    assert conn.izlar == ["BEGIN", "QULF", "INSERT", "COMMIT"] * 3
    assert [q["xabar"] for q in conn.yozildi] == [str(i) for i in range(450)]
    assert not navbat and jurnal._HOLAT["bayt"] == 0
    from psycopg.types.json import Jsonb
    assert isinstance(conn.yozildi[0]["qoshimcha"], Jsonb)
    assert conn.yozildi[0]["sorov_sarlavhalari"] is None       # JSON `null` emas, SQL NULL


def test_yomon_qator_toplamni_yiqitmaydi(navbat, capsys):
    """Ma'lumot xatosi: to'plam bittalab yoziladi, faqat YOMON qator tashlanadi
    — va tashlangani jurnalning o'ziga yoziladi."""
    for i in range(10):
        jurnal.qoy({"tur": "log", "manba": "api", "xabar": str(i)})
    conn = _Ulanish(yomon="4")
    jurnal._boshat(conn)
    xabarlar = [q["xabar"] for q in conn.yozildi]
    assert xabarlar[:9] == ["0", "1", "2", "3", "5", "6", "7", "8", "9"]
    assert conn.yozildi[9]["hodisa"] == "jurnal_tashlandi" and "1 ta yozuv tashlandi" in xabarlar[9]
    assert conn.yozildi[9]["qoshimcha"].obj == {"tashlandi": 1}
    assert not navbat and jurnal._HOLAT["tashlandi"] == 0
    # yiqilgan INSERT ning tranzaksiyasi qaytariladi — qulf ushlanib qolmaydi
    assert conn.izlar[:4] == ["BEGIN", "QULF", "INSERT", "ROLLBACK"]
    assert conn.izlar.count("ROLLBACK") == 2 and conn.qulf_soni == conn.chaqiruv == 12
    err = capsys.readouterr().err
    assert err.count("[jurnal] yozuv tashlandi (ValueError)") == 1
    assert "yomon qator" not in err                   # faqat istisno TURI


@pytest.mark.parametrize("xato", [
    psycopg.OperationalError, psycopg.InterfaceError,
    # qotgan jarayonning sessiyasi 10 s da uziladi (`_QULF`) — InternalError
    # sinfidan, lekin qator yomon emas: navbatda qolishi kerak
    psycopg.errors.IdleInTransactionSessionTimeout,
])
def test_ulanish_uzilsa_yozuvlar_navbatda_qoladi(navbat, xato):
    for i in range(5):
        jurnal.qoy({"tur": "log", "manba": "api", "xabar": str(i)})
    with pytest.raises(xato):
        jurnal._boshat(_Ulanish(yomon="2", xato=xato))
    assert len(navbat) == 5 and jurnal._HOLAT["tashlandi"] == 0
    conn = _Ulanish()
    jurnal._boshat(conn)                              # qayta ulangach — hammasi, tartib bilan
    assert [q["xabar"] for q in conn.yozildi] == ["0", "1", "2", "3", "4"] and not navbat


def test_tashlanganlar_soni_yozilmasa_oqim_kutishni_oshiradi(navbat, monkeypatch):
    """Navbat bo'sh, lekin tashlanganlar sonining o'zi rad etiladi (INSERT
    huquqi olib qo'yilgan): oqim har soniyada qayta urinmaydi — kutish
    1 → 2 → 4 → 8 bo'lib oshadi, son esa yo'qolmaydi."""

    class _HuquqsizUlanish(_Ulanish):
        def execute(self, sql, prm=None):
            if prm is not None and "jurnal_tashlandi" in prm:
                raise psycopg.errors.InsufficientPrivilege("ruxsat yo'q")
            return super().execute(sql, prm)

    class _Toxta:
        def __init__(self, n):
            self.kutishlar, self.n = [], n

        def is_set(self):
            return len(self.kutishlar) >= self.n

        def wait(self, soniya):
            self.kutishlar.append(soniya)

    toxta = _Toxta(4)
    monkeypatch.setattr(jurnal, "_TOXTA", toxta)
    monkeypatch.setattr(psycopg, "connect", lambda *a, **k: _HuquqsizUlanish())
    jurnal._HOLAT["tashlandi"] = 3
    jurnal._yozuvchi()
    assert toxta.kutishlar == [1, 2, 4, 8]
    assert jurnal._HOLAT["tashlandi"] == 3 and not navbat


def test_yozish_qulfi():
    """Qulf INSERT bilan BIR tranzaksiyada (`pg_advisory_xact_lock`), kutishi
    chegaralangan va chegara INSERT ga o'tmaydi; kalit migratsiya qulfidan
    boshqa (aks holda yozuvchi oqim `migrate` ni kuttirardi).

    Ushlashi ham chegaralangan: qulfni olgan jarayon tranzaksiya o'rtasida
    qotsa (`docker pause`), PostgreSQL uning sessiyasini 10 s da uzadi — aks
    holda qolgan hamma jarayonlarning jurnali u tiklanguncha to'xtab turardi.
    Chegara qulfdan OLDIN o'rnatiladi va COMMIT gacha qaytarilmaydi."""
    assert jurnal._QULF == ("SET LOCAL idle_in_transaction_session_timeout = '10s'; "
                            "SET LOCAL lock_timeout = '5s'; "
                            "SELECT pg_advisory_xact_lock(740202610); "
                            "SET LOCAL lock_timeout = DEFAULT")
    assert jurnal.YOZISH_QULFI != migrate._QULF_KALITI
    assert "%" not in jurnal._QULF                    # parametrsiz — bitta so'rovda to'rt buyruq


def test_oqim_yurmasa_xizmat_toxtamaydi(navbat, monkeypatch, capsys):
    """`Thread.start` yiqilsa (`can't start new thread`) `boshla` istisno
    KO'TARMAYDI — u API lifespan, worker va yuboruvchi boshida chaqiriladi.
    Jurnal yoqilmaydi; `toxtat` (atexit) yurmagan oqimni `join` qilmaydi."""
    jurnal._HOLAT.update(faol=False, oqim=None)
    monkeypatch.setattr(jurnal.atexit, "register", lambda f: None)

    def yurmaydi(self):
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(threading.Thread, "start", yurmaydi)
    jurnal.boshla("api")                              # istisnosiz
    assert not jurnal.faol() and jurnal._HOLAT["oqim"] is None
    jurnal.qoy({"tur": "log", "manba": "api", "xabar": "x"})
    log("[sinov] jurnal yoqilmagan")
    assert not navbat
    jurnal.toxtat()                                   # istisnosiz
    jurnal.boshla("api")                              # yana urinadi — yana jim
    err = capsys.readouterr().err
    assert err.count("[jurnal] yozuvchi oqim yurmadi (RuntimeError) — jurnal yoqilmadi") == 1
    assert "can't start new thread" not in err        # faqat istisno TURI


def test_toxtat_belgilangan_vaqtdan_uzoq_kutmaydi(navbat, monkeypatch):
    """Baza yotganda ulanish 10 s gacha cho'ziladi — `docker stop` va SIGTERM
    bilan to'xtash shunga cho'zilmasin."""
    jurnal._HOLAT.update(faol=False, oqim=None)
    monkeypatch.setattr(jurnal, "_yozuvchi", lambda: time.sleep(5))
    monkeypatch.setattr(jurnal.atexit, "register", lambda f: None)
    jurnal.boshla("worker")
    assert jurnal.faol() and jurnal._HOLAT["manba"] == "worker"
    t0 = time.monotonic()
    jurnal.toxtat(0.3)
    assert time.monotonic() - t0 < 2
    assert not jurnal.faol() and jurnal._HOLAT["oqim"] is None
    jurnal.toxtat()                                   # ikkinchi chaqiruv — hech narsa


def test_jurnal_yoqilgan_0_bolsa_boshlanmaydi(navbat, monkeypatch):
    jurnal._HOLAT.update(faol=False, oqim=None)
    monkeypatch.setenv("JURNAL_YOQILGAN", "0")
    config.tozala()
    monkeypatch.setattr(jurnal, "_yozuvchi", lambda: pytest.fail("oqim yurgizildi"))
    jurnal.boshla("api")
    assert not jurnal.faol() and jurnal._HOLAT["oqim"] is None


def test_signal_ishlovchisi_ichidan_log_osilmaydi():
    """SIGTERM asosiy oqim navbatga qo'yayotgan PAYTDA keladi va ishlovchi
    o'zi log yozadi (`sender.cli._toxtatish` shunday qiladi).

    `queue.Queue` bilan bu joyda jarayon osilib qolardi: `put` qayta
    kirilmaydigan qulf ushlab turadi. `deque.append` da qulf yo'q.
    """
    p = _ishlat(r'''
import collections, faulthandler, os, signal, sys
from app import jurnal
from app.log import log, setup_logging

class SignalliNavbat(collections.deque):
    """`append` ICHIDA signal keladi — haqiqiy signal ikki baytkod orasida shunday tushadi."""
    otildi = False
    def append(self, qator):
        if not self.otildi:
            self.otildi = True
            os.kill(os.getpid(), signal.SIGTERM)
            for _ in range(1000):
                pass                                  # interpretator signalni shu yerda ko'radi
        super().append(qator)

jurnal._NAVBAT = SignalliNavbat()
jurnal._HOLAT.update(faol=True, manba="sender")
setup_logging().addHandler(jurnal._LogUlagich())
signal.signal(signal.SIGTERM, lambda signum, _f: log("[to'xtatish] signal %s" % signum))
faulthandler.dump_traceback_later(10, exit=True)     # 10 s dan keyin ham shu yerda — osilgan
log("  ✓ file_id=1 status=1")
print("NAVBAT:", [q["xabar"] for q, _ in jurnal._NAVBAT])
''')
    assert p.returncode == 0, p.stderr[-800:]
    assert "NAVBAT: [\"[to'xtatish] signal 15\", '  ✓ file_id=1 status=1']" in p.stdout


# ═══════════════════════════════════════════════════════════════════════════
# Operator amallari
# ═══════════════════════════════════════════════════════════════════════════

def test_amal_bitta_insert_va_commit(navbat):
    conn = _Ulanish()
    jurnal.amal_yoz(conn, "worker", "requeue", {"nishon": "all", "soni": 3})
    assert conn.chaqiruv == 1 and conn.commit_soni == 1 and conn.rollback_soni == 0
    # yozuvchi oqim bilan bir navbatda: o'sha qulf, o'sha tranzaksiyada
    assert conn.izlar == ["BEGIN", "QULF", "INSERT", "COMMIT"]
    q = conn.yozildi[0]
    assert (q["tur"], q["hodisa"], q["manba"], q["daraja"]) == ("amal", "requeue", "worker", "info")
    assert q["qoshimcha"].obj == {"nishon": "all", "soni": 3}
    assert not navbat                                 # navbat orqali EMAS — sinxron


def test_amal_xatosi_yutiladi(navbat):
    """Jurnal yozilmagani uchun operator buyrug'i yiqilmasin — faqat ogohlantirish."""
    class _Yiqiluvchi(_Ulanish):
        def execute(self, sql, prm=None):
            raise RuntimeError("permission denied for table sorov_jurnali")

        def rollback(self):
            super().rollback()
            raise RuntimeError("ulanish ham uzilgan")

    conn = _Yiqiluvchi()
    jurnal.amal_yoz(conn, "sender", "qayta_och", {"soni": 0})
    assert conn.commit_soni == 0 and conn.rollback_soni == 1
    (ogoh,) = _qatorlar(navbat, "log")
    assert ogoh["daraja"] == "warning"
    assert ogoh["xabar"] == "[jurnal] amal yozilmadi (qayta_och): RuntimeError"   # TURI xolos


def test_amal_jurnal_ochiq_bolmasa_yozilmaydi(navbat, monkeypatch):
    monkeypatch.setenv("JURNAL_YOQILGAN", "no")
    config.tozala()
    conn = _Ulanish()
    jurnal.amal_yoz(conn, "worker", "requeue", {})
    assert conn.chaqiruv == 0 and conn.commit_soni == 0


# ═══════════════════════════════════════════════════════════════════════════
# Oraliq qatlam — `app/api/app.py` dagi ishlovchilarning nusxasi ustida
# ═══════════════════════════════════════════════════════════════════════════

def _ilova(oraliq):
    """`/check` (auth ishlovchining ICHIDA) va `/health` ning kichik nusxasi."""
    ilova = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    if oraliq:
        ilova.add_middleware(JurnalOraliq)

    @ilova.get("/health")
    @ilova.get("/api-v2/tender-v2/health")
    def health(request: Request):
        if "yotgan" in request.query_params:
            return JSONResponse({"baza": "xato: ulanmadi"}, status_code=503)
        return {"xizmat": "api_server"}

    @ilova.post("/api-v2/tender-v2/check")
    @ilova.post("/check")
    async def check(request: Request):
        if request.headers.get("authorization") != TOGRI:
            return JSONResponse({"xato": "login yoki parol noto'g'ri"}, status_code=401,
                                headers={"WWW-Authenticate": 'Basic realm="tender"'})
        tana = await request.body()
        if tana == b"yiqil":
            raise RuntimeError("sir: ulanish satri")
        log("[sinov] ishlovchi ichidan")
        return {"jami": len(tana)}

    @ilova.get("/sinxron")
    def sinxron():
        log("[sinov] threadpool ichidan")
        return {"ok": True}

    @ilova.get("/oqim")
    def oqim():
        return StreamingResponse(iter([b"a" * 40000, b"b" * 40000, b"c" * 40000]),
                                 media_type="application/octet-stream")

    @ilova.get("/jurnal")
    @ilova.get("/api-v2/tender-v2/jurnal/{jid}")
    def jurnal_nusxa(request: Request):
        return jurnal_api._ruxsat(request) or {"qatorlar": ["maxfiy jurnal mazmuni"]}

    return ilova


@pytest.fixture
def mijoz(navbat):
    return _Mijoz(_ilova(True))


SOROVLAR = [
    dict(method="GET", url="/health"),
    dict(method="GET", url="/api-v2/tender-v2/health"),
    dict(method="GET", url="/health?yotgan=1"),
    dict(method="POST", url="/check", content=b"x" * 100_000),
    dict(method="POST", url="/check", content=b"[1, 2]", headers={"authorization": TOGRI}),
    dict(method="POST", url="/api-v2/tender-v2/check", content=b"y" * 200_000,
         headers={"authorization": TOGRI, "content-type": "application/json"}),
    dict(method="POST", url="/check", content=b"yiqil", headers={"authorization": TOGRI}),
    dict(method="GET", url="/check"),
    dict(method="GET", url="/yoq-yol?a=1"),
    dict(method="GET", url="/x%00y"),
    dict(method="GET", url="/sinxron"),
    dict(method="GET", url="/oqim"),
    dict(method="GET", url="/jurnal"),
]


def test_javoblar_baytma_bayt_bir_xil(navbat):
    """Oraliq qatlam bilan va usiz: holat kodi, sarlavhalar, tana — AYNAN bir xil."""
    bilan, usiz = _Mijoz(_ilova(True)), _Mijoz(_ilova(False))
    for sorov in SOROVLAR:
        a, b = bilan.request(**sorov), usiz.request(**sorov)
        assert (a.status_code, a.content, sorted(a.headers.items())) == \
               (b.status_code, b.content, sorted(b.headers.items())), sorov["url"]
    assert {a.status_code for a in (bilan.request(**s) for s in SOROVLAR)} == \
           {200, 401, 404, 405, 500, 503}


def test_401_da_tana_yozilmaydi(mijoz, navbat):
    """Ishlovchi 401 da tanani O'QIMAYDI — tekshirilmagan mijoz jurnalga
    megabaytlab yoza olmaydi."""
    r = mijoz.post("/check", content=b"x" * 600_000,
                   headers={"authorization": basic(LOGIN, "xato-parol"), "user-agent": "sinov/1"})
    assert r.status_code == 401
    sorov, kirish = _qatorlar(navbat)
    assert (sorov["tur"], sorov["hodisa"], sorov["holat_kodi"]) == ("sorov", "http", 401)
    assert sorov["sorov_tanasi"] is None and sorov["qoshimcha"]["sorov_bayt"] == 0
    assert sorov["javob_tanasi"] == b'{"xato":"login yoki parol noto\'g\'ri"}'
    assert sorov["daraja"] == "warning" and sorov["tasdiqlangan"] is False
    assert sorov["login"] == LOGIN and sorov["user_agent"] == "sinov/1"
    assert sorov["sorov_sarlavhalari"]["authorization"] == "Basic [yashirildi]"
    # 401 → yana bitta `kirish` qatori, sababi javobdan
    assert (kirish["tur"], kirish["hodisa"], kirish["holat_kodi"]) == ("kirish", "kirish_rad", 401)
    assert kirish["qoshimcha"] == {"sabab": "login yoki parol noto'g'ri"}
    assert kirish["sorov_id"] == sorov["sorov_id"] and kirish["yaratildi"] == sorov["yaratildi"]
    assert kirish["login"] == LOGIN and kirish["tasdiqlangan"] is False
    assert "xato-parol" not in repr(list(navbat))
    assert basic(LOGIN, "xato-parol")[6:] not in repr(list(navbat))


def test_ishlovchi_yiqilsa_500_yoziladi(mijoz, navbat):
    """500 javobini TASHQI qatlam beradi — oraliq qatlam uni ko'rmaydi, lekin yozadi."""
    r = mijoz.post("/check", content=b"yiqil", headers={"authorization": TOGRI})
    assert r.status_code == 500
    (sorov,) = _qatorlar(navbat, "sorov")
    assert sorov["holat_kodi"] == 500 and sorov["daraja"] == "error"
    assert sorov["qoshimcha"]["istisno"] == "RuntimeError"
    assert sorov["tasdiqlangan"] is False and sorov["sorov_tanasi"] == b"yiqil"
    assert "ulanish satri" not in repr(list(navbat))  # istisno TURI, xabari emas


def test_istisno_ozgarmasdan_kotariladi(navbat):
    mijoz = _Mijoz(_ilova(True), istisno=True)
    with pytest.raises(RuntimeError, match="sir: ulanish satri"):
        mijoz.post("/check", content=b"yiqil", headers={"authorization": TOGRI})
    assert len(_qatorlar(navbat, "sorov")) == 1


def test_health_200_yozilmaydi(mijoz, navbat):
    """Konteyner healthcheck'i har 30 s da — ikkala yo'l shakli ham yozilmaydi;
    sog'lom EMAS javob (503) yoziladi."""
    assert mijoz.get("/health").status_code == 200
    assert mijoz.get("/api-v2/tender-v2/health").status_code == 200
    assert not navbat
    assert mijoz.get("/health?yotgan=1").status_code == 503
    (sorov,) = _qatorlar(navbat)
    assert sorov["holat_kodi"] == 503 and sorov["daraja"] == "error"
    assert sorov["sorov_qatori"] == "yotgan=1"


def test_muvaffaqiyatli_sorov_qatori(mijoz, navbat):
    r = mijoz.post("/api-v2/tender-v2/check?manba=sinov", content=b'[{"file_id": 1}]',
                   headers={"authorization": TOGRI, "content-type": "application/json",
                            "x-forwarded-for": "203.0.113.7", "cookie": "sessiya=sir",
                            "referer": "https://x.example/sahifa?token=sir"})
    assert r.status_code == 200
    sorov = _qatorlar(navbat, "sorov")[0]
    assert (sorov["usul"], sorov["yol"], sorov["holat_kodi"]) == \
           ("POST", "/api-v2/tender-v2/check", 200)
    assert sorov["manba"] == "api" and sorov["daraja"] == "info"
    assert sorov["tasdiqlangan"] is True and sorov["login"] == LOGIN
    assert sorov["sorov_tanasi"] == b'[{"file_id": 1}]' and sorov["javob_tanasi"] == b'{"jami":16}'
    assert sorov["qoshimcha"] == {"sorov_bayt": 16, "javob_bayt": 11}
    assert sorov["davomiylik_ms"] >= 0 and sorov["yaratildi"].tzinfo is not None
    assert re.fullmatch(r"[0-9a-f]{32}", sorov["sorov_id"])
    # sarlavhalar — oq ro'yxat; Authorization dan faqat sxema
    h = sorov["sorov_sarlavhalari"]
    assert h["authorization"] == "Basic [yashirildi]" and h["x-forwarded-for"] == "203.0.113.7"
    assert h["referer"] == "https://x.example/sahifa" and h["content-type"] == "application/json"
    assert "cookie" not in h and "user-agent" not in h
    assert PAROL not in repr(list(navbat)) and TOGRI[6:] not in repr(list(navbat))


def test_log_satri_sorov_id_bilan(mijoz, navbat):
    """So'rov davomida yozilgan log satri o'sha so'rovning `sorov_id` sini oladi
    — hodisa siklida ham, threadpool'da (sinxron ishlovchi) ham."""
    mijoz.post("/check", content=b"[]", headers={"authorization": TOGRI})
    mijoz.get("/sinxron")
    log("[sinov] so'rovdan tashqarida")
    sorovlar, loglar = _qatorlar(navbat, "sorov"), _qatorlar(navbat, "log")
    assert [x["xabar"] for x in loglar] == ["[sinov] ishlovchi ichidan",
                                           "[sinov] threadpool ichidan",
                                           "[sinov] so'rovdan tashqarida"]
    assert loglar[0]["sorov_id"] == sorovlar[0]["sorov_id"]
    assert loglar[1]["sorov_id"] == sorovlar[1]["sorov_id"] != sorovlar[0]["sorov_id"]
    assert loglar[2]["sorov_id"] is None
    assert jurnal.JORIY_SOROV.get() is None


def test_tana_64_kib_gacha_saqlanadi(mijoz, navbat):
    mijoz.post("/check", content=b"y" * 200_000, headers={"authorization": TOGRI})
    mijoz.get("/oqim")
    katta, oqim = _qatorlar(navbat, "sorov")
    assert katta["sorov_tanasi"] == b"y" * 65536
    assert katta["qoshimcha"]["sorov_bayt"] == 200_000 and katta["qoshimcha"]["sorov_qirqildi"]
    assert oqim["javob_tanasi"] == b"a" * 40000 + b"b" * 25536
    assert oqim["qoshimcha"] == {"sorov_bayt": 0, "javob_bayt": 120_000, "javob_qirqildi": True}


@pytest.mark.parametrize("sarlavha,login,yozilgan", [
    (basic("golden", "x"), "golden", "Basic [yashirildi]"),
    ("basic " + basic("kichik", "x")[6:], "kichik", "basic [yashirildi]"),
    ("Basic " + base64.b64encode(b"YalangochKalit").decode(), None, "Basic [yashirildi]"),
    ("Basic %%%", None, "Basic [yashirildi]"),
    ("Bearer abc.def", None, "Bearer [yashirildi]"),
    ("YalangochKalitningOzi", None, "[yashirildi]"),
    ("Maxsus kalit-qiymat", None, "[yashirildi]"),
])
def test_login_faqat_ikki_nuqta_bolsa(mijoz, navbat, sarlavha, login, yozilgan):
    """`login` — DA'VO (`_auth_ok` dagi dekodlash); `:` siz qiymat yozilmaydi,
    Authorization ning o'zi esa hech qachon."""
    mijoz.get("/yoq-yol", headers={"authorization": sarlavha})
    (sorov,) = _qatorlar(navbat)
    assert sorov["holat_kodi"] == 404 and sorov["tasdiqlangan"] is False
    assert sorov["login"] == login
    assert sorov["sorov_sarlavhalari"]["authorization"] == yozilgan
    assert sarlavha.split(" ", 1)[-1] not in repr(sorov)


def test_takror_authorization_birinchisi_yoziladi(mijoz, navbat):
    """Ikkita `Authorization`: ishlovchi BIRINCHISINI tekshiradi
    (`request.headers.get`) — jurnalga ham o'shaning logini tushadi, ikkinchi
    (soxta) sarlavhaniki emas."""
    r = mijoz.post("/check", content=b"[]",
                   headers=[("authorization", TOGRI),
                            ("authorization", basic("soxta-admin", "x")),
                            ("x-forwarded-for", "203.0.113.7"),
                            ("x-forwarded-for", "198.51.100.9")])
    assert r.status_code == 200                       # birinchi sarlavha bilan kirildi
    (sorov,) = _qatorlar(navbat, "sorov")
    assert sorov["login"] == LOGIN and sorov["tasdiqlangan"] is True
    assert sorov["sorov_sarlavhalari"]["x-forwarded-for"] == "203.0.113.7"
    # teskari tartib: birinchisi soxta → 401, da'vo qilingan login ham o'sha
    navbat.clear()
    r = mijoz.post("/check", content=b"[]",
                   headers=[("authorization", basic("soxta-admin", "x")), ("authorization", TOGRI)])
    assert r.status_code == 401
    sorov, kirish = _qatorlar(navbat)
    assert sorov["login"] == kirish["login"] == "soxta-admin"
    assert sorov["tasdiqlangan"] is False and kirish["tasdiqlangan"] is False


def test_chegara_toliq_bolsa_ham_tekshirilgan_sorov_yoziladi(mijoz, navbat, monkeypatch):
    """Daqiqalik chegara tugagan: 401 ning ikkala qatori va 404 tashlanadi va
    sanaladi, javoblar esa o'zgarmaydi; to'g'ri kalitli so'rov va uning log
    satri avvalgidek yoziladi."""
    monkeypatch.setattr(jurnal, "_soat", lambda: 0.0)
    jurnal._HOLAT.update(daqiqa=0, daqiqada=jurnal.TEKSHIRILMAGAN_MAX)
    r = mijoz.post("/check", content=b"[]", headers={"authorization": basic(LOGIN, "xato")})
    assert r.status_code == 401 and r.json() == {"xato": "login yoki parol noto'g'ri"}
    assert mijoz.get("/yoq-yol").status_code == 404
    assert not navbat and jurnal._HOLAT["tashlandi"] == 3 == jurnal._HOLAT["chegaradan"]
    r = mijoz.post("/check", content=b"[1]", headers={"authorization": TOGRI})
    assert r.status_code == 200
    assert [(q["tur"], q.get("holat_kodi")) for q in _qatorlar(navbat)] == \
           [("log", None), ("sorov", 200)]
    assert _qatorlar(navbat, "sorov")[0]["tasdiqlangan"] is True
    assert _qatorlar(navbat, "sorov")[0]["sorov_tanasi"] == b"[1]"
    assert jurnal._HOLAT["tashlandi"] == 3


def test_jurnal_javobi_faqat_hajmi(mijoz, navbat, monkeypatch):
    """`/jurnal` javobi saqlanmaydi (aks holda har o'qish jurnalni o'z nusxasi
    bilan shishirardi); `tasdiqlangan` ni o'qish kodi belgilaydi."""
    assert mijoz.get("/jurnal").status_code == 503                    # sozlanmagan
    monkeypatch.setenv("JURNAL_LOGIN", J_LOGIN)
    monkeypatch.setenv("JURNAL_PAROL", J_PAROL)
    config.tozala()
    assert mijoz.get("/jurnal", headers={"authorization": TOGRI}).status_code == 401
    r = mijoz.get("/api-v2/tender-v2/jurnal/5", headers={"authorization": basic(J_LOGIN, J_PAROL)})
    assert r.status_code == 200
    s503, s401, kirish, s200 = _qatorlar(navbat)
    assert [q["javob_tanasi"] for q in (s503, s401, s200)] == [None, None, None]
    assert s200["qoshimcha"]["javob_bayt"] == len(r.content) > 0
    assert (s503["tasdiqlangan"], s401["tasdiqlangan"], s200["tasdiqlangan"]) == (False, False, True)
    assert kirish["tur"] == "kirish" and kirish["qoshimcha"] == {"sabab": "login yoki parol noto'g'ri"}
    assert "maxfiy jurnal mazmuni" not in repr(list(navbat))


def test_jurnal_ochiq_bolmasa_sorov_togridan_togri_otadi(navbat):
    jurnal._HOLAT["faol"] = False
    mijoz = _Mijoz(_ilova(True))
    assert mijoz.post("/check", content=b"[]", headers={"authorization": TOGRI}).status_code == 200
    assert mijoz.post("/check", content=b"yiqil", headers={"authorization": TOGRI}).status_code == 500
    assert not navbat


def test_qayd_xatosi_sorovni_buzmaydi(mijoz, navbat, monkeypatch):
    """Jurnal kodidagi xato so'rovni 500 ga aylantirmasin."""
    monkeypatch.setattr(jurnal_api._Qayd, "qatorlar", lambda self: 1 / 0)
    r = mijoz.post("/check", content=b"[]", headers={"authorization": TOGRI})
    assert r.status_code == 200 and r.json() == {"jami": 2}
    monkeypatch.setattr(jurnal_api, "_Qayd", lambda scope: 1 / 0)
    r = mijoz.post("/check", content=b"[]", headers={"authorization": TOGRI})
    assert r.status_code == 200 and r.json() == {"jami": 2}
    assert not _qatorlar(navbat, "sorov")


# ═══════════════════════════════════════════════════════════════════════════
# Haqiqiy ilova: `/jurnal` marshrutlari (bazasiz yo'llar)
# ═══════════════════════════════════════════════════════════════════════════
# DIQQAT: bu yerda `/health` va to'g'ri kalitli `/check` CHAQIRILMAYDI — ular
# bazaga ulanadi. `_oqi` (yagona baza chaqiruvi) har testda almashtiriladi.

@pytest.fixture
def api(navbat, monkeypatch):
    from app.api.app import app
    monkeypatch.setenv("JURNAL_LOGIN", J_LOGIN)
    monkeypatch.setenv("JURNAL_PAROL", J_PAROL)
    config.tozala()
    monkeypatch.setattr(jurnal_api, "_oqi", lambda sql, prm: pytest.fail("bazaga murojaat"))
    return _Mijoz(app)                                # lifespan'siz — ulanish yo'q


J_AUTH = {"authorization": basic(J_LOGIN, J_PAROL)}
YOLLAR = ["/jurnal", "/api-v2/tender-v2/jurnal", "/jurnal/7", "/api-v2/tender-v2/jurnal/7"]


def test_marshrutlar_ikki_shaklda():
    from app.api.app import app
    yollar = {r.path: r for r in app.routes if "jurnal" in getattr(r, "path", "")}
    assert sorted(yollar) == sorted(["/jurnal", "/jurnal/{jid}", "/api-v2/tender-v2/jurnal",
                                     "/api-v2/tender-v2/jurnal/{jid}"])
    # kesilgan shakllar hujjatda ko'rinmaydi (`/check` kabi)
    assert {y: r.include_in_schema for y, r in yollar.items()} == {
        "/jurnal": False, "/jurnal/{jid}": False,
        "/api-v2/tender-v2/jurnal": True, "/api-v2/tender-v2/jurnal/{jid}": True}
    assert all(r.methods == {"GET"} for r in yollar.values())


@pytest.mark.parametrize("yol", YOLLAR)
def test_sozlanmagan_bolsa_503(api, monkeypatch, yol):
    monkeypatch.delenv("JURNAL_PAROL")
    config.tozala()
    r = api.get(yol, headers=J_AUTH)
    assert r.status_code == 503 and "sozlanmagan" in r.json()["xato"]
    assert "www-authenticate" not in r.headers


def test_qisqa_parol_yoq_deb_sanaladi(api, monkeypatch):
    """Yo'l internetdan ochiq va urinishlar cheklanmaydi — 24 belgidan qisqa
    parol bilan jurnal umuman OCHILMAYDI (to'g'ri parol bilan ham)."""
    monkeypatch.setenv("JURNAL_PAROL", "p" * 23)
    config.tozala()
    assert api.get("/jurnal", headers={"authorization": basic(J_LOGIN, "p" * 23)}).status_code == 503
    monkeypatch.setenv("JURNAL_LOGIN", "")
    monkeypatch.setenv("JURNAL_PAROL", J_PAROL)
    config.tozala()
    assert api.get("/jurnal", headers={"authorization": basic("", J_PAROL)}).status_code == 503


@pytest.mark.parametrize("sarlavha,sabab", [
    (None, "Basic Auth kerak"),
    ("Bearer abc", "Basic Auth kerak"),
    ("Basic %%%", "Authorization sarlavhasi buzuq"),
    (basic(J_LOGIN, "noto'g'ri"), "login yoki parol noto'g'ri"),
    (basic("boshqa", J_PAROL), "login yoki parol noto'g'ri"),
    (basic(J_LOGIN, J_PAROL + "x"), "login yoki parol noto'g'ri"),
    (basic("жур", "нал"), "login yoki parol noto'g'ri"),          # ASCII emas → 500 EMAS
    ("Basic " + base64.b64encode(b"\xff\xfe:\xfd").decode(), "login yoki parol noto'g'ri"),
    (basic(J_LOGIN + ":" + J_PAROL, ""), "login yoki parol noto'g'ri"),
])
def test_kalitsiz_401(api, navbat, sarlavha, sabab):
    for yol in YOLLAR:
        r = api.get(yol, headers={"authorization": sarlavha} if sarlavha else {})
        assert r.status_code == 401 and r.json() == {"xato": sabab}, yol
        assert r.headers["www-authenticate"] == 'Basic realm="tender-jurnal"'
    # har rad etilgan urinish — `kirish` qatori
    kirish = _qatorlar(navbat, "kirish")
    assert len(kirish) == len(YOLLAR) and {k["qoshimcha"]["sabab"] for k in kirish} == {sabab}


def test_sherik_kalitlari_jurnalni_ochmaydi(api, monkeypatch):
    """KIRUVCHI_* — sherikniki; u o'z so'rovlari jurnalini (va boshqalarnikini) o'qimasin."""
    from app.api import auth
    monkeypatch.setattr(auth, "KIRUVCHI_LOGIN", LOGIN)
    monkeypatch.setattr(auth, "KIRUVCHI_PAROL", PAROL)
    assert api.get("/jurnal", headers={"authorization": TOGRI}).status_code == 401
    # ... va aksincha: jurnal kalitlari `/check` ni ochmaydi
    r = api.post("/check", json=[], headers=J_AUTH)
    assert r.status_code == 401 and r.headers["www-authenticate"] == 'Basic realm="tender"'


@pytest.mark.parametrize("sorov,xato", [
    ("limit=0", "limit: 1..500"), ("limit=501", "limit: 1..500"), ("limit=-1", "limit:"),
    ("limit=1.5", "limit:"), ("limit=", "limit:"), ("limit=%D9%A1", "limit:"),
    ("oldin_id=abc", "oldin_id:"), ("keyin_id=1e3", "keyin_id:"),
    ("oldin_id=5&keyin_id=1", "oldin_id va keyin_id birga berilmaydi"),
    ("holat_kodi=4xx", "holat_kodi:"), ("dan=kecha", "dan: ISO 8601"),
    ("gacha=2026-13-01", "gacha: ISO 8601"), ("tur=request", "tur: kutilgan qiymatlar"),
    ("manba=gateway", "manba: kutilgan qiymatlar"), ("daraja=INFO", "daraja: kutilgan qiymatlar"),
    ("yol=%00", "yol: noto'g'ri belgi"), ("from=2026-01-01", "noma'lum parametr: from"),
    ("sorov_id=a%00", "sorov_id: noto'g'ri belgi"),
    # `gacha` dan 24 soat oldingi sana `datetime` ga sig'maydi — 500 (OverflowError) emas
    ("gacha=0001-01-01", "gacha: sana juda erta"),
    ("gacha=0001-01-01T10:00:00%2B05:00", "gacha: sana juda erta"),
])
def test_notogri_filtr_400(api, sorov, xato):
    """FastAPI ning 422 `{"detail": [...]}` i emas — boshqa hamma xato kabi `{"xato": ...}`."""
    for yol in ("/jurnal", "/api-v2/tender-v2/jurnal"):
        r = api.get(f"{yol}?{sorov}", headers=J_AUTH)
        assert r.status_code == 400 and list(r.json()) == ["xato"], r.text
        assert r.json()["xato"].startswith(xato), r.text


@pytest.mark.parametrize("jid", ["abc", "-1", "1.5", "12x", "%D9%A1", "9" * 19])
def test_notogri_id_404(api, jid):
    r = api.get(f"/jurnal/{jid}", headers=J_AUTH)
    assert r.status_code == 404 and r.json() == {"xato": "yozuv topilmadi"}


def _vaqt(soniya):
    import datetime
    return datetime.datetime(2026, 9, 30, 15, 0, soniya,
                             tzinfo=datetime.timezone(datetime.timedelta(hours=5)))


def test_royxat_korish_rejimi(api, monkeypatch):
    chaqiruv = []

    def oqi(sql, prm):
        chaqiruv.append((sql, prm))
        return [(9, _vaqt(2), "sorov", "http", "info", "api", "a" * 32, "POST", "/check", None,
                 200, 12, "10.0.0.1", "sherik", True, None, 120, 80, {"sorov_bayt": 120}),
                (8, _vaqt(1), "log", "log", "error", "api", None, None, None, None,
                 None, None, None, None, None, "[api] baza ulanmadi", None, None, None)]

    monkeypatch.setattr(jurnal_api, "_oqi", oqi)
    r = api.get("/jurnal?limit=2&tur=sorov&manba=api&daraja=info&holat_kodi=200"
                "&login=sherik&yol=/api_v2%25&oldin_id=50&gacha=2026-09-30T12:00:00Z", headers=J_AUTH)
    assert r.status_code == 200, r.text
    d = r.json()
    assert [q["id"] for q in d["qatorlar"]] == [9, 8] and d["keyingi_oldin_id"] == 8
    assert d["qatorlar"][0]["yaratildi"] == "2026-09-30T10:00:02+00:00"          # har doim UTC
    assert d["qatorlar"][0]["sorov_tanasi_uzunligi"] == 120
    assert d["qatorlar"][0]["qoshimcha"] == {"sorov_bayt": 120}
    assert "sorov_tanasi" not in d["qatorlar"][0] and "javob_tanasi" not in d["qatorlar"][0]
    assert d["gacha"] == "2026-09-30T12:00:00+00:00" and d["dan"] == "2026-09-29T12:00:00+00:00"
    sql, prm = chaqiruv[0]
    assert "sorov_tanasi," not in sql and "length(sorov_tanasi)" in sql and "left(xabar, 500)" in sql
    assert sql.endswith(
        "WHERE yaratildi >= %s AND yaratildi < %s AND tur = %s AND manba = %s AND daraja = %s "
        "AND holat_kodi = %s AND login = %s AND yol LIKE %s AND id < %s ORDER BY id DESC LIMIT %s")
    assert prm[2:] == ["sorov", "api", "info", 200, "sherik", "/api\\_v2\\%%", 50, 2]


def test_royxat_standart_oxirgi_24_soat(api, monkeypatch):
    chaqiruv = []
    monkeypatch.setattr(jurnal_api, "_oqi", lambda sql, prm: chaqiruv.append((sql, prm)) or [])
    d = api.get("/api-v2/tender-v2/jurnal", headers=J_AUTH).json()
    assert d["qatorlar"] == [] and d["gacha"] is None and d["keyingi_oldin_id"] is None
    sql, prm = chaqiruv[0]
    assert sql.endswith("WHERE yaratildi >= %s ORDER BY id DESC LIMIT %s") and prm[1] == 100
    assert 86395 < (jurnal.hozir() - prm[0]).total_seconds() < 86405


def test_royxat_kuzatish_rejimi(api, monkeypatch):
    """`keyin_id` — `id` o'sish tartibida, vaqt oralig'isiz; ufq (eng katta
    `id`) sahifa bilan BITTA so'rovda o'qiladi va sahifani chegaralaydi."""
    chaqiruv = []

    def oqi(sql, prm):
        chaqiruv.append((sql, prm))
        return [(57,) + (None,) * 19]                # mos qator yo'q: ufq va NULL lar

    monkeypatch.setattr(jurnal_api, "_oqi", oqi)
    d = api.get("/jurnal?keyin_id=41&tur=kirish", headers=J_AUTH).json()
    assert d == {"qatorlar": [], "keyingi_keyin_id": 57}
    assert len(chaqiruv) == 1                        # alohida `max(id)` so'rovi yo'q
    sql, prm = chaqiruv[0]
    assert sql == ("SELECT ufq.id, sahifa.* FROM (SELECT coalesce(max(id), 0) AS id "
                   "FROM sorov_jurnali) ufq LEFT JOIN LATERAL (SELECT "
                   + jurnal_api._ROYXAT_SQL + " FROM sorov_jurnali WHERE tur = %s AND id > %s "
                   "AND id <= ufq.id ORDER BY id ASC LIMIT %s) sahifa ON true ORDER BY sahifa.id")
    assert prm == ["kirish", 41, 100]


def _kuzatish_qatori(ufq, jid):
    """Kuzatish so'rovining bitta natija qatori: ufq + `_ROYXAT` ning 19 ustuni."""
    return (ufq, jid, _vaqt(0), "kirish") + (None,) * 16


BOSH = (None,) * 19                                  # LEFT JOIN: mos qator yo'q


@pytest.mark.parametrize("limit,natija,kutilgan", [
    # filtr hech narsa topmadi — kursor ufqqa siljiydi. Avval 41 da qolardi va
    # har chaqiruv jadvalni o'sha `id` dan qayta ko'rardi (jadval o'sgan sari
    # uzoqroq — `statement_timeout` → 503 gacha).
    (100, [(900,) + BOSH], 900),
    # sahifa to'la emas — ufqqacha hammasi ko'rildi
    (3, [_kuzatish_qatori(900, 50), _kuzatish_qatori(900, 60)], 900),
    # sahifa TO'LA — ufqqacha yana mos qator bo'lishi mumkin: oxirgisidan davom
    (2, [_kuzatish_qatori(900, 50), _kuzatish_qatori(900, 60)], 60),
    # ufq `keyin_id` dan kichik (jadval tozalangan / tiklangan) — orqaga EMAS
    (100, [(0,) + BOSH], 41),
    (100, [(7,) + BOSH], 41),
])
def test_kuzatish_kursori_ufqqa_siljiydi(api, monkeypatch, limit, natija, kutilgan):
    monkeypatch.setattr(jurnal_api, "_oqi", lambda sql, prm: natija)
    r = api.get(f"/jurnal?keyin_id=41&tur=kirish&limit={limit}", headers=J_AUTH)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["keyingi_keyin_id"] == kutilgan
    # ufq ustuni qatorlarga tushmaydi: har qator — aynan `_ROYXAT`
    assert [q["id"] for q in d["qatorlar"]] == [q[1] for q in natija if q[1] is not None]
    assert all(list(q) == list(jurnal_api._ROYXAT) and q["tur"] == "kirish"
               and q["yaratildi"] == "2026-09-30T10:00:00+00:00" for q in d["qatorlar"])


def test_royxat_sorov_id_boyicha(api, monkeypatch):
    """`sorov_id` — aniq moslik: bitta so'rovning `sorov`, `kirish` va `log`
    qatorlari birga olinadi (ko'rish va kuzatish rejimlarida)."""
    chaqiruv = []
    monkeypatch.setattr(jurnal_api, "_oqi", lambda sql, prm: chaqiruv.append((sql, prm)) or [])
    sid = "0123456789abcdef" * 2
    r = api.get(f"/jurnal?sorov_id={sid}&login=sherik", headers=J_AUTH)
    assert r.status_code == 200, r.text
    sql, prm = chaqiruv[0]
    assert sql.endswith("WHERE yaratildi >= %s AND login = %s AND sorov_id = %s "
                        "ORDER BY id DESC LIMIT %s")
    assert prm[1:] == ["sherik", sid, 100]
    # LIKE emas: `%` va `_` oddiy belgi, qiymat o'zgarmasdan beriladi
    api.get("/jurnal?sorov_id=a%25_&keyin_id=5", headers=J_AUTH)
    sql, prm = chaqiruv[1]
    assert " WHERE sorov_id = %s AND id > %s AND id <= ufq.id ORDER BY id ASC " in sql
    assert "LIKE" not in sql and prm == ["a%_", 5, 100]


def test_bitta_yozuv_toliq(api, monkeypatch):
    chaqiruv = []

    def oqi(sql, prm):
        chaqiruv.append((sql, prm))
        return [] if prm == (404,) else [(7, _vaqt(0), "sorov") + (None,) * 14 + ('{"a": 1}', None, None, None)]

    monkeypatch.setattr(jurnal_api, "_oqi", oqi)
    r = api.get("/api-v2/tender-v2/jurnal/7", headers=J_AUTH)
    assert r.status_code == 200 and list(r.json()) == ["id"] + list(jurnal.USTUNLAR)
    assert r.json()["sorov_tanasi"] == '{"a": 1}' and r.json()["yaratildi"] == "2026-09-30T10:00:00+00:00"
    assert chaqiruv[0][1] == (7,) and chaqiruv[0][0].endswith("FROM sorov_jurnali WHERE id = %s")
    r = api.get("/jurnal/404", headers=J_AUTH)
    assert r.status_code == 404 and r.json() == {"xato": "yozuv topilmadi"}


def test_baza_yotgan_bolsa_503(api, navbat, monkeypatch):
    def oqi(sql, prm):
        raise psycopg.OperationalError("connection to server at 10.0.0.5 failed: parol=sir")

    monkeypatch.setattr(jurnal_api, "_oqi", oqi)
    for yol in ("/jurnal", "/jurnal/7"):
        r = api.get(yol, headers=J_AUTH)
        assert r.status_code == 503 and r.json() == {"xato": "baza mavjud emas"}
    assert [q["xabar"] for q in _qatorlar(navbat, "log")] == \
           ["[jurnal] o'qib bo'lmadi: OperationalError"] * 2           # TURI; xabari emas
    assert "parol=sir" not in repr(list(navbat))


def test_oqish_yagona_ulanishga_tegmaydi():
    """O'qish `ULANISH` va uning qulfidan foydalanmaydi — aks holda jurnal
    so'rovi `/check` va `/health` ni to'xtatib qo'yardi."""
    matn = open(os.path.join(ILDIZ, "app", "api", "jurnal.py"), encoding="utf-8").read()
    kod = "\n".join(s for s in matn.split('"""')[::2])          # docstring'larsiz
    kod = "\n".join(s.split("#")[0] for s in kod.splitlines())  # izohlarsiz
    assert "ULANISH" not in kod and "qulf" not in kod and "app.api.store" not in kod
    assert "connect_timeout=10" in kod and "SET statement_timeout = '5s'" in kod
    import inspect
    from app.api import app as api_app
    assert not inspect.iscoroutinefunction(api_app.jurnal_royxati)
    assert not inspect.iscoroutinefunction(api_app.jurnal_yozuvi)


# ═══════════════════════════════════════════════════════════════════════════
# Sxema: migratsiya 0002 va huquqlar
# ═══════════════════════════════════════════════════════════════════════════

def test_migratsiya_0002_va_kerakli_versiya():
    royxat = migrate.migratsiyalar()
    assert [(v, nom) for v, nom, _ in royxat] == [(1, "boshlangich_sxema"), (2, "sorov_jurnali")]
    assert schema.KERAKLI_VERSIYA == len(royxat) == 2
    sql = open(royxat[1][2], encoding="utf-8").read()
    kod = "\n".join(s.split("--")[0] for s in sql.splitlines())
    for ustun in jurnal.USTUNLAR:
        assert re.search(rf"^\s+{ustun}\s", kod, re.M), f"0002 da ustun yo'q: {ustun}"
    assert re.search(r"yaratildi\s+timestamptz\s+NOT NULL\s+DEFAULT now\(\)", kod)
    assert "sj_yaratildi_idx" in kod and "sj_tur_idx" in kod
    # jurnal yozuvi hech qachon rad etilmasin; egasi eskilarini o'chira olsin
    assert "CHECK" not in kod.upper() and "TRIGGER" not in kod.upper()
    assert "%" not in sql                              # matn psycopg ga parametrsiz beriladi


def test_huquqlar_sql_jingalak_qavssiz():
    """Fayl `sql.SQL(matn).format(rol=..., baza=...)` dan o'tadi: izohdagi
    bitta ortiqcha `{` — `migrate` yiqiladi va api/worker/sender ISHGA TUSHMAYDI
    (compose: `service_completed_successfully`)."""
    matn = open(migrate.HUQUQLAR, encoding="utf-8").read()
    assert re.search(r"\{(?!rol\}|baza\})", matn) is None
    assert re.search(r"(?<!\{rol)(?<!\{baza)\}", matn) is None
    from psycopg import sql
    tayyor = sql.SQL(matn).format(rol=sql.Identifier("tender_ai"),
                                  baza=sql.Identifier("tender_v2")).as_string(None)
    assert 'GRANT SELECT, INSERT ON sorov_jurnali TO "tender_ai";' in tayyor
    assert 'GRANT USAGE ON SEQUENCE sorov_jurnali_id_seq TO "tender_ai";' in tayyor
    # append-only: ilova roli jurnalni o'zgartira ham, o'chira ham olmaydi
    jurnal_satrlari = [s for s in tayyor.splitlines()
                       if "sorov_jurnali" in s and not s.startswith("--")]
    assert len(jurnal_satrlari) == 2
    assert not any(soz in s for s in jurnal_satrlari for soz in ("UPDATE", "DELETE", "TRUNCATE"))


def test_insert_ustunlari_migratsiyaga_mos():
    assert jurnal._INSERT == ("INSERT INTO sorov_jurnali (" + ", ".join(jurnal.USTUNLAR) + ") VALUES ")
    assert jurnal._QATOR.count("%s") == len(jurnal.USTUNLAR) == 20
    assert jurnal_api._TOLIQ == ("id",) + jurnal.USTUNLAR
    # ro'yxat so'rovi: har nomga bitta ifoda (`left(xabar, 500)` — ortiqcha bitta vergul)
    assert jurnal_api._ROYXAT_SQL.count(",") == len(jurnal_api._ROYXAT) == 19


# ═══════════════════════════════════════════════════════════════════════════
# Sozlama guruhi
# ═══════════════════════════════════════════════════════════════════════════

def test_jurnal_sozlama_guruhi(monkeypatch):
    for nom in ("JURNAL_YOQILGAN", "JURNAL_LOGIN", "JURNAL_PAROL"):
        monkeypatch.delenv(nom, raising=False)
    config.tozala()
    assert config.jurnal() == config.Jurnal(yoqilgan=True, login="", parol="")
    monkeypatch.setenv("JURNAL_LOGIN", "  oquvchi ")
    monkeypatch.setenv("JURNAL_PAROL", " " + "p" * 30 + "\n")
    assert config.jurnal().login == ""                 # keshlangan — `tozala()` gacha
    config.tozala()                                    # guruh `tozala()` da ro'yxatda
    assert config.jurnal() == config.Jurnal(yoqilgan=True, login="oquvchi", parol="p" * 30)
    config.tozala()


@pytest.mark.parametrize("qiymat,kutilgan", [
    ("1", True), ("", True), ("ha", True), ("0", False), ("false", False), ("no", False),
    (" 0 ", False), ("False", True),                   # TEXNIK_YUBORISH bilan bir xil tahlil
])
def test_jurnal_yoqilgan_tahlili(monkeypatch, qiymat, kutilgan):
    monkeypatch.setenv("JURNAL_YOQILGAN", qiymat)
    config.tozala()
    assert config.jurnal().yoqilgan is kutilgan
    config.tozala()


def test_jurnal_guruhi_boshqalardan_mustaqil(monkeypatch):
    """Guruhni api, worker va yuboruvchi — UCHALASI o'qiydi: boshqa guruhning
    noto'g'ri sozlamasi uni, u esa boshqalarni yiqitmasin."""
    # Muhitda (yoki repo ildizidagi `.env` da) JURNAL_YOQILGAN=0 turgan bo'lishi
    # mumkin — DEPLOY.md dagi favqulodda kalit; test natijasi unga bog'liq bo'lmasin.
    monkeypatch.delenv("JURNAL_YOQILGAN", raising=False)
    monkeypatch.setenv("MAX_TOPLAM", "besh yuz")
    monkeypatch.setenv("TENDER_TIMEOUT", "yarim")
    config.tozala()
    assert config.jurnal().yoqilgan is True
    with pytest.raises(config.SozlamaXatosi):
        config.api()
    config.tozala()


def test_env_example_da_jurnal_sozlamalari_bor():
    matn = open(os.path.join(ILDIZ, ".env.example"), encoding="utf-8").read()
    for nom in ("JURNAL_LOGIN", "JURNAL_PAROL", "JURNAL_YOQILGAN"):
        assert nom in matn, f".env.example da yo'q: {nom}"
