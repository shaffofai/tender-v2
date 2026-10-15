# -*- coding: utf-8 -*-
"""`GET /dump` — bazaning to'liq nusxasi (app/api/dump.py).

Bazasiz qism: kalitlar (o'z kalitlari, 503/401), parametrlar, bir vaqtda
bitta nusxa, jurnalga tana yozilmasligi, o'rtadagi xato `ROLLBACK` bilan
tugashi va oqim qanday tugamasin band bo'shashi.

Haqiqiy baza bilan (faqat `E2E_ADMIN_URL` berilsa — CI da beriladi): migratsiya
qilingan bazaga noqulay qatorlar yoziladi, nusxa ILOVA ROLI bilan olinadi
(huquqlari yetarliligi ham tekshiriladi), bo'sh bazaga tiklanadi va ikkalasining
katalogi hamda har jadval mazmuni solishtiriladi.
"""
import asyncio
import base64
import gzip
import os
import secrets
import sys

import httpx
import pytest

ILDIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ILDIZ)

from app import config, jurnal                       # noqa: E402
from app.api import dump                             # noqa: E402

D_LOGIN, D_PAROL = "nusxachi", "d" * 24


def basic(login, parol):
    return "Basic " + base64.b64encode(f"{login}:{parol}".encode()).decode()


D_AUTH = {"authorization": basic(D_LOGIN, D_PAROL)}
YOLLAR = ["/dump", "/api-v2/tender-v2/dump"]


def _get(url, headers=None):
    """Haqiqiy ilova, tarmoqsiz va lifespan'siz (bazaga ulanish ochilmaydi)."""
    from app.api.app import app

    async def yubor():
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as m:
            return await m.get(url, headers=headers)
    return asyncio.run(yubor())


@pytest.fixture
def sozlangan(monkeypatch):
    monkeypatch.setenv("DUMP_LOGIN", D_LOGIN)
    monkeypatch.setenv("DUMP_PAROL", D_PAROL)
    config.tozala()
    monkeypatch.setattr(dump, "_BAND", dump._Band())
    monkeypatch.setattr(dump, "ulan", lambda: pytest.fail("bazaga murojaat"))
    yield
    config.tozala()


class _SoxtaUlanish:
    """`ulan()` o'rnida: tekshiruv so'rovlari javobi va yopilganini qayd etadi."""

    def __init__(self, qollanmaydi=()):
        self.qollanmaydi = list(qollanmaydi)
        self.yopildi = False

    def execute(self, sql, *a):
        import datetime
        natija = [(q,) for q in self.qollanmaydi] if "UNION ALL" in sql else \
            [(datetime.datetime(2026, 10, 15, 12, 0, 0, tzinfo=datetime.timezone.utc),)]

        class _N:
            def fetchall(self):
                return natija

            def fetchone(self):
                return natija[0]
        return _N()

    def close(self):
        self.yopildi = True


def _soxta(monkeypatch, bolaklar=("BEGIN;\n", "COPY t FROM stdin;\n", b"1\tbir\n", "\\.\n",
                                  "COMMIT;\n\n-- NUSXA TO'LIQ: 1 jadval, 1 qator.\n"),
           xato=None, qollanmaydi=()):
    ulanish = _SoxtaUlanish(qollanmaydi)
    monkeypatch.setattr(dump, "ulan", lambda: ulanish)

    def qismlar(conn, hisob):
        assert conn is ulanish
        for b in bolaklar:
            if b == "COPY t FROM stdin;\n":
                hisob["copy_ichida"] = True
            yield b
        if xato:
            raise xato
        hisob["copy_ichida"] = False
    monkeypatch.setattr(dump, "qismlar", qismlar)
    return ulanish


# ═══════════════════════════════════════════════════════════════════════════
# Marshrut va kalitlar
# ═══════════════════════════════════════════════════════════════════════════

def test_marshrutlar_ikki_shaklda():
    from app.api.app import app
    yollar = {r.path: r for r in app.routes if getattr(r, "path", "").endswith("/dump")}
    assert sorted(yollar) == sorted(YOLLAR)
    assert {y: r.include_in_schema for y, r in yollar.items()} == {
        "/dump": False, "/api-v2/tender-v2/dump": True}
    assert all(r.methods == {"GET"} for r in yollar.values())


