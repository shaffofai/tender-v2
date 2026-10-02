# -*- coding: utf-8 -*-
"""`python jobs_worker.py` buyruqlari.

    python jobs_worker.py --init-db        # baza sxemasi (= python -m app.db.migrate)
    python jobs_worker.py --once           # bitta fayl ishlab to'xtash
    python jobs_worker.py                  # doimiy polling
    python jobs_worker.py --dead-letters   # qayta ishlanmagan qatorlar
    python jobs_worker.py --requeue <uuid|all>
    python jobs_worker.py --shablonlar     # `templates` dagi etalonlar holati
    python jobs_worker.py --health [--toliq]

(Ilgari `jobs_worker.py` da — ko'chirilgan.)
"""

import argparse
import os
import sys

import psycopg

from app import jurnal
from app import templates as templates_db
from app.db import DATABASE_URL, schema
from app.db.schema import JOBS_TABLE, _kalit_join, _yashamayotgan
from app.log import _utf8_stdout, log
from app.worker.health import health
from app.worker.loop import main_loop
from app.worker.queue import requeue


def init_db():
    """`--init-db` — endi versiyalangan migratsiyalar (`app/db/migrations/`).

    `python -m app.db.migrate` bilan bir xil; qo'llangan versiyalar
    `schema_migrations` da. Xato bo'lsa istisno (chaqiruvchi ko'radi).
    """
    from app.db import migrate
    migrate.migratsiya_qil()


