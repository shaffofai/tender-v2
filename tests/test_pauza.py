# -*- coding: utf-8 -*-
"""PAUZA (ommaviy yuklab olish nosozligi) va muddatli toshqin to'xtatgichi (2026-10-16).

Bazasiz qism: pauza holat mashinasi (`app/worker/pauza.py`) va `_yukla` ning
uch yo'li — odatdagi xato, pauzani ochgan xato, pauzadagi xato/muvaffaqiyat.

Haqiqiy baza bilan (faqat `E2E_ADMIN_URL` berilsa — CI da beriladi):
  • `pauza_tirilt` / `pauzada_qaytar` / `pauza_uygot` SQL i — ilova roli bilan;
  • toshqin to'xtatgichi: shablonsiz o'lganlar va muddati o'tganlar baribir
    yoziladi, yangi o'lganlar ushlanadi, ushlash ABADIY emas.
"""
import os
import secrets
import subprocess
import sys

import pytest

ILDIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ILDIZ)

from app.download import PermanentDownloadError    # noqa: E402
from app.worker import pauza, pipeline, queue        # noqa: E402


@pytest.fixture(autouse=True)
def toza_pauza(monkeypatch):
    pauza._tozala()
    monkeypatch.setattr(pauza, "KETMA_KET", 10)
    monkeypatch.setattr(pauza, "TENDERLAR", 3)
    yield
    pauza._tozala()


# ═══════════════════════════════════════════════════════════════════════════
# Holat mashinasi
# ═══════════════════════════════════════════════════════════════════════════

def test_bitta_tenderdan_ochilmaydi():
    """Bitta tenderning fayllari (masalan sherik o'chirgan) — manba emas."""
    for i in range(30):
        assert pauza.xato(i, 777) is None
    assert not pauza.faolmi()


def test_ketma_ket_va_tenderlar_yetsa_ochiladi():
    for i in range(9):
        assert pauza.xato(i, 100 + i % 3) is None
    assert pauza.xato(9, 100) == "yangi"
    assert pauza.faolmi()
    assert pauza.xato(10, 200) == "faol"
    # Pauzadagi xatolar seriyaga qo'shilmaydi — tiriltirish faqat oldingilarga.
    assert pauza.seriya_uuidlari() == [str(i) for i in range(10)]


def test_muvaffaqiyat_seriyani_uzadi():
    for i in range(9):
        pauza.xato(i, i)
    assert pauza.muvaffaqiyat() is False          # pauza yo'q edi
    for i in range(9):
        assert pauza.xato(100 + i, i) is None     # seriya noldan
    assert not pauza.faolmi()


def test_muvaffaqiyat_pauzani_yopadi():
    for i in range(10):
        pauza.xato(i, i)
    assert pauza.faolmi()
    assert pauza.muvaffaqiyat() is True
    assert not pauza.faolmi()
    assert pauza.seriya_uuidlari() == []


def test_ketma_ket_0_ochiq_emas(monkeypatch):
    monkeypatch.setattr(pauza, "KETMA_KET", 0)
    for i in range(100):
        assert pauza.xato(i, i) is None
    assert not pauza.faolmi()


def test_kut_toxtash_signalida_darhol_qaytadi(monkeypatch):
    for i in range(10):
        pauza.xato(i, i)
    uxlash = []
    monkeypatch.setattr(pauza.time, "sleep", uxlash.append)
    pauza.kut(lambda: True)
    assert uxlash == []
    pauza.kut(lambda: False)
    assert len(uxlash) == pauza.SINOV_INTERVAL


# ═══════════════════════════════════════════════════════════════════════════
# `_yukla` — pauza qatlami odatdagi texnik yo'lni qachon almashtiradi
# ═══════════════════════════════════════════════════════════════════════════

class _Yozuv:
    def __init__(self):
        self.chaqiruvlar = []

    def __call__(self, nom):
        def f(*a, **k):
            self.chaqiruvlar.append((nom, a, k))
            return 1
        return f

    def nomlar(self):
        return [c[0] for c in self.chaqiruvlar]