@pytest.mark.parametrize("yol", YOLLAR)
def test_standart_holatda_yopiq(monkeypatch, yol):
    """Kalit berilmagan — 503, to'g'ri ko'rinishdagi so'rov bilan ham."""
    for nom in ("DUMP_LOGIN", "DUMP_PAROL"):
        monkeypatch.delenv(nom, raising=False)
    config.tozala()
    r = _get(yol, D_AUTH)
    assert r.status_code == 503 and "sozlanmagan" in r.json()["xato"]
    assert "www-authenticate" not in r.headers


def test_qisqa_parol_yoq_deb_sanaladi(sozlangan, monkeypatch):
    monkeypatch.setenv("DUMP_PAROL", "p" * 23)
    config.tozala()
    assert _get("/dump", {"authorization": basic(D_LOGIN, "p" * 23)}).status_code == 503


@pytest.mark.parametrize("sarlavha,sabab", [
    (None, "Basic Auth kerak"),
    ("Basic %%%", "Authorization sarlavhasi buzuq"),
    (basic(D_LOGIN, D_PAROL + "x"), "login yoki parol noto'g'ri"),
    (basic("boshqa", D_PAROL), "login yoki parol noto'g'ri"),
])
def test_notogri_kalit_401(sozlangan, sarlavha, sabab):
    r = _get("/dump", {"authorization": sarlavha} if sarlavha else None)
    assert r.status_code == 401 and r.json() == {"xato": sabab}
    assert r.headers["www-authenticate"] == 'Basic realm="tender-dump"'


def test_boshqa_kalitlar_ishlamaydi(sozlangan, monkeypatch):
    """Sherikning (KIRUVCHI_*) va jurnalning (JURNAL_*) kalitlari nusxani ochmaydi."""
    monkeypatch.setenv("JURNAL_LOGIN", "jurnalchi")
    monkeypatch.setenv("JURNAL_PAROL", "j" * 24)
    config.tozala()
    from app.api import auth
    for login, parol in ((auth.KIRUVCHI_LOGIN or "k", auth.KIRUVCHI_PAROL or "k"),
                         ("jurnalchi", "j" * 24)):
        assert _get("/dump", {"authorization": basic(login, parol)}).status_code == 401


@pytest.mark.parametrize("sorov,xato", [
    ("?limit=5", "noma'lum parametr: limit"),
    ("?gzip=ha", "gzip: 0 yoki 1"),
])
def test_parametrlar(sozlangan, sorov, xato):
    r = _get("/dump" + sorov, D_AUTH)
    assert r.status_code == 400 and xato in r.json()["xato"]


# ═══════════════════════════════════════════════════════════════════════════
# Oqim
# ═══════════════════════════════════════════════════════════════════════════

def test_nusxa_fayl_bolib_keladi(sozlangan, monkeypatch):
    ulanish = _soxta(monkeypatch)
    r = _get("/api-v2/tender-v2/dump", D_AUTH)
    assert r.status_code == 200
    assert r.content.endswith(b"-- NUSXA TO'LIQ: 1 jadval, 1 qator.\n")
    assert r.headers["content-type"] == "application/sql; charset=utf-8"
    assert r.headers["content-disposition"] == 'attachment; filename="tender_v2_20261015_120000Z.sql"'
    assert r.headers["cache-control"] == "no-store"
    assert ulanish.yopildi and dump._BAND.ol() is not None          # band bo'shagan


def test_gzip(sozlangan, monkeypatch):
    _soxta(monkeypatch)
    r = _get("/dump?gzip=1", D_AUTH)
    assert r.status_code == 200 and r.headers["content-type"] == "application/gzip"
    assert r.headers["content-disposition"].endswith('.sql.gz"')
    assert gzip.decompress(r.content).endswith(b"-- NUSXA TO'LIQ: 1 jadval, 1 qator.\n")


def test_ortadagi_xato_rollback_bilan_tugaydi(sozlangan, monkeypatch):
    """200 allaqachon ketgan — xato fayl ICHIDA aytiladi va tiklash bekor bo'ladi."""
    import psycopg
    ulanish = _soxta(monkeypatch, bolaklar=("BEGIN;\n", "COPY t FROM stdin;\n", b"1\tbi"),
                     xato=psycopg.OperationalError("uzildi"))
    r = _get("/dump", D_AUTH)
    assert r.status_code == 200
    assert r.content.endswith(b"1\tbi\n\\.\nROLLBACK;\n\n-- XATO: nusxa TO'LIQ EMAS "
                              b"(OperationalError). Bu faylni ishlatmang.\n")
    assert b"COMMIT" not in r.content
    assert ulanish.yopildi and dump._BAND.ol() is not None


