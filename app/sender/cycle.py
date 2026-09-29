# -*- coding: utf-8 -*-
"""Bitta aylanish: texnik xabarlarni yig'ish → navbatni yuborish.

(Ilgari `yuboruvchi.py` da — ko'chirilgan.)
"""

from concurrent.futures import ThreadPoolExecutor, as_completed

import psycopg

from app import config
from app.log import log
from app.sender.delivery import yubor
from app.sender.outbox import natijani_yoz, navbatni_ol
from app.sender.technical import texnik_xabarlarni_yig

YUBORISH_OQIM = config.yuboruvchi().yuborish_oqim


# ---------------------------------------------------------------------------
# 5) Bitta aylanish
# ---------------------------------------------------------------------------
def hisob_jami(h):
    """Bitta aylanishda navbatdan OLINGAN xabarlar soni.

    `texnik_yozildi` ATAYLAB kirmaydi: u navbatga YOZILGAN xabarlar soni,
    navbatdan olinganlari emas — uni qo'shsak drain sharti yolg'on «to'plam
    to'la» deb aylanib qolardi.
    """
    return int(h.get("yuborildi", 0)) + int(h.get("xato", 0)) + \
        int(h.get("tashlandi", 0))


def aylanish(conn):
    """Qaytadi: {yuborildi, xato, tashlandi, texnik_yozildi, toshqin}."""
    hisob = {"yuborildi": 0, "xato": 0, "tashlandi": 0,
             "texnik_yozildi": 0, "toshqin": False}

    # 1) texnik xabarlarni yig'ish
    try:
        yozildi, _nomzod, toxtatildi = texnik_xabarlarni_yig(conn)
        hisob["texnik_yozildi"] = yozildi
        hisob["toshqin"] = toxtatildi
    except (psycopg.OperationalError, psycopg.InterfaceError):
        raise
    except Exception as exc:                          # pragma: no cover
        conn.rollback()
        log(f"[texnik] yig'ishda xato: {exc}", "error")

    # 2) navbatni yuborish — COMMIT (navbatni_ol ichida) → HTTP → alohida COMMIT
    xabarlar = navbatni_ol(conn)
    if not xabarlar:
        return hisob
    # HAR natija TAYYOR BO'LGANDA yoziladi (`ijrochi.map` emas — u hamma HTTP
    # tugamaguncha bironta natijani ham qaytarmasdi). Sabab: to'plam eng yomon
    # holatda ceil(TOPLAM/OQIM) × TENDER_TIMEOUT davom etadi (100/20 × 30 s =
    # 150 s), compose'dagi `stop_grace_period` esa 60 s — SIGKILL to'plam
    # o'rtasida tushsa HECH NARSA yozilmasdi, `band_until` tugagach ALLAQACHON
    # HTTP 200 olgan xabarlar qayta POST qilinardi (ishtirokchiga takroriy
    # xabar). Endi yozilmagan oyna butun to'plam emas, bitta so'rov.
    # Yozish ASOSIY oqimda qoladi — `conn` thread'lar orasida ulashilmaydi.
    with ThreadPoolExecutor(max_workers=max(1, YUBORISH_OQIM)) as ijrochi:
        kelajak = {ijrochi.submit(yubor, x): x for x in xabarlar}
        for kel in as_completed(kelajak):
            xabar_id, file_id, status, _c = kelajak[kel]
            kod, xato = kel.result()
            holat = natijani_yoz(conn, xabar_id, kod, xato, status)
            if holat == 2:
                hisob["yuborildi"] += 1
                log(f"  ✓ file_id={file_id} status={status}")
            elif holat == 3:
                hisob["tashlandi"] += 1
                log(f"  ✗ file_id={file_id} status={status} — TASHLANDI "
                    f"({xato[:100]})", "error")
            else:
                hisob["xato"] += 1
                log(f"  ↻ file_id={file_id} status={status} — qayta uriniladi "
                    f"({xato[:100]})", "warning")
    return hisob