@pytest.fixture
def yozuv(monkeypatch):
    y = _Yozuv()
    monkeypatch.setattr(pipeline, "_texnik_iz", lambda *a, **k: None)
    for nom in ("texnik_qoyib_yubor", "pauza_tirilt", "pauzada_qaytar", "pauza_uygot"):
        monkeypatch.setattr(pipeline, nom, y(nom))
    return y


def _ish(uid, tender):
    return {"uuid": str(uid), "tender_id": tender, "link": f"x/{uid}.xlsx"}


def _yiqil(exc):
    def download(link, papka):
        raise exc
    return download


def test_pauzasiz_xato_odatdagi_yoldan(monkeypatch, yozuv):
    monkeypatch.setattr(pipeline, "download", _yiqil(PermanentDownloadError("havola mavjud emas (HTTP 404)")))
    assert pipeline._yukla(None, _ish(1, 5), "tok", "/tmp/x") is None
    assert yozuv.nomlar() == ["texnik_qoyib_yubor"]
    assert yozuv.chaqiruvlar[0][2]["doimiy"] is True


def test_pauzani_ochgan_xato_seriyani_qaytaradi(monkeypatch, yozuv):
    monkeypatch.setattr(pauza, "KETMA_KET", 3)
    monkeypatch.setattr(pauza, "TENDERLAR", 2)
    monkeypatch.setattr(pipeline, "download", _yiqil(RuntimeError("HTTP 503 — qayta uriniladi")))
    pipeline._yukla(None, _ish(1, 10), "t1", "/tmp/x")
    pipeline._yukla(None, _ish(2, 10), "t2", "/tmp/x")
    assert yozuv.nomlar() == ["texnik_qoyib_yubor", "texnik_qoyib_yubor"]
    pipeline._yukla(None, _ish(3, 11), "t3", "/tmp/x")
    # 3-xato pauzani ochdi: 1 va 2 qaytariladi, 3 o'zi jazosiz qaytadi.
    assert yozuv.nomlar()[2:] == ["pauza_tirilt", "pauzada_qaytar"]
    _nom, a, k = yozuv.chaqiruvlar[2]
    assert sorted(a[1]) == ["1", "2"]
    _nom, a, k = yozuv.chaqiruvlar[3]
    assert a[1] == "3" and k["token"] == "t3"
    assert pauza.faolmi()


def test_pauzadagi_xato_jazosiz_muvaffaqiyat_yopadi(monkeypatch, yozuv):
    for i in range(10):
        pauza.xato(i, i)
    monkeypatch.setattr(pipeline, "download", _yiqil(PermanentDownloadError("Excel fayli emas")))
    pipeline._yukla(None, _ish(50, 1), "t", "/tmp/x")
    assert yozuv.nomlar() == ["pauzada_qaytar"]           # o'lmaydi
    monkeypatch.setattr(pipeline, "download", lambda link, papka: "/tmp/x/fayl.xlsx")
    assert pipeline._yukla(None, _ish(51, 2), "t", "/tmp/x") == "/tmp/x/fayl.xlsx"
    assert yozuv.nomlar() == ["pauzada_qaytar", "pauza_uygot"]
    assert not pauza.faolmi()


def test_muvaffaqiyat_pauzasiz_uygotmaydi(monkeypatch, yozuv):
    monkeypatch.setattr(pipeline, "download", lambda link, papka: "/tmp/x/fayl.xlsx")
    pipeline._yukla(None, _ish(1, 1), "t", "/tmp/x")
    assert yozuv.nomlar() == []


# ═══════════════════════════════════════════════════════════════════════════
# Haqiqiy baza
# ═══════════════════════════════════════════════════════════════════════════

ADMIN_URL = os.environ.get("E2E_ADMIN_URL")
BAZA, ROL = "tender_v2_pauza_test", "tender_v2_pauza_app"
haqiqiy = pytest.mark.skipif(not ADMIN_URL, reason="E2E_ADMIN_URL berilmagan (haqiqiy PostgreSQL kerak)")