def test_bir_vaqtda_bitta(sozlangan, monkeypatch):
    egasi = dump._BAND.ol()
    r = _get("/dump", D_AUTH)
    assert r.status_code == 429 and r.headers["retry-after"] == "60"
    dump._BAND.qoy(egasi)
    _soxta(monkeypatch)
    assert _get("/dump", D_AUTH).status_code == 200


def test_eskirgan_band_beriladi(sozlangan, monkeypatch):
    """Oqim boshlanmay qolgan band abadiy turmaydi."""
    band = dump._BAND
    eski = band.ol()
    assert band.ol() is None
    monkeypatch.setattr(dump, "BAND_ESKIRISH", 0)
    yangi = band.ol()
    assert yangi is not None and yangi is not eski
    band.qoy(eski)                                    # eski egasi yangisini bo'shata olmaydi
    monkeypatch.setattr(dump, "BAND_ESKIRISH", 600)
    assert band.ol() is None


def test_qollanmaydigan_obyekt_rad_etiladi(sozlangan, monkeypatch):
    ulanish = _soxta(monkeypatch, qollanmaydi=["public.v — view"])
    r = _get("/dump", D_AUTH)
    assert r.status_code == 500 and "public.v — view" in r.json()["xato"]
    assert ulanish.yopildi and dump._BAND.ol() is not None


def test_baza_yoq_503(sozlangan, monkeypatch):
    import psycopg

    def yiqil():
        raise psycopg.OperationalError("connection refused")
    monkeypatch.setattr(dump, "ulan", yiqil)
    r = _get("/dump", D_AUTH)
    assert r.status_code == 503 and r.json() == {"xato": "baza mavjud emas"}
    assert dump._BAND.ol() is not None


def test_mijoz_uzilsa_generator_darhol_yopiladi(sozlangan, monkeypatch):
    """Starlette uzilishda generatorni yopmaydi — `_NusxaOqimi` o'zi yopadi.

    Generatorga test o'zi havola ushlab turadi: haqiqiy serverda uzilgan
    so'rovning generatori havolalar halqasida qolib, yig'uvchi kelguncha
    yopilmagan (COPY osilib, band bo'shamagan). Havola bo'lmasa CPython uni
    hisoblagich bilan darhol yopar va test xatoni ko'rmasdi.
    """
    ulanish = _soxta(monkeypatch, bolaklar=["x" * dump.BOLAK] * 50)
    tutilgan, asl = [], dump._oqim

    def ushla(*a):
        tutilgan.append(asl(*a))
        return tutilgan[-1]
    monkeypatch.setattr(dump, "_oqim", ushla)
    from app.api.app import app

    async def yubor():
        jonatildi = []

        async def qabul():
            if jonatildi:
                return {"type": "http.disconnect"}
            return {"type": "http.request", "body": b"", "more_body": False}

        async def jonat(xabar):
            jonatildi.append(xabar)
            if xabar["type"] == "http.response.body" and len(jonatildi) > 3:
                raise OSError("mijoz uzildi")
        scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
                 "http_version": "1.1", "method": "GET", "scheme": "http",
                 "path": "/dump", "raw_path": b"/dump", "query_string": b"", "root_path": "",
                 "headers": [(b"authorization", D_AUTH["authorization"].encode())],
                 "client": ("127.0.0.1", 1), "server": ("testserver", 80)}
        try:
            await app(scope, qabul, jonat)
        except Exception:                 # ClientDisconnect — ilova qatlamlariga bog'liq
            pass
        assert len(jonatildi) > 3
    asyncio.run(yubor())
    assert ulanish.yopildi, "uzilgan nusxaning ulanishi yopilmadi"
    assert dump._BAND.ol() is not None, "uzilgan nusxa bandni bo'shatmadi"


def test_jurnalga_tana_yozilmaydi(sozlangan, monkeypatch):
    """Nusxa — butun baza: uning boshi ham `sorov_jurnali` ga tushmaydi."""
    _soxta(monkeypatch)
    eski = dict(jurnal._HOLAT)
    jurnal._NAVBAT.clear()
    jurnal._HOLAT.update(faol=True, manba="api", bayt=0, tashlandi=0, chegaradan=0,
                         daqiqa=None, daqiqada=0, xabar="soz")
    try:
        r = _get("/dump", D_AUTH)
        rad = _get("/dump", {"authorization": basic(D_LOGIN, "x")})
        qatorlar = [q for q, _ in jurnal._NAVBAT if q["tur"] == "sorov"]
    finally:
        jurnal._NAVBAT.clear()
        jurnal._HOLAT.clear()
        jurnal._HOLAT.update(eski)
    assert r.status_code == 200 and rad.status_code == 401
    ok, yoq = qatorlar
    assert ok["javob_tanasi"] is None and ok["qoshimcha"]["javob_bayt"] == len(r.content)
    assert "javob_qirqildi" not in ok["qoshimcha"]
    assert (ok["tasdiqlangan"], yoq["tasdiqlangan"]) == (True, False)
    assert yoq["javob_tanasi"] is None


