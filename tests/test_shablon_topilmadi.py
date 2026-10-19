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