@pytest.fixture(scope="module")
def baza():
    """Migratsiya qilingan vaqtinchalik baza: (admin_url, ilova_url)."""
    import psycopg
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    d = conninfo_to_dict(ADMIN_URL)
    parol = secrets.token_hex(12)
    admin_url = make_conninfo(**{**d, "dbname": BAZA})
    ilova_url = make_conninfo(**{**d, "dbname": BAZA, "user": ROL, "password": parol})
    with psycopg.connect(ADMIN_URL, autocommit=True) as c:
        c.execute(f"DROP DATABASE IF EXISTS {BAZA} WITH (FORCE)")
        c.execute(f"DROP ROLE IF EXISTS {ROL}")
        c.execute(f"CREATE DATABASE {BAZA}")
    try:
        env = dict(os.environ, DATABASE_URL=admin_url, APP_DB_USER=ROL,
                   APP_DB_PASSWORD=parol, PYTHONIOENCODING="utf-8")
        p = subprocess.run([sys.executable, "-m", "app.db.migrate"], cwd=ILDIZ, env=env,
                           capture_output=True, text=True, timeout=120)
        assert p.returncode == 0, p.stdout[-800:] + p.stderr[-800:]
        yield admin_url, ilova_url
    finally:
        with psycopg.connect(ADMIN_URL, autocommit=True) as c:
            c.execute(f"DROP DATABASE IF EXISTS {BAZA} WITH (FORCE)")
            c.execute(f"DROP ROLE IF EXISTS {ROL}")


_SANOQ = iter(range(1, 10 ** 6))


def _fayl(adm, tender, status=0, olgan=None, sabab=None, urinish=1, xato=None,
          qayta=None, band=None):
    """files + jobs_state qatori. Vaqtlar — hozirdan soniya (manfiy = o'tmish)."""
    fid = next(_SANOQ)
    uid = adm.execute(
        "INSERT INTO files (link, file_id, tender_id, type, status, created_at, updated_at) "
        "VALUES (%s, %s, %s, 'excel1', %s, now(), now()) RETURNING id",
        (f"x/{fid}.xlsx", 700000 + fid, tender, status)).fetchone()[0]
    adm.execute(
        "INSERT INTO jobs_state (uuid, attempts, last_error, retry_after, dead_at, "
        "dead_reason, claimed_at, claim_token, created_at) VALUES (%s, %s, %s, "
        "now() + %s::int * interval '1 second', now() + %s::int * interval '1 second', "
        "%s, now() + %s::int * interval '1 second', %s, now() - %s * interval '1 second')",
        (str(uid), urinish, xato, qayta, olgan, sabab, band,
         "boshqa" if band is not None else None, 1000 - fid))
    return str(uid)


def _holat(adm, uid):
    return adm.execute(
        "SELECT f.status, s.attempts, s.dead_at IS NOT NULL, s.last_error, "
        "s.retry_after > now(), s.claimed_at IS NOT NULL "
        "FROM files f JOIN jobs_state s ON s.uuid = f.id::text WHERE f.id = %s::bigint",
        (uid,)).fetchone()