# ═══════════════════════════════════════════════════════════════════════════
# Haqiqiy baza: nusxa → bo'sh bazaga tiklash → solishtirish
# ═══════════════════════════════════════════════════════════════════════════

ADMIN_URL = os.environ.get("E2E_ADMIN_URL")
MANBA, NISHON, ROL = "tender_v2_dump_manba", "tender_v2_dump_nishon", "tender_v2_dump_app"

_QATORLAR = r"""
INSERT INTO files (link, file_id, tender_id, type, status, comment, created_at)
SELECT 'https://apisitender.mc.uz/storage/' || g || '/excel1/f.xlsx', g, 290000 + g % 50,
       'excel' || (1 + g % 3), (g % 5)::smallint,
       CASE WHEN g % 7 = 0 THEN NULL ELSE 'Hujjat to''g''ri to''ldirilgan. №' || g END,
       timestamp '2026-10-01' + g * interval '7 seconds'
  FROM generate_series(1, 3000) g;
INSERT INTO files (link, file_id, tender_id, type, status, comment) VALUES
 ('weird\path.xls', 9000001, 1, 'excel1', 2, E'tab\there\nnew line\r\n \\ backslash ''q'' "dq"'),
 ('кирилл/ўзбек.xlsx', 9000002, 2, 'excel2', 1, 'Oʻzbekiston — emoji 😀 va \N matni'),
 ('bo''sh', 9000003, 3, 'excel3', 0, ''),
 ('nuqta', 9000004, 4, 'excel1', 0, E'\\.\nbackslash-nuqtadan keyingi qator');
INSERT INTO templates (link, file_id, tender_id, type) VALUES ('t/1.xlsx', 1, 290001, 'excel1');
INSERT INTO jobs_state (uuid, attempts, last_error, dead_at)
SELECT id::text, (id % 6)::int, CASE WHEN id % 11 = 0 THEN 'ETALON_KUTILMOQDA: x' END,
       CASE WHEN id % 13 = 0 THEN now() END FROM files;
INSERT INTO validation_evidence (link, fayl_hash, ichki_status, tashqi_status, varaq_dalil,
                                 validator_version)
SELECT 'l' || g, md5(g::text), 'ACCEPT_FILLED', 1, jsonb_build_object('varaq', 'Лист1', 'n', g),
       'engine-0.2' FROM generate_series(1, 200) g;
INSERT INTO yuborish_navbati (fayl_id, file_id, status, comment, sabab)
VALUES (1, 1, 4, 'texnik xabar', 'texnik');
INSERT INTO sorov_jurnali (tur, manba, sorov_sarlavhalari, sorov_tanasi)
VALUES ('sorov', 'api', '{"a": "b\\c"}', E'{"link": "a\\\\b\\tc"}');
COMMENT ON TABLE files IS 'Ishtirokchi fayllari';
"""

# Katalog — matn ko'rinishi emas, mazmuni (CHECK ifodasini PostgreSQL qayta
# o'qiganda boshqacha yozishi mumkin; pg_dump ning o'zida ham shunday).
_KATALOG = [
    """SELECT c.relname, a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull,
              pg_get_expr(d.adbin, d.adrelid)
         FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid
         JOIN pg_namespace n ON n.oid = c.relnamespace
         LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
        WHERE n.nspname = 'public' AND c.relkind = 'r' AND a.attnum > 0 AND NOT a.attisdropped
        ORDER BY 1, a.attnum""",
    """SELECT conrelid::regclass::text, conname, contype FROM pg_constraint
        WHERE connamespace = 'public'::regnamespace AND contype <> 'n' ORDER BY 1, 2""",
    "SELECT pg_get_indexdef(indexrelid) FROM pg_index i JOIN pg_class c ON c.oid = i.indrelid "
    "WHERE c.relnamespace = 'public'::regnamespace ORDER BY 1",
    "SELECT pg_get_triggerdef(oid) FROM pg_trigger WHERE NOT tgisinternal ORDER BY 1",
    "SELECT pg_get_functiondef(p.oid) FROM pg_proc p WHERE pronamespace = 'public'::regnamespace",
    "SELECT sequencename, last_value FROM pg_sequences WHERE schemaname = 'public' ORDER BY 1",
    """SELECT obj_description(c.oid, 'pg_class') FROM pg_class c
        WHERE c.relnamespace = 'public'::regnamespace AND c.relkind = 'r' ORDER BY c.relname""",
]


