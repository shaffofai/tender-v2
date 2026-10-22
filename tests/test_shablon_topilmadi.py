# -*- coding: utf-8 -*-
"""Shablon yo'q — kutilmaydi (2026-10-19), haqiqiy bazada, ilova roli bilan.

`ishni_bajar` → shablon yo'q → BITTA tranzaksiyada: status 4 + izoh + chiquvchi
xabar (kechikishsiz); yuboruvchi o'sha faylga ikkinchi texnik xabar yozmaydi;
shablon keyin kelsa fayl tiriladi (status 0, izoh tozalanadi).
Faqat `E2E_ADMIN_URL` berilsa (CI da beriladi).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.worker import pipeline, queue, verdict         # noqa: E402
from test_pauza import _fayl, baza, haqiqiy               # noqa: E402,F401


@haqiqiy
def test_shablon_yoq_darhol_va_shablon_kelsa_tiriladi(baza, monkeypatch, tmp_path):
    import psycopg

    from app.sender import technical
    admin_url, ilova_url = baza
    fayl = tmp_path / "x.xlsx"
    fayl.write_bytes(b"PK\x03\x04")
    monkeypatch.setattr(pipeline, "download", lambda link, papka: str(fayl))
    monkeypatch.setattr(technical, "status_qabulmi", lambda st: True)
    monkeypatch.setattr(technical, "YUBORISH_TEXNIK_KECHIKISH", 0)

    with psycopg.connect(admin_url, autocommit=True) as adm, \
            psycopg.connect(ilova_url) as conn:
        adm.execute("TRUNCATE files, jobs_state, yuborish_navbati, templates RESTART IDENTITY")
        uid = _fayl(adm, 296436)
        ish, token = queue.ish_ol(conn)
        assert ish["uuid"] == uid
        assert pipeline.ishni_bajar(conn, ish, token) is None

        status, comment = adm.execute("SELECT status, comment FROM files WHERE id = %s::bigint",
                                      (uid,)).fetchone()
        assert (status, comment) == (4, verdict.IZOH_SHABLON_TOPILMADI)
        olik, sabab, band = adm.execute(
            "SELECT dead_at IS NOT NULL, dead_reason, claimed_at IS NOT NULL FROM jobs_state "
            "WHERE uuid = %s", (uid,)).fetchone()
        assert olik and sabab.startswith(queue.KUTISH_BELGI + ":") and not band
        xabarlar = adm.execute("SELECT status, comment, sabab, holat FROM yuborish_navbati").fetchall()
        assert xabarlar == [(4, verdict.IZOH_SHABLON_TOPILMADI, "texnik", 0)]

        # Yuboruvchi o'z «texnik» xabarini QO'SHMAYDI (o'lim va xabar bir lahzada).
        assert technical.texnik_xabarlarni_yig(conn) == (0, 0, False)
        assert adm.execute("SELECT count(*) FROM yuborish_navbati").fetchone()[0] == 1

        # Qayta ishlansa ham (--requeue / sherik havolani almashtirsa) — xabar
        # o'sha bitta: yangi qator ham, yuboruvchining boshqa matnli xabari ham yo'q.
        adm.execute("UPDATE yuborish_navbati SET holat = 2, yuborilgan_at = now()")
        assert queue.requeue(conn, uid) == 1
        ish, token = queue.ish_ol(conn)
        assert ish["uuid"] == uid
        pipeline.ishni_bajar(conn, ish, token)
        assert adm.execute("SELECT status, comment FROM files WHERE id = %s::bigint",
                           (uid,)).fetchone() == (4, verdict.IZOH_SHABLON_TOPILMADI)
        assert technical.texnik_xabarlarni_yig(conn) == (0, 0, False)
        assert adm.execute("SELECT count(*) FROM yuborish_navbati").fetchone()[0] == 1

        # Shablon keyin keldi — fayl qayta tekshiruvga qaytadi, izoh tozalanadi.
        adm.execute("INSERT INTO templates (link, file_id, tender_id, type, status, created_at) "
                    "VALUES ('t/1.xlsx', 1, 296436, 'excel1', 0, now())")
        assert queue.shablon_kelganini_tekshir(conn) == 1
        assert adm.execute("SELECT status, comment FROM files WHERE id = %s::bigint",
                           (uid,)).fetchone() == (0, None)
        assert adm.execute("SELECT dead_at IS NULL, retry_after <= now() FROM jobs_state "
                           "WHERE uuid = %s", (uid,)).fetchone() == (True, True)
        conn.rollback()
        ish2, _t = queue.ish_ol(conn)
        assert ish2["uuid"] == uid


@haqiqiy
def test_sherik_api_si_shablon_bersa(baza, monkeypatch, tmp_path):
    """Buyurtmachi fayllari API si (2026-10-22), ilova roli bilan.

    Ichki yo'l: API bergan shablon `templates` ga yoziladi — fayl status 4 olmaydi.
    Fon qidiruvi: status 4 olgan faylning tenderi keyin so'raladi; API fayllarni
    bersa shablon yoziladi va fayl tiriladi (status 0, izoh tozalanadi).
    """
    import httpx
    import psycopg

    from app import config, download as _w
    from app.worker import buyurtmachi
    admin_url, ilova_url = baza
    fayl = tmp_path / "x.xlsx"
    fayl.write_bytes(b"PK\x03\x04")
    monkeypatch.setattr(pipeline, "download", lambda link, papka: str(fayl))
    monkeypatch.setattr(_w, "download", lambda link, papka: (_ for _ in ()).throw(RuntimeError("tarmoq yo'q")))
    monkeypatch.setattr(_w, "FILE_BASE_URL", "https://apisitender.mc.uz/storage/")
    monkeypatch.setattr(_w, "RUXSAT_HOSTLAR", ("apisitender.mc.uz",))
    monkeypatch.setattr(config, "buyurtmachi_api", lambda: config.BuyurtmachiApi(
        url="https://api.example.uz/get-customer-files", login="l", parol="p",
        timeout=5, interval=1800, kun=7))
    sherikda = {}

    def javob(req):
        tid = int(str(req.url).rsplit("/", 1)[1])
        return httpx.Response(200, json={"success": True, "data": sherikda.get(tid, [])})

    def forma(tid, fid):
        return [{"file_id": fid, "type": "excel1", "link": f"{tid}/excel1/56/{fid}.xlsx",
                 "tender_id": tid}]

    buyurtmachi._tozala()
    buyurtmachi._Mijoz._c = httpx.Client(transport=httpx.MockTransport(javob))
    try:
        with psycopg.connect(admin_url, autocommit=True) as adm, \
                psycopg.connect(ilova_url) as conn:
            adm.execute("TRUNCATE files, jobs_state, yuborish_navbati, templates RESTART IDENTITY")
            yoq = _fayl(adm, 296436)                  # sherikda ham yo'q → status 4
            bor = _fayl(adm, 296449)                  # sherikda bor → status 4 EMAS
            sherikda[296449] = forma(296449, 2970001)

            for kutilgan in (yoq, bor):
                conn.rollback()
                ish, token = queue.ish_ol(conn)
                assert ish["uuid"] == kutilgan
                pipeline.ishni_bajar(conn, ish, token)

            holat = "SELECT f.status, f.comment, s.dead_at IS NOT NULL FROM files f " \
                    "JOIN jobs_state s ON s.uuid = f.id::text WHERE f.id = %s::bigint"
            assert adm.execute(holat, (yoq,)).fetchone() == (4, verdict.IZOH_SHABLON_TOPILMADI, True)
            # Shablon bor, faqat yuklab olinmadi (sinovda tarmoq yo'q) — texnik qayta urinish.
            assert adm.execute(holat, (bor,)).fetchone() == (0, None, False)
            assert adm.execute("SELECT tender_id, type, file_id, link FROM templates").fetchall() == [
                (296449, "excel1", 2970001,
                 "https://apisitender.mc.uz/storage/296449/excel1/56/2970001.xlsx")]

            # Fon qidiruvi: sherik fayllarni keyin yukladi; interval o'tdi.
            sherikda[296436] = forma(296436, 2967839)
            assert buyurtmachi.supur(conn) == 0       # 296436 yaqinda so'ralgan
            buyurtmachi._H.soroldi.clear()
            buyurtmachi._H.supurildi = None
            assert buyurtmachi.supur(conn) == 1
            assert adm.execute(holat, (yoq,)).fetchone() == (0, None, False)
            assert adm.execute("SELECT count(*) FROM templates WHERE tender_id = 296436"
                               ).fetchone()[0] == 1
            buyurtmachi._H.supurildi = None
            assert buyurtmachi.supur(conn) == 0       # endi nomzod yo'q
    finally:
        buyurtmachi._tozala()
