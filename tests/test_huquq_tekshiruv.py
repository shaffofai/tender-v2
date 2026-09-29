# -*- coding: utf-8 -*-
"""B2 (2026-09-14) — `--health` yozish huquqlarini tekshiradimi (bazasiz).

NEGA. Worker minimal huquqli rol bilan ishlashi kerak
(`app/db/huquqlar.sql`). Agar rolda bitta ustun yetishmasa — masalan
`UPDATE files.updated_at` — HAR verdikt «permission denied» bilan tugaydi,
`_sessiya` uni texnik holatga o'tkazadi va navbat JIMGINA to'xtaydi.
Ilgari `--health` buni ko'rmasdi va SOG'LOM deb ko'rsatardi.

Bu testlar bazaga ULANMAYDI — soxta ulanish `has_*_privilege` javoblarini
boshqaradi.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import schema         # noqa: E402
from app.worker import health     # noqa: E402


class _Kursor:
    """`has_column_privilege` / `has_table_privilege` ga javob beradi."""

    def __init__(self, yoq, yiqiladigan):
        self.yoq = yoq                    # huquq YO'Q deb javob beriladiganlar
        self.yiqiladigan = yiqiladigan    # so'rovning o'zi xato beradiganlar
        self._javob = None

    def execute(self, sql, prm=None):
        if "has_column_privilege" in sql:
            kalit = f"UPDATE {prm[0]}.{prm[1]}"
        elif "has_table_privilege" in sql:
            kalit = f"{prm[1]} {prm[0]}"
        else:
            self._javob = ("test_rol", False)
            return
        if kalit in self.yiqiladigan:
            raise RuntimeError("relation does not exist")
        self._javob = (kalit not in self.yoq,)

    def fetchone(self):
        return self._javob

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Conn:
    def __init__(self, yoq=(), yiqiladigan=()):
        self.yoq, self.yiqiladigan = set(yoq), set(yiqiladigan)
        self.rollback_soni = 0

    def cursor(self):
        return _Kursor(self.yoq, self.yiqiladigan)

    def rollback(self):
        self.rollback_soni += 1


def test_hamma_huquq_bor_bolsa_bosh_royxat():
    assert health._yozish_huquqi(_Conn()) == []


def test_updated_at_yoq_bolsa_tutiladi():
    """ENG MUHIM holat: eski DEPLOY.md aynan shu ustunni unutgan edi."""
    yoq = health._yozish_huquqi(_Conn(yoq={f"UPDATE {schema.JOBS_TABLE}.updated_at"}))
    assert yoq == [f"UPDATE {schema.JOBS_TABLE}.updated_at"]


def test_templates_updated_at_yoq_bolsa_tutiladi():
    T = schema.TEMPLATES_TABLE
    yoq = health._yozish_huquqi(_Conn(yoq={f"UPDATE {T}.updated_at"}))
    assert yoq == [f"UPDATE {T}.updated_at"]


def test_jadval_huquqi_yoq_bolsa_tutiladi():
    yoq = health._yozish_huquqi(_Conn(yoq={"INSERT jobs_state"}))
    assert "INSERT jobs_state" in yoq


def test_evidence_jadvali_yoq_bolsa_tutiladi():
    """Audit jadvali yo'qligi REVIEW verdiktlarini cheksiz kechiktiradi."""
    yoq = health._yozish_huquqi(
        _Conn(yiqiladigan={"INSERT validation_evidence"}))
    assert any("validation_evidence" in x for x in yoq)


def test_validated_at_soralmaydi():
    """Bo'lmagan ustunga huquq talab qilinmasin (yolg'on ogohlantirish) —
    qat'iy sxemada `files.validated_at` YO'Q."""
    assert not any("validated_at" in x for x in
                   health._yozish_huquqi(_Conn(yoq={f"UPDATE {schema.JOBS_TABLE}.validated_at"})))


def test_ustun_sorovi_yiqilsa_huquq_muammosi_deb_hisoblanmaydi():
    """Ustun yo'qligi — sxema masalasi, huquq masalasi emas."""
    conn = _Conn(yiqiladigan={f"UPDATE {schema.JOBS_TABLE}.comment"})
    yoq = health._yozish_huquqi(conn)
    assert not any("comment" in x for x in yoq)
    assert conn.rollback_soni >= 1


def test_huquqlar_sql_mavjud_va_updated_at_ni_qamraydi():
    """`app/db/huquqlar.sql` kod bilan mos bo'lsin (B2 ning hujjat qismi)."""
    ildiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    yol = os.path.join(ildiz, "app", "db", "huquqlar.sql")
    assert os.path.isfile(yol), "app/db/huquqlar.sql topilmadi"
    sql = open(yol, encoding="utf-8").read()
    assert "UPDATE (status, comment, updated_at) ON files" in sql
    assert "UPDATE (status, updated_at) ON templates" in sql
    # Rolning o'zi (NOSUPERUSER ...) migratsiya buyrug'ida yaratiladi
    migrate = open(os.path.join(ildiz, "app", "db", "migrate.py"), encoding="utf-8").read()
    assert "NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT" in migrate
    # Audit jadvaliga UPDATE/DELETE BERILMASIN
    assert "GRANT SELECT, INSERT ON validation_evidence" in sql
    assert "GRANT UPDATE ON validation_evidence" not in sql
    assert "GRANT ALL" not in sql
