# -*- coding: utf-8 -*-
"""
BAZADAGI MA'LUMOTLARNI TEKSHIRISHNI BOSHLASH  (`python main.py`)
================================================================
Navbatda kutayotgan (`status = 0`, `send = 0`) barcha fayllarni tekshiradi va
natijani bazaga yozadi: `status` (1 = to'ldirilgan, 2 = kamchilik, 3 = buyurtmachi
shabloni bilan aynan bir xil — Q1 darvozasi) va
o'zbekcha `comment`.

    python main.py                # hammasini tekshiradi, tugagach to'xtaydi
    python main.py --bitta        # faqat 1 ta fayl (birinchi sinov uchun)
    python main.py -n 20          # faqat 20 ta fayl
    python main.py --quruq        # BAZAGA YOZMASDAN sinab ko'rish
    python main.py --davomiy      # tugagach ham kutib turadi (xizmat rejimi)
    python main.py -y             # tasdiq so'ramaydi (avtomatlashtirish uchun)

TO'XTATISH: Ctrl+C — joriy fayl tugagach xavfsiz to'xtaydi.

NIMA YOZILADI:
    UPDATE files SET status = ?, comment = ? WHERE id = ? AND send = 0
Boshqa hech qaysi ustunga tegilmaydi, yangi qator YARATILMAYDI,
`send = 1` bo'lgan (Shaffofga yuborilgan) qatorlar o'zgarmaydi.

OLTIN QOIDA: texnik xato (tarmoq, etalon topilmadi, fayl o'qilmadi) HECH QACHON
`status = 2` bermaydi — bunday qator `status = 0` da qolib, keyin qayta uriniladi;
urinishlar tugasa `status = 4` (texnik, taslim — rad EMAS, `--requeue` bilan qaytadi).

(Ilgari `main.py` — ko'chirilgan.)
"""

import argparse
import os
import sys
import time

import psycopg

from app import config, jurnal
from app.db import DATABASE_URL, safe_dsn, schema
from app.db.schema import JOBS_TABLE, _pk, _yashamayotgan, _yuborilmagan
from app.log import _utf8_stdout, log
from app.worker import loop
from app.worker.gates import STATUS_IDENTICAL
from app.worker.loop import POLL_INTERVAL, _etalon_manbaini_tekshir
from app.worker.pipeline import ishni_bajar
from app.worker.queue import (
    STATUS_TEXNIK,
    ish_ol,
    kashf_qil,
    kutayotganlar_soni,
    olik_belgilash,
    shablon_kelganini_tekshir,
    texnik_qoyib_yubor,
)


def _chiziq(belgi="="):
    print(belgi * 74)


def _vaqt(soniya):
    soniya = int(soniya)
    if soniya < 60:
        return f"{soniya} soniya"
    if soniya < 3600:
        return f"{soniya // 60} daq {soniya % 60} son"
    return f"{soniya // 3600} soat {(soniya % 3600) // 60} daq"


# ---------------------------------------------------------------------------

def navbat_holati(conn):
    """(kutilmoqda, to'ldirilgan, kamchilik, aynan_nusxa) — faqat o'qiydi.

    aynan_nusxa = status 3 (Q1 darvozasi, 2026-09-18): buyurtmachi shabloni
    bayt-ba-bayt qaytarilgan — kamchilik (2) dan alohida sanaladi.
    """
    with conn.cursor() as cur:
        cur.execute(f"SELECT status, count(*) FROM {JOBS_TABLE} "
                    f"WHERE {_yuborilmagan()}{_yashamayotgan()} "
                    f"GROUP BY status")
        h = dict(cur.fetchall())
    # 4 (texnik, taslim) alohida — `kutilmoqda` ga qo'shilmaydi: u navbatda EMAS
    return h.get(0, 0), h.get(1, 0), h.get(2, 0), h.get(STATUS_IDENTICAL, 0)


def texnik_soni(conn):
    with conn.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {JOBS_TABLE} WHERE status = %s"
                    f"{_yashamayotgan()}", (STATUS_TEXNIK,))
        return cur.fetchone()[0]


