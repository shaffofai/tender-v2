# -*- coding: utf-8 -*-
"""F1 (2026-09-08) — «shablon kutilmoqda» holati (bog'lash qatlami, bazasiz).

Ishtirokchi fayli buyurtmachi shablonidan OLDIN kelsa:
  • fayl O'LMAYDI, urinish hisobi sarflanmaydi, `retry_after` qat'iy interval;
  • `last_error` `ETALON_KUTILMOQDA:` bilan boshlanadi;
  • shablon `templates` ga tushgach `shablon_kelganini_tekshir` uyg'otadi;
  • MAX_SOAT o'tsa — o'lik (sabab aniq), `jobs.status` ga tegilmaydi;
  • `EtalonOqilmadi` (shablon bor, lekin o'qilmadi) eski texnik yo'ldan boradi.
Verdikt zanjiriga TEGILMAGAN — bu testlar `ishni_bajar` ni 2-qadamda to'xtatadi.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import templates as templates_db   # noqa: E402
from app.worker import audit, pipeline, queue  # noqa: E402


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


def test_kutish_urinish_sarflamaydi_va_olmaydi(monkeypatch):
    conn = _Conn(javoblar=[(False,)])            # created_at hali chegaradan o'tmagan
    queue.shablon_kutilsin(conn, "77", "tender_id=5 type=excel2 uchun shablon hali yo'q", token="tok")
    sql, prm = conn.jurnal[0]
    assert "attempts = greatest(attempts - 1, 0)" in sql          # ish_ol oshirgani qaytariladi
    assert "retry_after = now() + interval '1 second' * %(i)s" in sql
    assert "dead_at = now()" not in sql
    assert prm["x"].startswith(queue.KUTISH_BELGI + ":") and prm["i"] == queue.ETALON_KUTISH_INTERVAL
    assert prm["tok"] == "tok" and "claim_token = %(tok)s" in sql   # fencing saqlanadi
    assert len(conn.jurnal) == 1 and conn.commit_soni == 1


def test_kutish_chegaradan_otsa_olik(monkeypatch):
    conn = _Conn(javoblar=[(True,)])             # MAX_SOAT o'tgan
    queue.shablon_kutilsin(conn, "77", "shablon hali yo'q")
    assert len(conn.jurnal) == 2
    sql2, prm2 = conn.jurnal[1]
    assert "dead_at = now()" in sql2 and "jobs" not in sql2.split("UPDATE ")[1].split(" SET")[0].lower().replace("jobs_state", "")
    assert f"{queue.ETALON_KUTISH_MAX_SOAT} soat kutildi" in prm2["x"]
    # `files.status` ga tegilmaydi — faqat jobs_state
    assert all(s.startswith("UPDATE jobs_state") for s, _ in conn.jurnal)


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
    monkeypatch.setattr(pipeline, "shablon_kutilsin",
                        lambda conn, uid, sabab, token=None: chaqiruvlar.append(("kutish", uid, sabab)))
    monkeypatch.setattr(pipeline, "texnik_qoyib_yubor",
                        lambda conn, uid, xato, doimiy=False, token=None: chaqiruvlar.append(("texnik", uid, xato, doimiy)))
    ish = {"uuid": "77", "link": "5/excel2/9/x.xlsx", "type": "excel2", "bidder_id": 9, "tender_id": 5}
    pipeline.ishni_bajar(_Conn(), ish, "tok")
    return chaqiruvlar


def test_shablon_yoq_kutish_yoli(monkeypatch, tmp_path):
    ch = _ishni_bajar_2qadamgacha(monkeypatch, tmp_path,
                                  templates_db.EtalonYoq("templates da tender_id=5 type=excel2 uchun etalon topilmadi"))
    assert ch and ch[0][0] == "kutish" and ch[0][1] == "77"
    assert "shablon hali yo'q" in ch[0][2]
    assert not any(c[0] == "texnik" for c in ch)          # eski texnik yo'lga tushmaydi


def test_shablon_bor_lekin_oqilmadi_eski_texnik_yol(monkeypatch, tmp_path):
    ch = _ishni_bajar_2qadamgacha(monkeypatch, tmp_path,
                                  templates_db.EtalonOqilmadi("etalon yuklab olinmadi (templates.id=3)"))
    assert ch and ch[0][0] == "texnik" and ch[0][3] is False   # backoff bilan qayta uriniladi
    assert not any(c[0] == "kutish" for c in ch)


# ── F3: notanish type ───────────────────────────────────────────────────────

def test_notanish_type_va_shablon_yoq_darhol_olik(monkeypatch, tmp_path):
    """type=excel7 (TYPE_ROL da yo'q) va shablon yo'q — kutish emas, DETERMINIK o'lik."""
    fayl = tmp_path / "x.xlsx"; fayl.write_bytes(b"PK\x03\x04")
    monkeypatch.setattr(pipeline, "download", lambda link, papka: str(fayl))
    monkeypatch.setattr(pipeline, "_texnik_iz", lambda *a, **k: None)
    monkeypatch.setattr(templates_db, "etalon_ol",
                        lambda conn, t, typ: (_ for _ in ()).throw(templates_db.EtalonYoq("yo'q")))
    ch = []
    monkeypatch.setattr(pipeline, "shablon_kutilsin", lambda *a, **k: ch.append(("kutish",)))
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
