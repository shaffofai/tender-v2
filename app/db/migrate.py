# -*- coding: utf-8 -*-
"""Versiyalangan migratsiyalar — baza sxemasining YAGONA manbai.

    python -m app.db.migrate            # qo'llanmaganlarini qo'llaydi + ilova roli
    python -m app.db.migrate --holat    # qaysilari qo'llangan
    python jobs_worker.py --init-db     # xuddi shu (eski buyruq)

`app/db/migrations/NNNN_nom.sql` — tartib bilan, har biri O'Z tranzaksiyasida;
qo'llanganlari `schema_migrations` da. Bir vaqtda ikki migratsiya yurmasin —
advisory lock. Qo'llangan faylni TAHRIRLAMANG: o'zgarish = yangi fayl.

DATABASE_URL — sxema EGASI bilan (compose: POSTGRES_USER). Ilova (API,
worker, yuboruvchi) minimal huquqli APP_DB_USER bilan ulanadi: APP_DB_PASSWORD
berilsa rol yaratiladi (bor bo'lsa paroli yangilanadi), huquqlar
(`huquqlar.sql`) har safar qayta beriladi — idempotent.
"""

import argparse
import os
import re
import sys

import psycopg
from psycopg import sql

from app import config
from app.db import DATABASE_URL, safe_dsn
from app.log import log

PAPKA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "migrations")
HUQUQLAR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "huquqlar.sql")

_NOM_RE = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")
_ROL_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
#: pg_advisory_lock kaliti — faqat shu dastur ishlatadi (ixtiyoriy doimiy son).
_QULF_KALITI = 740202609


def migratsiyalar():
    """[(versiya, nom, yo'l)] — versiya bo'yicha tartiblangan, uzluksiz."""
    out = []
    for f in sorted(os.listdir(PAPKA)):
        m = _NOM_RE.match(f)
        if m:
            out.append((int(m.group(1)), m.group(2), os.path.join(PAPKA, f)))
        elif f.endswith(".sql"):
            raise SystemExit(f"[migratsiya] noto'g'ri fayl nomi: {f} (kerak: NNNN_nom.sql)")
    kutilgan = list(range(1, len(out) + 1))
    if [v for v, _, _ in out] != kutilgan:
        raise SystemExit(f"[migratsiya] versiyalar uzluksiz emas: {[v for v, _, _ in out]}")
    return out


def _schema_migrations(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            versiya    integer     PRIMARY KEY,
            nom        text        NOT NULL,
            qollanildi timestamptz NOT NULL DEFAULT now()
        )""")


def migratsiya_qil(url=None):
    """Qo'llanmagan migratsiyalarni qo'llaydi, keyin ilova roli va huquqlari.

    Qaytaradi: yangi qo'llangan versiyalar ro'yxati. Xato bo'lsa istisno —
    o'sha migratsiya tranzaksiyasi to'liq qaytariladi.
    """
    url = url or DATABASE_URL
    yangi = []
    with psycopg.connect(url, autocommit=True, connect_timeout=30) as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (_QULF_KALITI,))
        try:
            _schema_migrations(conn)
            bor = {r[0] for r in conn.execute("SELECT versiya FROM schema_migrations")}
            for versiya, nom, yol in migratsiyalar():
                if versiya in bor:
                    continue
                with open(yol, encoding="utf-8") as fh:
                    matn = fh.read()
                with conn.transaction():
                    conn.execute(matn)
                    conn.execute("INSERT INTO schema_migrations (versiya, nom) "
                                 "VALUES (%s, %s)", (versiya, nom))
                yangi.append(versiya)
                log(f"[migratsiya] {versiya:04d}_{nom} qo'llandi")
            if not yangi:
                log("[migratsiya] sxema yangi — qo'llanadigan migratsiya yo'q")
            _ilova_roli(conn)
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (_QULF_KALITI,))
    return yangi


def _ilova_roli(conn):
    """APP_DB_USER rolini yaratadi/yangilaydi va minimal huquqlarni beradi."""
    s = config.migratsiya()
    rol, parol = s.app_db_user, s.app_db_password
    if not rol:
        return
    if not _ROL_RE.match(rol):
        raise SystemExit(f"[migratsiya] APP_DB_USER noto'g'ri: {rol!r}")
    qator = conn.execute(
        "SELECT rolsuper, rolname = current_user FROM pg_roles WHERE rolname = %s",
        (rol,)).fetchone()
    if qator is not None and (qator[0] or qator[1]):
        # Egasi yoki superuser parolini «ilova paroli» deb almashtirib
        # yubormaslik uchun: ilova roli ALOHIDA va oddiy bo'lishi shart.
        raise SystemExit(f"[migratsiya] APP_DB_USER={rol!r} superuser yoki migratsiya "
                         f"rolining o'zi — ilova uchun alohida oddiy rol kerak")
    if parol:
        if qator is None:
            conn.execute(sql.SQL(
                "CREATE ROLE {} LOGIN PASSWORD {} "
                "NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT").format(
                    sql.Identifier(rol), sql.Literal(parol)))
            log(f"[migratsiya] ilova roli yaratildi: {rol}")
        else:
            conn.execute(sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD {}").format(
                sql.Identifier(rol), sql.Literal(parol)))
    elif qator is None:
        log(f"[migratsiya] ilova roli «{rol}» yo'q va APP_DB_PASSWORD berilmagan — "
            f"huquqlar berilmadi (ilova migratsiya roli bilan ulansa shart emas)",
            "warning")
        return
    baza = conn.execute("SELECT current_database()").fetchone()[0]
    with open(HUQUQLAR, encoding="utf-8") as fh:
        matn = fh.read()
    conn.execute(sql.SQL(matn).format(rol=sql.Identifier(rol), baza=sql.Identifier(baza)))
    log(f"[migratsiya] «{rol}» huquqlari yangilandi (app/db/huquqlar.sql)")


def holat(url=None):
    url = url or DATABASE_URL
    with psycopg.connect(url, autocommit=True, connect_timeout=30) as conn:
        try:
            bor = dict(conn.execute(
                "SELECT versiya, qollanildi FROM schema_migrations").fetchall())
        except psycopg.errors.UndefinedTable:
            bor = {}
    print(f"  baza: {safe_dsn()}")
    for versiya, nom, _ in migratsiyalar():
        qachon = bor.get(versiya)
        print(f"  {versiya:04d}_{nom:<40} "
              + (f"qo'llangan {qachon:%Y-%m-%d %H:%M}" if qachon else "KUTILMOQDA"))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Baza sxemasi migratsiyalari")
    ap.add_argument("--holat", action="store_true",
                    help="qo'llangan va kutilayotgan migratsiyalar (faqat o'qiydi)")
    a = ap.parse_args(argv)
    try:
        if a.holat:
            return holat()
        migratsiya_qil()
        return 0
    except psycopg.Error as exc:
        log(f"[migratsiya] XATO: {type(exc).__name__}: {exc}", "error")
        return 1


if __name__ == "__main__":
    sys.exit(main())