@haqiqiy
def test_tirilt_qaytar_uygot_ilova_roli_bilan(baza):
    import psycopg
    admin_url, ilova_url = baza
    with psycopg.connect(admin_url, autocommit=True) as adm, \
            psycopg.connect(ilova_url) as conn:
        adm.execute("TRUNCATE files, jobs_state RESTART IDENTITY")
        olik = _fayl(adm, 1, status=4, olgan=-60, sabab="havola mavjud emas (HTTP 404)")
        kutgan = _fayl(adm, 2, urinish=2, xato="HTTP 503", qayta=300)
        verdikt = _fayl(adm, 3, status=1)
        band = _fayl(adm, 4, status=0, olgan=-60, sabab="timeout", band=-10)

        n = queue.pauza_tirilt(conn, [olik, kutgan, verdikt, band], "manba nosoz — x", 600)
        assert n == 2
        assert _holat(adm, olik) == (0, 0, False, "PAUZA: manba nosoz — x", True, False)
        assert _holat(adm, kutgan) == (0, 1, False, "PAUZA: manba nosoz — x", True, False)
        assert _holat(adm, verdikt) == (1, 1, False, None, None, False)   # tegilmadi
        assert _holat(adm, band) == (0, 1, True, None, None, True)        # boshqada band

        assert queue.pauzadagilar_soni(conn) == 2
        assert queue.ish_ol(conn) == (None, None)                 # hammasi kutmoqda
        assert queue.pauza_uygot(conn) == 2
        ish, token = queue.ish_ol(conn)
        assert ish["uuid"] in (olik, kutgan)
        oldin = _holat(adm, ish["uuid"])[1]
        queue.pauzada_qaytar(conn, ish["uuid"], "manba nosoz — y", 600, token=token)
        st = _holat(adm, ish["uuid"])
        assert st[1] == oldin - 1 and st[2] is False and st[5] is False
        assert st[3] == "PAUZA: manba nosoz — y" and st[4] is True


@haqiqiy
def test_ommaviy_nosozlik_hech_kimni_oldirmaydi(baza, monkeypatch):
    """Worker sikli kabi: `ish_ol` → `_yukla` → `olik_belgilash`, manba yotgan.

    Pauzasiz bu 10 ta faylning hammasi (404 — bitta urinishda) o'lik va
    status 4 bo'lardi. Pauza bilan — bittasi ham o'lmaydi, manba tiklangach
    birinchi muvaffaqiyat qolganlarini darhol navbatga qaytaradi.
    """
    import psycopg
    admin_url, ilova_url = baza
    monkeypatch.setattr(pauza, "KETMA_KET", 6)
    monkeypatch.setattr(pauza, "TENDERLAR", 3)
    with psycopg.connect(admin_url, autocommit=True) as adm, \
            psycopg.connect(ilova_url) as conn:
        adm.execute("TRUNCATE files, jobs_state, yuborish_navbati RESTART IDENTITY")
        uids = [_fayl(adm, 60 + i % 3) for i in range(10)]
        monkeypatch.setattr(pipeline, "download",
                            _yiqil(PermanentDownloadError("havola mavjud emas (HTTP 404)")))
        for _ in range(20):
            ish, token = queue.ish_ol(conn)
            if ish is None:
                break
            assert pipeline._yukla(conn, ish, token, "/tmp/pauza_test") is None
            queue.olik_belgilash(conn)
        assert pauza.faolmi()
        olik, holat4 = adm.execute(
            "SELECT count(*) FILTER (WHERE s.dead_at IS NOT NULL), "
            "count(*) FILTER (WHERE f.status = 4) "
            "FROM files f JOIN jobs_state s ON s.uuid = f.id::text").fetchone()
        assert (olik, holat4) == (0, 0)
        assert queue.pauzadagilar_soni(conn) == 10
        conn.rollback()          # ochiq tranzaksiyada now() eski bo'lib qoladi

        # Manba tiklandi: sinov nomzodi vaqti keldi, yuklab olish muvaffaqiyatli.
        adm.execute("UPDATE jobs_state SET retry_after = now() - interval '1 minute' "
                    "WHERE uuid = %s", (uids[0],))
        monkeypatch.setattr(pipeline, "download", lambda link, papka: "/tmp/pauza_test/f.xlsx")
        ish, token = queue.ish_ol(conn)
        assert ish["uuid"] == uids[0]
        assert pipeline._yukla(conn, ish, token, "/tmp/pauza_test") == "/tmp/pauza_test/f.xlsx"
        assert not pauza.faolmi()
        kutayotgan = adm.execute(
            "SELECT count(*) FROM jobs_state WHERE retry_after > now()").fetchone()[0]
        assert kutayotgan == 0                       # hammasi darhol navbatda
        conn.rollback()


