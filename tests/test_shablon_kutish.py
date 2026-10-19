# -*- coding: utf-8 -*-
"""Shablon (buyurtmachi fayli) yo'q holati — bog'lash qatlami, bazasiz.

2026-10-19 dan (buyurtmachi qarori) shablon KUTILMAYDI:
  • fayl darhol status 4, izoh «Ushbu lot uchun buyurtmachi fayllari
    topilmadi.», xabar kechikishsiz (`verdict.shablon_topilmadi_yoz`);
  • `dead_reason` `ETALON_KUTILMOQDA:` bilan boshlanadi — shablon keyin
    kelsa `shablon_kelganini_tekshir` tiriltiradi (N1), izohni tozalaydi;
  • jurnalga `hodisa = shablon_topilmadi` satri (audit bot → Telegram);
  • `EtalonOqilmadi` (shablon bor, lekin o'qilmadi) eski texnik yo'ldan boradi.
(Ilgari F1, 2026-09-08: fayl shablonni soatlab kutardi — `shablon_kutilsin`.)
Verdikt zanjiriga TEGILMAGAN — bu testlar `ishni_bajar` ni 2-qadamda to'xtatadi.
Haqiqiy bazadagi yo'l — `tests/test_shablon_topilmadi.py`.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import templates as templates_db   # noqa: E402
from app.worker import audit, pipeline, queue, verdict  # noqa: E402


class _Kursor:
    def __init__(self, jurnal, javoblar, qatorlar=None):
        self.jurnal, self.javoblar, self.rowcount = jurnal, javoblar, 0
        self.qatorlar = qatorlar if qatorlar is not None else []

    def execute(self, sql, prm=None):
        self.jurnal.append((" ".join(sql.split()), prm))
        self.rowcount = 1

    def fetchone(self):
        return self.javoblar.pop(0) if self.javoblar else None

    def fetchall(self):
        return list(self.qatorlar)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Conn:
    """Soxta ulanish: SQL matni va parametrlarini yozib oladi."""
    def __init__(self, javoblar=None, qatorlar=None):
        self.jurnal, self.javoblar, self.commit_soni = [], list(javoblar or []), 0
        self.qatorlar = qatorlar

    def cursor(self):
        return _Kursor(self.jurnal, self.javoblar, self.qatorlar)

    def commit(self):
        self.commit_soni += 1

    def rollback(self):
        pass

    def transaction(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_shablon_topilmadi_yoz_bitta_tranzaksiyada():
    conn = _Conn()
    ish = {"uuid": "77", "bidder_id": 9, "tender_id": 5, "type": "excel2"}
    assert verdict.shablon_topilmadi_yoz(conn, ish, "tok", queue.KUTISH_BELGI + ": yo'q") is True
    assert len(conn.jurnal) == 3
    (s1, p1), (s2, p2), (s3, p3) = conn.jurnal
    assert s1.startswith("UPDATE jobs_state SET") and "dead_at = now()" in s1
    assert "attempts = greatest(attempts - 1, 0)" in s1          # ish_ol oshirgani qaytariladi
    assert "claim_token = %s" in s1 and p1[3:] == ("tok", "tok")  # fencing
    assert p1[0].startswith(queue.KUTISH_BELGI + ":") and p1[1] == p1[0]
    assert s2.startswith("UPDATE files SET status = %s, comment = %s")
    assert p2[:2] == (4, verdict.IZOH_SHABLON_TOPILMADI)
    assert s3.startswith("INSERT INTO yuborish_navbati")
    assert p3 == (77, 9, 4, verdict.IZOH_SHABLON_TOPILMADI, "texnik")
    assert verdict.IZOH_SHABLON_TOPILMADI == "Ushbu lot uchun buyurtmachi fayllari topilmadi."


@pytest.mark.parametrize("link, kutilgan", [
    ("https://apisitender.mc.uz/storage/296436/excel2/16677/80c7.xlsx", "16677"),
    ("296436/excel2/16677/80c7.xlsx", "16677"),
    ("https://apisitender.mc.uz/storage/296436/excel2/16677/80c7.xlsx?token=x", "16677"),
    ("https://apisitender.mc.uz/storage/111111/excel2/16677/80c7.xlsx", None),  # boshqa tender
    ("https://apisitender.mc.uz/storage/296436/excel3/16677/80c7.xlsx", None),  # boshqa tur
    ("https://apisitender.mc.uz/storage/296436/excel2/abc/80c7.xlsx", None),    # raqam emas
    ("80c7.xlsx", None),
])
def test_yuklovchi_faqat_aniq_naqshda(link, kutilgan):
    assert pipeline._yuklovchi({"link": link, "tender_id": 296436, "type": "excel2"}) == kutilgan


def test_shablon_kelganini_tekshir_sql(monkeypatch):
    conn = _Conn()
    n = queue.shablon_kelganini_tekshir(conn)
    # Soxta kursor har UPDATE ga rowcount=1 beradi: 1 ta kutayotgan uyg'otildi
    # + 1 ta kutib O'LGAN fayl tiriltirildi (N1, 2026-09-23) = 2. (Eski test
    # N1 dan oldin yozilgan edi va 1 ni kutardi.)
    assert n == 2
    assert len(conn.jurnal) == 3
    assert conn.jurnal[1][0].startswith("UPDATE files j SET status = 0")
    assert conn.jurnal[2][0].startswith("UPDATE jobs_state s SET dead_at = NULL")
    sql, prm = conn.jurnal[0]
    assert sql.startswith("UPDATE jobs_state s SET retry_after = now()")
    assert templates_db.TEMPLATES_TABLE in sql and "t.tender_id = j.tender_id AND t.type = j.type" in sql
    assert "s.last_error LIKE %s" in sql and prm == (queue.KUTISH_BELGI + "%",)
    assert "deleted_at IS NULL" in sql


def _ishni_bajar_2qadamgacha(monkeypatch, tmp_path, etalon_xato):
    """`ishni_bajar` ni 2-qadamda (etalon topish) to'xtatib, qaysi yo'l tanlanganini yozib oladi."""
    fayl = tmp_path / "x.xlsx"
    fayl.write_bytes(b"PK\x03\x04")
    monkeypatch.setattr(pipeline, "download", lambda link, papka: str(fayl))
    monkeypatch.setattr(pipeline, "_texnik_iz", lambda *a, **k: None)

    def etalon_ol(conn, tender_id, typ):
        raise etalon_xato

    monkeypatch.setattr(templates_db, "etalon_ol", etalon_ol)
    chaqiruvlar = []
    def shablon_topilmadi_yoz(conn, ish, token, sabab):
        chaqiruvlar.append(("yoq", ish["uuid"], sabab))
        return True

    monkeypatch.setattr(pipeline, "shablon_topilmadi_yoz", shablon_topilmadi_yoz)
    monkeypatch.setattr(pipeline, "log", lambda msg, level="info", hodisa=None, qoshimcha=None:
                        chaqiruvlar.append(("log", hodisa, qoshimcha)) if hodisa else None)
    monkeypatch.setattr(pipeline, "texnik_qoyib_yubor",
                        lambda conn, uid, xato, doimiy=False, token=None: chaqiruvlar.append(("texnik", uid, xato, doimiy)))
    ish = {"uuid": "77", "link": "5/excel2/9/x.xlsx", "type": "excel2", "bidder_id": 9, "tender_id": 5}
    pipeline.ishni_bajar(_Conn(), ish, "tok")
    return chaqiruvlar


def test_shablon_yoq_darhol_status4_va_telegram_satri(monkeypatch, tmp_path):
    ch = _ishni_bajar_2qadamgacha(monkeypatch, tmp_path,
                                  templates_db.EtalonYoq("templates da tender_id=5 type=excel2 uchun etalon topilmadi"))
    assert ch[0][0] == "yoq" and ch[0][1] == "77"
    assert ch[0][2].startswith(queue.KUTISH_BELGI + ":")   # N1 tiriltirish va toshqin shu belgi bilan
    assert ch[0][2].startswith(queue.SHABLON_YOQ_BELGI)    # yuboruvchi o'z xabarini yozmaydi
    assert not any(c[0] == "texnik" for c in ch)          # eski texnik yo'lga tushmaydi
    jurnal = [c for c in ch if c[0] == "log"]
    assert len(jurnal) == 1 and jurnal[0][1] == "shablon_topilmadi"
    q = jurnal[0][2]
    assert (q["tender_id"], q["type"], q["file_id"], q["fayl_id"], q["foydalanuvchi"]) == \
        (5, "excel2", 9, "77", "9")
    assert q["link"] == "5/excel2/9/x.xlsx" and q["izoh"] == verdict.IZOH_SHABLON_TOPILMADI


def test_shablon_bor_lekin_oqilmadi_eski_texnik_yol(monkeypatch, tmp_path):
    ch = _ishni_bajar_2qadamgacha(monkeypatch, tmp_path,
                                  templates_db.EtalonOqilmadi("etalon yuklab olinmadi (templates.id=3)"))
    assert ch and ch[0][0] == "texnik" and ch[0][3] is False   # backoff bilan qayta uriniladi
    assert not any(c[0] == "yoq" for c in ch)


# ── F3: notanish type ───────────────────────────────────────────────────────

def test_notanish_type_va_shablon_yoq_darhol_olik(monkeypatch, tmp_path):
    """type=excel7 (TYPE_ROL da yo'q) va shablon yo'q — kutish emas, DETERMINIK o'lik."""
    fayl = tmp_path / "x.xlsx"; fayl.write_bytes(b"PK\x03\x04")
    monkeypatch.setattr(pipeline, "download", lambda link, papka: str(fayl))
    monkeypatch.setattr(pipeline, "_texnik_iz", lambda *a, **k: None)
    monkeypatch.setattr(templates_db, "etalon_ol",
                        lambda conn, t, typ: (_ for _ in ()).throw(templates_db.EtalonYoq("yo'q")))
    ch = []
    monkeypatch.setattr(pipeline, "shablon_topilmadi_yoz", lambda *a, **k: ch.append(("yoq",)))
    monkeypatch.setattr(pipeline, "texnik_qoyib_yubor",
                        lambda conn, uid, xato, doimiy=False, token=None: ch.append(("texnik", xato, doimiy)))
    ish = {"uuid": "78", "link": "5/excel7/9/x.xlsx", "type": "excel7", "bidder_id": 9, "tender_id": 5}
    pipeline.ishni_bajar(_Conn(), ish, "tok")
    assert ch == [("texnik", ch[0][1], True)]
    assert ch[0][1].startswith("TYPE_NOMALUM:") and "excel7" in ch[0][1]


# ── F2: bir nechta shablon ──────────────────────────────────────────────────

def test_qatorni_top_bir_nechta_shablon_eng_yangisi_va_nomzodlar():
    conn = _Conn(qatorlar=[(20561, "b.xlsx", 9002, 250609, "excel1", 1),
                           (20560, "a.xlsx", 9001, 250609, "excel1", 0)])
    q = templates_db.qatorni_top(conn, 250609, "excel1")
    assert q["id"] == 20561 and q["link"] == "b.xlsx"          # tanlov o'zgarmagan: eng yangi id
    assert q["nomzodlar"] == 2 and q["boshqa_idlar"] == [20560]
    sql, prm = conn.jurnal[0]
    assert "ORDER BY id DESC" in sql and "deleted_at IS NULL" in sql and prm == (250609, "excel1")


def test_qatorni_top_bitta_shablon_va_yoq():
    q = templates_db.qatorni_top(_Conn(qatorlar=[(7, "a.xlsx", 1, 3, "excel2", 1)]), 3, "excel2")
    assert q["nomzodlar"] == 1 and q["boshqa_idlar"] == []
    assert templates_db.qatorni_top(_Conn(qatorlar=[]), 3, "excel2") is None


def test_etalon_kop_note_faqat_note():
    ish = {"tender_id": 250609, "type": "excel1"}
    assert audit._etalon_kop_note({"id": 7, "nomzodlar": 1}, ish) is None
    n = audit._etalon_kop_note({"id": 20561, "nomzodlar": 2, "boshqa_idlar": [20560]}, ish)
    assert n["code"] == "ETALON_KOP" and n["severity"] == "note"
    assert "20561" in n["detail_uz"] and "20560" in n["detail_uz"]
    # note statusga va ishtirokchi izohiga ta'sir qilmaydi
    from tender_engine.validate import _render_comment
    assert _render_comment("f.xlsx", [n]) == "Hujjat to'g'ri to'ldirilgan."


def test_kutayotganlar_soni_sql():
    _sx = {}
    conn = _Conn(javoblar=[(3, None)])
    n, eng_eski = queue.kutayotganlar_soni(conn)
    assert n == 3 and eng_eski is None
    sql, prm = conn.jurnal[0]
    assert sql.startswith("SELECT count(*), min(created_at) FROM jobs_state") and prm == (queue.KUTISH_BELGI + "%",)
