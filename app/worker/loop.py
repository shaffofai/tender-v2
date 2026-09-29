# -*- coding: utf-8 -*-
"""`python jobs_worker.py [--once]` sikli — navbatdan bittadan fayl oladi.

Ishlab chiqarish (Docker) sikli esa `run.py` da (`python main.py --davomiy`).
`_shutdown` — ikkala sikl ham shu bayroqqa qaraydi (SIGTERM/SIGINT).

(Ilgari `jobs_worker.py` da — ko'chirilgan.)
"""

import signal
import sys
import time

import psycopg

from app import config
from app import templates as templates_db
from app.db import DATABASE_URL, safe_dsn, schema
from app.db.schema import JOBS_TABLE, SxemaXatosi
from app.log import log
from app.worker.pipeline import ishni_bajar
from app.worker.queue import (
    ish_ol,
    kashf_qil,
    olik_belgilash,
    shablon_kelganini_tekshir,
    texnik_qoyib_yubor,
)

POLL_INTERVAL = config.worker().poll_interval
RECONNECT_MAX_WAIT = config.worker().reconnect_max_wait


_shutdown = False


def _request_shutdown(signum, _frame):
    global _shutdown
    if _shutdown:
        log("\n[to'xtatish] ikkinchi signal — darhol chiqilmoqda.")
        sys.exit(1)
    _shutdown = True
    log(f"\n[to'xtatish] signal {signum} — joriy fayl tugagach to'xtaymiz.")


def main_loop(once=False):
    log(f"jobs_worker ishga tushdi. DB={safe_dsn()}  jadval={JOBS_TABLE}")

    signal.signal(signal.SIGINT, _request_shutdown)
    try:
        signal.signal(signal.SIGTERM, _request_shutdown)
    except (AttributeError, ValueError):
        pass

    kutish = 1
    while not _shutdown:
        try:
            with psycopg.connect(DATABASE_URL, autocommit=False) as conn:
                kutish = 1
                schema.tekshir(conn)
                if _sessiya(conn, once):
                    return
        except SxemaXatosi as exc:
            # Jadval tuzilmasi mos emas — qayta urinish foydasiz, cheksiz
            # jim aylanishdan ko'ra aniq o'lim yaxshi (systemd ko'rsin).
            log(f"[FATAL] Sxema xatosi: {exc}", "error")
            sys.exit(2)
        except (psycopg.OperationalError, psycopg.InterfaceError) as exc:
            if _shutdown:
                break
            log(f"[DB uzildi] {str(exc)[:180]}", "warning")
            log(f"[DB] {kutish} soniyadan keyin qayta ulanish...")
            time.sleep(kutish)
            kutish = min(kutish * 2, RECONNECT_MAX_WAIT)
        except Exception as exc:
            if _shutdown:
                break
            log(f"[kutilmagan sikl xatosi] {type(exc).__name__}: {exc}", "error")
            time.sleep(min(kutish, RECONNECT_MAX_WAIT))
            kutish = min(kutish * 2, RECONNECT_MAX_WAIT)
    log("Worker to'xtatildi.")


def _etalon_manbaini_tekshir(conn):
    """Etalon olish uchun HECH BO'LMASA bitta manba bormi?

    Etalonsiz ishlash MA'NOSIZ: har bir fayl «etalon aniqlanmadi» texnik
    xatosiga uchrab navbatda qolaveradi, worker esa «sog'lom» ko'rinadi.
    Shuning uchun jimgina aylanishdan ko'ra DARHOL to'xtaymiz.
    """
    # Etalon `templates` jadvalidan olinadi
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT count(*) FROM {templates_db.TEMPLATES_TABLE} "
                        f"WHERE deleted_at IS NULL")
            n = cur.fetchone()[0]
    except Exception as exc:
        conn.rollback()
        log(f"[FATAL] «{templates_db.TEMPLATES_TABLE}» jadvali o'qilmadi: {exc}\n"
            f"          TEMPLATES_TABLE sozlamasini tekshiring.", "error")
        sys.exit(1)
    if n:
        return
    # Jadval BOR va o'qildi, lekin hali bo'sh — bu SOZLAMA XATOSI EMAS,
    # yangi o'rnatishning normal holati: sherik shablonlarni keyinroq
    # yozadi. Ilgari bu yerda `sys.exit(1)` bor edi va yangi serverda
    # konteyner cheksiz qayta ishga tushardi (2026-09-22 Docker sinovida
    # topildi). Fayllar yo'qolmaydi: F1 mexanizmi (11-BOSQICH) ularni
    # «shablon kutilmoqda» holatida ushlab turadi va shablon `templates` ga
    # tushgan zahoti uyg'otadi. Jadval umuman o'qilmasa — yuqorida FATAL.
    log(f"[eslatma] «{templates_db.TEMPLATES_TABLE}» hali bo'sh — shablon "
        f"kutilmoqda. Fayllar navbatda qoladi (status 0), shablon kelgach "
        f"avtomatik tekshiriladi.", "warning")


def _sessiya(conn, once):
    _etalon_manbaini_tekshir(conn)
    olik_belgilash(conn)
    kashf_qil(conn, toliq=True)      # boshlanishida bir marta to'liq
    shablon_kelganini_tekshir(conn)  # F1: kutayotganlar uchun shablon kelganmi

    bosh_tick = 0
    while not _shutdown:
        kashf_qil(conn)              # arzon: faqat yangi qatorlar
        ish, token = ish_ol(conn)
        if ish is None:
            if once:
                log("Ish yo'q. To'xtatildi (--once).")
                return True
            bosh_tick += 1
            if bosh_tick % 60 == 0:
                olik_belgilash(conn)
            # Navbat bo'shadi — to'liq skan qilib ko'ramiz (qaytarilgan
            # qatorlar bo'lishi mumkin), keyin kutamiz.
            kashf_qil(conn, toliq=True)
            # F1: shablon kelgan bo'lsa kutayotgan fayl darhol ishga tushsin
            # (aks holda ETALON_KUTISH_INTERVAL tugashini kutardi).
            shablon_kelganini_tekshir(conn)
            time.sleep(POLL_INTERVAL)
            continue
        bosh_tick = 0
        log(f"\n=== uuid={ish['uuid']} bidder={ish.get('bidder_id')} "
            f"type={ish.get('type')} ===")
        try:
            ishni_bajar(conn, ish, token)
        except (psycopg.OperationalError, psycopg.InterfaceError):
            raise
        except Exception as exc:
            conn.rollback()
            log(f"[kutilmagan xato] {ish['uuid']}: {exc}")
            texnik_qoyib_yubor(conn, ish["uuid"], f"worker exception: {exc}", token=token)
        olik_belgilash(conn)
        if once:
            log("Bitta fayl ishlandi. To'xtatildi (--once).")
            return True
    return True