@haqiqiy
def test_toshqin_ushlaydi_lekin_abadiy_emas(baza, monkeypatch):
    import psycopg

    from app.sender import technical
    admin_url, ilova_url = baza
    monkeypatch.setattr(technical, "status_qabulmi", lambda st: True)
    monkeypatch.setattr(technical, "YUBORISH_TEXNIK_KECHIKISH", 0)
    monkeypatch.setattr(technical, "TEXNIK_TOSHQIN_CHEGARA", 5)
    monkeypatch.setattr(technical, "TEXNIK_TOSHQIN_MAKS_KUTISH", 3600)

    with psycopg.connect(admin_url, autocommit=True) as adm, \
            psycopg.connect(ilova_url) as conn:
        adm.execute("TRUNCATE files, jobs_state, yuborish_navbati RESTART IDENTITY")
        yangi = [_fayl(adm, 10, status=4, olgan=-60, sabab="havola mavjud emas (HTTP 404)")
                 for _ in range(8)]
        shablonsiz = [_fayl(adm, 20 + i, status=4, olgan=-60,
                            sabab="ETALON_KUTILMOQDA: shablon hali yo'q (18 soat kutildi)")
                      for i in range(3)]
        eski = [_fayl(adm, 30, status=4, olgan=-7200, sabab="havola mavjud emas (HTTP 404)")
                for _ in range(2)]

        def navbat():
            return {r[0]: r[1] for r in adm.execute(
                "SELECT fayl_id::text, comment FROM yuborish_navbati WHERE sabab = 'texnik'")}

        with conn.cursor() as cur:
            assert technical.toshqin_soni(cur) == 8
        conn.rollback()

        # Toshqin: 8 yangi o'lim ushlanadi; shablonsiz 3 ta va muddati o'tgan 2 ta ketadi.
        assert technical.texnik_xabarlarni_yig(conn) == (5, 13, True)
        n = navbat()
        assert set(n) == set(shablonsiz) | set(eski)
        assert all(n[u] == technical.MATN_SHABLON for u in shablonsiz)
        assert all(n[u] == technical.MATN_FAYL for u in eski)

        # Hali ham toshqin — yangilari ushlanishda davom etadi, hech narsa yozilmaydi.
        assert technical.texnik_xabarlarni_yig(conn) == (0, 8, True)

        # Ushlash muddati o'tdi (odam aralashmadi) — xabarlar baribir ketadi, 5 tadan.
        adm.execute("UPDATE jobs_state SET dead_at = now() - interval '2 hours' "
                    "WHERE uuid = ANY(%s)", (yangi,))
        assert technical.texnik_xabarlarni_yig(conn) == (5, 8, False)
        assert technical.texnik_xabarlarni_yig(conn) == (3, 3, False)
        assert set(navbat()) == set(yangi) | set(shablonsiz) | set(eski)
        assert technical.texnik_xabarlarni_yig(conn) == (0, 0, False)


@haqiqiy
def test_toshqin_yoq_bolsa_hammasi_darhol(baza, monkeypatch):
    import psycopg

    from app.sender import technical
    admin_url, ilova_url = baza
    monkeypatch.setattr(technical, "status_qabulmi", lambda st: True)
    monkeypatch.setattr(technical, "YUBORISH_TEXNIK_KECHIKISH", 0)
    monkeypatch.setattr(technical, "TEXNIK_TOSHQIN_CHEGARA", 5)
    with psycopg.connect(admin_url, autocommit=True) as adm, \
            psycopg.connect(ilova_url) as conn:
        adm.execute("TRUNCATE files, jobs_state, yuborish_navbati RESTART IDENTITY")
        for _ in range(4):
            _fayl(adm, 40, status=4, olgan=-60, sabab="timeout")
        assert technical.texnik_xabarlarni_yig(conn) == (4, 4, False)