def boshlashdan_oldin(conn, tasdiqsiz, davomiy=False):
    """Nima bo'lishini ko'rsatadi va tasdiq so'raydi. False = bekor qilindi."""
    kutmoqda, toldirilgan, kamchilik, nusxa = navbat_holati(conn)
    # F1: shablon kutayotganlar (ishtirokchi shablondan oldin yuklagan) — faqat o'qiydi
    shablon_kutayotgan = 0
    try:
        shablon_kutayotgan, _ = kutayotganlar_soni(conn)
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass

    _chiziq()
    print("  BAZADAGI MA'LUMOTLARNI TEKSHIRISH")
    _chiziq()
    print(f"  Baza    : {safe_dsn()}")
    print(f"  Jadval  : {JOBS_TABLE}")
    print()
    print(f"  Navbatda kutmoqda : {kutmoqda}"
          + (f"   (shundan shablon kutayotgan: {shablon_kutayotgan})" if shablon_kutayotgan else ""))
    print(f"  Allaqachon tekshirilgan : {toldirilgan} to'ldirilgan, "
          f"{kamchilik} kamchilik, {nusxa} aynan nusxa (status=3)")
    print()
    print(f"  Yoziladi : {JOBS_TABLE}.status  va  {JOBS_TABLE}.comment")
    print(f"  Tegilmaydi : boshqa barcha ustunlar, `send = 1` qatorlar")
    _chiziq("-")

    if not kutmoqda:
        if davomiy:
            # Xizmat rejimi: navbat hozir bo'sh bo'lsa ham to'xtamaymiz,
            # yangi qatorlar kelishini kutamiz.
            print("  Navbat hozircha bo'sh — yangi fayllar kutilmoqda.")
            return True
        print("  Navbatda kutayotgan fayl yo'q — tekshiradigan narsa yo'q.")
        return False
    if tasdiqsiz:
        return True
    try:
        javob = input(f"\n  {kutmoqda} ta faylni tekshirib, natijani bazaga "
                      f"yozamizmi? (ha/yo'q): ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    if javob not in ("ha", "h", "yes", "y"):
        print("  Bekor qilindi. Bazaga hech narsa yozilmadi.")
        return False
    return True


def _toxtatishni_ulash():
    """SIGTERM/SIGINT — joriy fayl tugagach xavfsiz to'xtash.

    `docker stop` SIGTERM yuboradi. Ushlamasak Python darhol o'ladi va
    band qilingan qator lease tugaguncha (15 daqiqa) muzlab qoladi.
    """
    import signal

    def toxta(signum, _frame):
        if loop._shutdown:
            print("\n  Ikkinchi signal — darhol chiqilmoqda.")
            sys.exit(1)
        loop._shutdown = True
        print(f"\n  Signal {signum} qabul qilindi — joriy fayl tugagach "
              f"to'xtaymiz...")

    for sig in (getattr(signal, "SIGTERM", None), getattr(signal, "SIGINT", None)):
        if sig is not None:
            try:
                signal.signal(sig, toxta)
            except (ValueError, OSError):
                pass       # asosiy oqim bo'lmasa — e'tiborsiz qoldiramiz


def _qayta_ulan(eski_conn, xato, maks_kutish=60):
    """Baza uzilganda qayta ulanadi (eksponensial kutish bilan).

    Uzoq yurishlarda tarmoq uzilishi odatiy hol — bitta uzilish 10 soatlik
    ishni yo'qqa chiqarmasligi kerak. Ishlangan fayllar allaqachon bazada
    (har fayl alohida tranzaksiyada yoziladi); yarim qolgan fayl esa lease
    muddati (LEASE_MINUTES) o'tgach avtomatik qayta navbatga tushadi.
    """
    log(f"  [baza uzildi] {type(xato).__name__}: {str(xato)[:90]}", "warning")
    try:
        eski_conn.close()
    except Exception:
        pass
    kutish = 1
    urinish = 0
    while not loop._shutdown:
        urinish += 1
        try:
            conn = psycopg.connect(DATABASE_URL, autocommit=False,
                                   connect_timeout=30)
            schema.tekshir(conn)
            log(f"  [baza tiklandi] {urinish}-urinishda davom etamiz")
            return conn
        except Exception as exc:
            log(f"  [ulanish {urinish}] {type(exc).__name__} — "
                   f"{kutish} s kutamiz", "warning")
            time.sleep(kutish)
            kutish = min(kutish * 2, maks_kutish)
    raise KeyboardInterrupt


def tekshirishni_boshla(chegara=None, davomiy=False, tasdiqsiz=False):
    _toxtatishni_ulash()

    with psycopg.connect(DATABASE_URL, autocommit=False) as conn:
        schema.tekshir(conn)
        # So'rov jurnali: shu yerdan boshlab worker'ning log satrlari
        # `sorov_jurnali` ga ham tushadi (O'Z ulanishida, alohida oqimda).
        # `--quruq` bu yerga kelmaydi — u bazaga hech narsa yozmaydi.
        jurnal.boshla("worker")
        _etalon_manbaini_tekshir(conn)

        if not boshlashdan_oldin(conn, tasdiqsiz, davomiy):
            return 0

        boshlangan_kutmoqda = navbat_holati(conn)[0]
        rejalashtirilgan = min(chegara, boshlangan_kutmoqda) if chegara \
            else boshlangan_kutmoqda

        olik_belgilash(conn)
        # Navbatni bir marta to'liq ro'yxatga olamiz. Keyin har aylanishda
        # faqat YANGI qatorlar ko'riladi — aks holda 168 000 qatorli navbatda
        # har bir fayl uchun butun jadval qayta skanerlanardi.
        kashf_qil(conn, toliq=True)

        print()
        n = 0
        t0 = time.time()
        try:
            while not loop._shutdown:
                if chegara and n >= chegara:
                    print(f"\n  Chegaraga yetildi ({chegara} ta).")
                    break

                try:
                    kashf_qil(conn)
                    ish, token = ish_ol(conn)
                    if ish is None:
                        kashf_qil(conn, toliq=True)   # oxirgi to'liq tekshiruv
                        ish, token = ish_ol(conn)
                except (psycopg.OperationalError, psycopg.InterfaceError) as exc:
                    conn = _qayta_ulan(conn, exc)
                    continue
                if ish is None:
                    if not davomiy:
                        break
                    print("  ... navbat bo'sh, kutilmoqda (Ctrl+C = to'xtatish)",
                          end="\r")
                    # N2 (2026-09-23): bu blok `continue` bilan pastdagi
                    # `olik_belgilash` ga YETIB BORMAYDI — navbat bo'shaganda
                    # lease'i tugagan, attempts>=5 qatorlar `dead_at` siz muallaq
                    # qolardi (na texnik xabar, na --dead-letters). `_sessiya`
                    # dagi kabi shu yerda ham chaqiriladi; F1 uyg'otish ham —
                    # aks holda shablon kelgan fayl 600 s kutardi.
                    try:
                        olik_belgilash(conn)
                        shablon_kelganini_tekshir(conn)
                    except (psycopg.OperationalError, psycopg.InterfaceError) as exc:
                        conn = _qayta_ulan(conn, exc)
                        continue
                    time.sleep(POLL_INTERVAL)
                    continue

                n += 1
                otgan = time.time() - t0
                qolgan = ""
                if n > 1 and rejalashtirilgan:
                    tezlik = otgan / (n - 1)
                    qoldi = max(0, rejalashtirilgan - n + 1)
                    qolgan = f"  ~{_vaqt(tezlik * qoldi)} qoldi"
                print(f"\n[{n}/{rejalashtirilgan}]  {JOBS_TABLE}."
                      f"{_pk()}={ish['uuid']}  type={ish.get('type')}"
                      f"{qolgan}")

                try:
                    ishni_bajar(conn, ish, token)
                except (psycopg.OperationalError, psycopg.InterfaceError) as exc:
                    # Baza uzilishi — YURISHNI TO'XTATMAYDI.
                    #
                    # 2026-08-25: 14 712 fayllik yurish 703-faylda o'lgan
                    # («server closed the connection unexpectedly»). Uzoq
                    # yurishda tarmoq uzilishi muqarrar, shuning uchun qayta
                    # ulanib davom etamiz. Ishlangan fayllar bazada saqlangan
                    # (har fayl alohida tranzaksiyada yoziladi), tekshirilmagan
                    # fayl esa lease muddati o'tgach yana navbatga tushadi.
                    conn = _qayta_ulan(conn, exc)
                    continue
                except Exception as exc:
                    conn.rollback()
                    log(f"  [kutilmagan xato] {exc}", "error")
                    texnik_qoyib_yubor(conn, ish["uuid"],
                                          f"main.py: {exc}", token=token)
                try:
                    olik_belgilash(conn)
                except (psycopg.OperationalError, psycopg.InterfaceError) as exc:
                    conn = _qayta_ulan(conn, exc)

        except KeyboardInterrupt:
            print("\n\n  To'xtatildi (Ctrl+C). Ishlangan fayllar bazada saqlandi.")
        if loop._shutdown:
            print("\n  To'xtatish signali — ishlangan fayllar bazada saqlandi.")

        # ── Yakuniy hisobot ────────────────────────────────────────────────
        kutmoqda, toldirilgan, kamchilik, nusxa = navbat_holati(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM jobs_state WHERE dead_at IS NOT NULL")
            olik = cur.fetchone()[0]

        print()
        _chiziq()
        print(f"  YAKUN — {n} ta fayl ishlandi, {_vaqt(time.time() - t0)}")
        _chiziq()
        print(f"    to'ldirilgan (status=1) : {toldirilgan}")
        print(f"    kamchilik    (status=2) : {kamchilik}")
        print(f"    aynan nusxa  (status=3) : {nusxa}   ← buyurtmachi shabloni o'zgarmasdan qaytarilgan")
        print(f"    hali navbatda (status=0): {kutmoqda}")
        try:
            texnik = texnik_soni(conn)
        except Exception:
            conn.rollback()
            texnik = 0
        if texnik or olik:
            print(f"    texnik, taslim (status=4): {texnik}   ← rad EMAS; "
                  f"python jobs_worker.py --dead-letters / --requeue")
        if kutmoqda:
            print()
            print("    Eslatma: navbatda qolganlar TEXNIK sabab bilan qolgan")
            print("    (tarmoq, etalon topilmadi va h.k.) — bu rad javob EMAS.")
            print("    Qayta ishga tushirsangiz yana uriniladi.")
        print()
        print("    Natijalarni ko'rish:")
        print(f"      SELECT {_pk()}, status, comment FROM {JOBS_TABLE}")
        print(f"      WHERE status = 2 ORDER BY {_pk()} LIMIT 20;")
        print()
    return 0


# ---------------------------------------------------------------------------

def main():
    _utf8_stdout()
    ap = argparse.ArgumentParser(
        description="Bazadagi tender fayllarini tekshirishni boshlash")
    ap.add_argument("-n", "--soni", type=int, metavar="N",
                    help="Faqat N ta faylni tekshirish")
    ap.add_argument("--bitta", action="store_true",
                    help="Faqat 1 ta fayl (birinchi sinov uchun)")
    ap.add_argument("--quruq", action="store_true",
                    help="BAZAGA YOZMASDAN sinab ko'rish")
    ap.add_argument("--davomiy", action="store_true",
                    help="Navbat tugagach ham kutib turish (xizmat rejimi)")
    ap.add_argument("-y", "--tasdiqsiz", action="store_true",
                    help="Tasdiq so'ramaslik")
    args = ap.parse_args()

    # Sozlama IKKI yo'l bilan kelishi mumkin: `.env` fayli (qo'lda ishga tushirish)
    # YOKI muhit o'zgaruvchilari (Docker `env_file`/`environment`, systemd
    # `EnvironmentFile`). Ilgari faqat FAYL tekshirilardi — shu sabab konteyner
    # «.env fayli yo'q» deb cheksiz qayta ishga tushardi (`.dockerignore` `.env`
    # ni ataylab bloklaydi, compose esa qiymatlarni muhitga beradi; 2026-09-22).
    if not os.path.isfile(os.path.join(config.ROOT, ".env")) and not (
            os.environ.get("DATABASE_URL") or os.environ.get("DB_HOST")):
        print("XATO: sozlama yo'q — bazaga qanday ulanishni bilmaymiz.")
        print("YECHIM: cp .env.example .env   (keyin ichini to'ldiring)")
        print("        yoki DATABASE_URL (yoki DB_HOST/DB_PORT/DB_DATABASE/"
              "DB_USERNAME/DB_PASSWORD) muhit o'zgaruvchisini bering.")
        return 1

    if args.quruq:
        from app.tools.ishga_tushir import quruq_yurish
        quruq_yurish(args.soni or (1 if args.bitta else 10 ** 9))
        return 0

    chegara = 1 if args.bitta else args.soni
    try:
        return tekshirishni_boshla(chegara=chegara, davomiy=args.davomiy,
                                   tasdiqsiz=args.tasdiqsiz)
    except SystemExit:
        raise
    except KeyboardInterrupt:
        print("\n  To'xtatildi.")
        return 0
    except Exception as exc:
        print(f"\n  XATO: {type(exc).__name__}: {exc}")
        print("  Batafsil holat: python ishga_tushir.py --holat")
        return 1


if __name__ == "__main__":
    sys.exit(main())