def _tikla(conn, matn):
    """psql qanday o'qisa shunday: SQL bo'laklari va `COPY ... FROM stdin` bloklari."""
    sql, qatorlar = [], iter(matn.splitlines(keepends=True))
    for satr in qatorlar:
        if satr.startswith("COPY ") and satr.rstrip().endswith("FROM stdin;"):
            if sql:
                conn.execute("".join(sql))
                sql = []
            blok = []
            for d in qatorlar:
                if d == "\\.\n":
                    break
                blok.append(d)
            with conn.cursor().copy(satr.rstrip().rstrip(";")) as cp:
                cp.write("".join(blok))
        else:
            sql.append(satr)
    if "".join(sql).strip():
        conn.execute("".join(sql))


@pytest.mark.skipif(not ADMIN_URL, reason="E2E_ADMIN_URL berilmagan (haqiqiy PostgreSQL kerak)")
def test_tiklangan_baza_aynan_bir_xil(monkeypatch):
    import subprocess

    import psycopg
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    d = conninfo_to_dict(ADMIN_URL)
    url = lambda baza, **k: make_conninfo(**{**d, "dbname": baza, **k})  # noqa: E731
    rol_paroli = secrets.token_hex(12)

    with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
        for b in (MANBA, NISHON):
            admin.execute(f"DROP DATABASE IF EXISTS {b}")
            admin.execute(f"CREATE DATABASE {b}")
    try:
        env = dict(os.environ, DATABASE_URL=url(MANBA), APP_DB_USER=ROL,
                   APP_DB_PASSWORD=rol_paroli, PYTHONIOENCODING="utf-8")
        p = subprocess.run([sys.executable, "-m", "app.db.migrate"], cwd=ILDIZ, env=env,
                           capture_output=True, text=True, timeout=120)
        assert p.returncode == 0, p.stdout[-800:] + p.stderr[-800:]
        with psycopg.connect(url(MANBA), autocommit=True) as c:
            c.execute(_QATORLAR)

        # Nusxa ILOVA ROLI bilan — ishlab chiqarishdagidek (huquqlar yetadimi).
        monkeypatch.setattr(dump, "DATABASE_URL", url(MANBA, user=ROL, password=rol_paroli))
        conn = dump.ulan()
        try:
            assert conn.execute(dump._QOLLANMAYDI).fetchall() == []
            hisob = {"jadval": 0, "qator": 0, "copy_ichida": False}
            matn = b"".join(q if isinstance(q, (bytes, memoryview)) else q.encode()
                            for q in dump.qismlar(conn, hisob)).decode("utf-8")
        finally:
            conn.close()
        assert matn.endswith(f"-- NUSXA TO'LIQ: 8 jadval, {hisob['qator']} qator.\n")
        assert hisob["qator"] == 3004 + 1 + 3004 + 200 + 1 + 1 + 2       # + schema_migrations

        with psycopg.connect(url(NISHON), autocommit=True) as c:
            _tikla(c, matn)

        def surat(baza):
            with psycopg.connect(url(baza)) as c:
                jadvallar = [r[0] for r in c.execute(
                    "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY 1")]
                mazmun = {t: c.execute(
                    f"SELECT count(*), md5(coalesce(string_agg(x::text, E'\\n' ORDER BY x::text), '')) "
                    f"FROM public.{t} x").fetchone() for t in jadvallar}
                return [c.execute(q).fetchall() for q in _KATALOG], mazmun

        kat_m, maz_m = surat(MANBA)
        kat_n, maz_n = surat(NISHON)
        assert len(maz_m) == 8
        assert maz_m == maz_n
        for q, m, n in zip(_KATALOG, kat_m, kat_n):
            assert m == n, q
    finally:
        with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
            for b in (MANBA, NISHON):
                admin.execute(f"DROP DATABASE IF EXISTS {b} WITH (FORCE)")
            admin.execute(f"DROP ROLE IF EXISTS {ROL}")