def olik_royxat(conn, limit=100):
    schema.tekshir(conn)
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT s.uuid, s.attempts, s.dead_at, s.dead_reason, j.link,
                   j.file_id, j.type
            FROM jobs_state s LEFT JOIN {JOBS_TABLE} j ON {_kalit_join()}
            WHERE s.dead_at IS NOT NULL
            ORDER BY s.dead_at DESC LIMIT %s
            """, (limit,))
        qatorlar = cur.fetchall()
    if not qatorlar:
        log("O'lik qatorlar yo'q — hammasi qayta ishlangan.")
        return 0
    log(f"{len(qatorlar)} ta qayta ishlanmagan fayl:\n")
    for uid, att, dead_at, sabab, link, bid, typ in qatorlar:
        log(f"  uuid={uid}  bidder={bid}  {typ}  urinish={att}  {dead_at:%Y-%m-%d %H:%M}")
        log(f"      sabab: {(sabab or '')[:150]}")
        log(f"      havola: {(link or '')[:150]}")
    log("\nQayta navbatga qo'yish: python jobs_worker.py --requeue <uuid>  (yoki all)")
    return len(qatorlar)


def shablonlar_xulosasi(conn):
    """`templates` jadvalidagi etalonlar holati (faqat O'QIYDI)."""
    T = templates_db.TEMPLATES_TABLE
    schema.tekshir(conn)
    with conn.cursor() as cur:
        cur.execute(f"SELECT status, count(*) FROM {T} "
                    f"WHERE deleted_at IS NULL GROUP BY status ORDER BY status")
        h = dict(cur.fetchall())
        cur.execute(f"SELECT count(DISTINCT tender_id) FROM {T} "
                    f"WHERE deleted_at IS NULL")
        tenderlar = cur.fetchone()[0]
        cur.execute(f"SELECT type, count(*) FROM {T} "
                    f"WHERE deleted_at IS NULL GROUP BY type ORDER BY type")
        turlar = cur.fetchall()
        # Ishtirokchi fayli bor, lekin shabloni yo'q tenderlar — jim qoladigan
        # texnik muammo, oldindan ko'rinib tursin.
        cur.execute(
            f"SELECT count(*) FROM {JOBS_TABLE} j "
            f"WHERE j.status = 0{_yashamayotgan('j')} AND NOT EXISTS ("
            f"  SELECT 1 FROM {T} t WHERE t.tender_id = j.tender_id "
            f"    AND t.type = j.type AND t.deleted_at IS NULL)")
        etalonsiz = cur.fetchone()[0]

    log(f"  jadval      : {T}   ({tenderlar} ta tender)")
    log(f"  holat       : tekshirilmagan={h.get(0, 0)}  tayyor={h.get(1, 0)}")
    log(f"  turlar      : " + ", ".join(f"{t}={n}" for t, n in turlar))
    log(f"  kesh        : {templates_db.ETALON_CACHE_DIR} "
        f"({len(os.listdir(templates_db.ETALON_CACHE_DIR)) if os.path.isdir(templates_db.ETALON_CACHE_DIR) else 0} fayl)")
    if etalonsiz:
        log(f"  [DIQQAT] {etalonsiz} ta ishtirokchi fayli uchun «{T}» da mos "
            f"shablon yo'q — ular tekshirilmay navbatda qoladi.", "warning")


def main():
    _utf8_stdout()

    ap = argparse.ArgumentParser(
        description="jobs jadvalidan tender fayllarini tekshiruvchi worker")
    ap.add_argument("--init-db", action="store_true",
                    help="baza sxemasini yaratish/yangilash (= python -m app.db.migrate)")
    ap.add_argument("--once", action="store_true", help="Bitta fayl ishlab to'xtash")
    ap.add_argument("--dead-letters", action="store_true",
                    help="Qayta ishlanmagan fayllar ro'yxati")
    ap.add_argument("--requeue", metavar="UUID",
                    help="O'lik qatorni qayta navbatga qo'yish (yoki 'all')")
    # Diskdagi etalonlar (etalon/) olib tashlangan — bayroq eski skriptlar
    # yiqilmasligi uchun qoldirildi, faqat tushuntirish chiqaradi.
    ap.add_argument("--etalonlar", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--shablonlar", action="store_true",
                    help=f"Bazadagi «{templates_db.TEMPLATES_TABLE}» shablonlari holati")
    ap.add_argument("--shablonlarni-tayyorla", action="store_true",
                    help="status=0 shablonlarni oldindan yuklab keshlash (status=1)")
    ap.add_argument("--kesh-tozala", action="store_true",
                    help="Yuklab olingan etalon nusxalarini o'chirish")
    ap.add_argument("--health", action="store_true",
                    help="Tiriklik tekshiruvi (exit 0 = sog'lom, 1 = muammo)")
    ap.add_argument("--toliq", action="store_true",
                    help="--health bilan: navbat holatini ham ko'rsatish")
    args = ap.parse_args()

    if args.etalonlar:
        log("[eslatma] diskdagi etalonlar (etalon/) olib tashlangan — etalonlar "
            "faqat `templates` jadvalidan olinadi: python jobs_worker.py --shablonlar",
            "warning")
        return
    if args.kesh_tozala:
        templates_db.keshni_tozala()
        log(f"[kesh] tozalandi: {templates_db.ETALON_CACHE_DIR}")
        return
    if args.shablonlar:
        with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
            shablonlar_xulosasi(conn)
        return
    if args.shablonlarni_tayyorla:
        with psycopg.connect(DATABASE_URL, autocommit=False) as conn:
            tayyor, xato = templates_db.hammasini_tayyorla(conn)
            conn.commit()
        sys.exit(1 if xato and not tayyor else 0)
    if args.health:
        sys.exit(health(toliq=args.toliq))
    if args.init_db:
        init_db()
        return
    if args.dead_letters:
        with psycopg.connect(DATABASE_URL, autocommit=False) as conn:
            olik_royxat(conn)
        return
    if args.requeue:
        with psycopg.connect(DATABASE_URL, autocommit=False) as conn:
            soni = requeue(conn, args.requeue)
            # Operator amali jurnalga — `requeue` o'z ishini COMMIT qilgandan keyin.
            jurnal.amal_yoz(conn, "worker", "requeue",
                            {"nishon": args.requeue, "soni": soni})
        return
    main_loop(once=args.once)
