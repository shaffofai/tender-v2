# -*- coding: utf-8 -*-
"""
ishga_tushir.py — LOYIHANI QO'LDA ISHGA TUSHIRISH
==================================================
Bitta skript: muhitni tekshiradi, bazaga ulanadi, tekshiruvni sinab ko'radi
va worker'ni ishga tushiradi. Buyruqlarni yodlab yurish shart emas.

    python ishga_tushir.py              # menyu (eng qulayi)
    python ishga_tushir.py --holat      # muhit + baza holati (hech narsa o'zgarmaydi)
    python ishga_tushir.py --tayyorla   # kerakli jadvallarni yaratish (bir marta)
    python ishga_tushir.py --quruq 20   # 20 ta faylni QURUQ tekshirish (BAZAGA YOZMAYDI)
    python ishga_tushir.py --bitta      # bitta faylni haqiqiy tekshirish (BAZAGA YOZADI)
    python ishga_tushir.py --ishla      # doimiy worker (Ctrl+C bilan to'xtatiladi)

XAVFSIZLIK:
  --holat va --quruq  → bazadan faqat O'QIYDI.
  --bitta va --ishla  → `files.status` va `files.comment` ni yozadi
                        (faqat `send=0` qatorlarga; boshqa ustunga tegilmaydi).
"""

import argparse
import collections
import os
import sys
import time

from app import config
from app.db.schema import JOBS_TABLE, TEMPLATES_TABLE
from app.log import _utf8_stdout


# ---------------------------------------------------------------------------
# Chiroyli chiqish
# ---------------------------------------------------------------------------

def sarlavha(matn):
    print()
    print("=" * 74)
    print(f"  {matn}")
    print("=" * 74)


def qator(nom, qiymat, holat=None):
    """holat: True=yaxshi, False=muammo, None=oddiy ma'lumot"""
    belgi = {True: "[ OK ]", False: "[XATO]", None: "      "}[holat]
    print(f"{belgi}  {nom:<26} {qiymat}")


def xato_chiq(matn, yechim=None):
    print()
    print(f"  XATO: {matn}")
    if yechim:
        print(f"  YECHIM: {yechim}")
    print()


# ---------------------------------------------------------------------------
# 1-bosqich: muhit tekshiruvi (baza kerak emas)
# ---------------------------------------------------------------------------

KERAKLI_PAKETLAR = [
    ("psycopg", "psycopg[binary]", "PostgreSQL ulanishi"),
    ("httpx", "httpx", "fayllarni yuklab olish"),
    ("openpyxl", "openpyxl", ".xlsx o'qish"),
    ("xlrd", "xlrd", ".xls o'qish"),
    ("dotenv", "python-dotenv", ".env o'qish"),
]


def muhitni_tekshir():
    """Paketlar va .env joyidami? Returns: True = davom etsa bo'ladi."""
    sarlavha("1. MUHIT")
    yaxshi = True

    v = sys.version_info
    ok = v >= (3, 9)
    qator("Python", f"{v.major}.{v.minor}.{v.micro}", ok)
    if not ok:
        xato_chiq("Python 3.9 yoki undan yangi kerak.")
        yaxshi = False

    # Virtual muhitdami?
    venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    qator("Virtual muhit", "ha" if venv else "yo'q (tavsiya: .venv)", None)

    yetishmagan = []
    for modul, paket, nima in KERAKLI_PAKETLAR:
        try:
            __import__(modul)
            qator(f"paket: {modul}", nima, True)
        except ImportError:
            qator(f"paket: {modul}", f"YO'Q — {nima}", False)
            yetishmagan.append(paket)
    if yetishmagan:
        xato_chiq(f"{len(yetishmagan)} ta paket o'rnatilmagan.",
                  f"python -m pip install {' '.join(yetishmagan)}")
        return False

    from dotenv import load_dotenv
    env_yoli = os.path.join(config.ROOT, ".env")
    if os.path.isfile(env_yoli):
        load_dotenv(env_yoli)
        qator(".env fayli", "topildi", True)
    else:
        qator(".env fayli", "YO'Q", False)
        xato_chiq(".env fayli yo'q — bazaga qanday ulanishni bilmaymiz.",
                  "copy .env.example .env   (keyin ichidagi qiymatlarni to'ldiring)")
        return False

    dsn = os.environ.get("DATABASE_URL", "").strip()
    host = os.environ.get("DB_HOST", "").strip()
    if not dsn and not host:
        qator("ulanish sozlamasi", "YO'Q", False)
        xato_chiq(".env da DATABASE_URL ham, DB_HOST ham yo'q.",
                  "DATABASE_URL=postgresql://user:parol@host:5432/baza")
        return False
    qator("ulanish sozlamasi", "DATABASE_URL" if dsn else f"DB_HOST={host}", True)
    qator("JOBS_TABLE", JOBS_TABLE + "   (ishtirokchi fayllari)", None)
    qator("TEMPLATES_TABLE", TEMPLATES_TABLE + "   (etalonlar)", None)
    baza_url = os.environ.get("FILE_BASE_URL", "").strip()
    qator("FILE_BASE_URL", baza_url or "(bo'sh — havolalar to'liq bo'lishi kerak)",
          True if baza_url else None)
    return yaxshi


# ---------------------------------------------------------------------------
# 2-bosqich: baza holati (faqat o'qiydi)
# ---------------------------------------------------------------------------

def bazani_tekshir():
    """Bazaga ulanib holatni ko'rsatadi. Returns: (ulandi, jadvallar_tayyor)."""
    sarlavha("2. BAZA")
    import psycopg
    from app import templates as templates_db
    from app.db import DATABASE_URL, safe_dsn, schema
    from app.db.schema import _pk, _yashamayotgan

    try:
        conn = psycopg.connect(DATABASE_URL, connect_timeout=10)
    except Exception as exc:
        qator("ulanish", "MUVAFFAQIYATSIZ", False)
        xato_chiq(f"{type(exc).__name__}: {str(exc)[:160]}",
                  "Baza ishlayaptimi, host/port/parol to'g'rimi — tekshiring.")
        return False, False

    tayyor = True
    with conn:
        conn.autocommit = True
        qator("ulanish", safe_dsn(), True)

        # Ishtirokchi fayllari jadvali + ustun sxemasi
        try:
            schema.tekshir(conn)
            qator(f"jadval: {JOBS_TABLE}",
                  f"kalit={_pk()}, tender_id=bor", True)
        except Exception as exc:
            qator(f"jadval: {JOBS_TABLE}", str(exc)[:70], False)
            xato_chiq(f"«{JOBS_TABLE}» jadvali o'qilmadi.",
                      ".env dagi JOBS_TABLE nomini tekshiring.")
            return True, False

        # Bizning xizmat jadvalimiz (jobs_state) bormi?
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.jobs_state') IS NOT NULL")
            state_bor = cur.fetchone()[0]
        qator("jadval: jobs_state", "tayyor" if state_bor else
              "YO'Q — `--tayyorla` ni ishga tushiring", state_bor)
        tayyor = tayyor and bool(state_bor)

        # Navbat holati
        with conn.cursor() as cur:
            cur.execute(f"SELECT status, count(*) FROM {JOBS_TABLE} "
                        f"WHERE TRUE{_yashamayotgan()} GROUP BY status ORDER BY status")
            h = dict(cur.fetchall())
        qator("navbat", f"kutilmoqda={h.get(0, 0)}  to'ldirilgan={h.get(1, 0)}  "
                        f"kamchilik={h.get(2, 0)}", None)

        # Etalon manbai
        T = templates_db.TEMPLATES_TABLE
        try:
            with conn.cursor() as cur:
                cur.execute(f"SELECT count(*) FROM {T} WHERE deleted_at IS NULL")
                n_tpl = cur.fetchone()[0]
            qator(f"jadval: {T}", f"{n_tpl} ta etalon", n_tpl > 0)
        except Exception:
            conn.rollback()
            n_tpl = 0
            qator(f"jadval: {T}", "YO'Q yoki o'qilmadi", False)

        # Shablonsiz qolgan fayllar — jim qoladigan muammo
        if n_tpl:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT count(*) FROM {JOBS_TABLE} j "
                    f"WHERE j.status = 0{_yashamayotgan('j')} AND NOT EXISTS ("
                    f"  SELECT 1 FROM {T} t WHERE t.tender_id = j.tender_id "
                    f"    AND t.type = j.type AND t.deleted_at IS NULL)")
                etalonsiz = cur.fetchone()[0]
            qator("shablonsiz fayllar", f"{etalonsiz} ta", etalonsiz == 0)
            if etalonsiz:
                print(f"        ^ bular tekshirilmay navbatda qoladi "
                      f"(status 0) — bu TEXNIK holat, rad javob emas.")

        if not n_tpl:
            xato_chiq("Etalon manbai umuman yo'q — hech narsani solishtirib bo'lmaydi.",
                      f"«{T}» jadvaliga shablon qo'shing.")
            tayyor = False

    return True, tayyor


# ---------------------------------------------------------------------------
# 3-bosqich: jadvallarni tayyorlash
# ---------------------------------------------------------------------------

def tayyorla():
    sarlavha("JADVALLARNI TAYYORLASH")
    from app.worker.cli import init_db
    print("  Baza sxemasi migratsiyalar bilan yaratiladi/yangilanadi.")
    print("  (= python -m app.db.migrate)\n")
    init_db()
    print("\n  Tayyor.")


# ---------------------------------------------------------------------------
# 4-bosqich: QURUQ yurish — bazaga yozmaydi
# ---------------------------------------------------------------------------

def quruq_yurish(limit=10):
    """Haqiqiy fayllarni tekshiradi, lekin natijani BAZAGA YOZMAYDI."""
    sarlavha(f"QURUQ YURISH — {limit} ta fayl (BAZAGA YOZILMAYDI)")
    import psycopg
    from app import download as common
    from app import templates as templates_db
    from app.db import DATABASE_URL, schema
    from app.db.schema import _pk, _yashamayotgan, _yuborilmagan
    from tender_engine.reader import read_file, ExcelTooLargeError
    from tender_engine.validate import validate_one

    yuklash_dir = os.path.join(config.ROOT, "_quruq_yuklash")
    os.makedirs(yuklash_dir, exist_ok=True)

    hisob = collections.Counter()
    texnik = []
    namunalar = []
    boshlandi = time.time()

    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        schema.tekshir(conn)
        pk = _pk()
        tender_ustun = "tender_id"
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {pk}::text, link, type, {tender_ustun} "
                f"FROM {JOBS_TABLE} "
                f"WHERE status = 0 AND {_yuborilmagan()}{_yashamayotgan()} "
                f"ORDER BY {pk} LIMIT %s", (limit,))
            qatorlar = cur.fetchall()

        if not qatorlar:
            print("\n  Navbatda kutayotgan fayl yo'q (status=0 va send=0).")
            return
        print(f"\n  {len(qatorlar)} ta fayl tekshiriladi...\n")

        for k, (uid, link, typ, tender) in enumerate(qatorlar, 1):
            nom = common._link_filename(link)
            yuklangan = None
            try:
                if tender is None:
                    raise RuntimeError("tender_id yo'q — bu skript yangi sxema uchun")
                et = templates_db.etalon_ol(conn, tender, typ)   # status'ga TEGMAYDI
                yuklangan = common.download(link, yuklash_dir)
                natija = validate_one(et["role"], read_file(et["path"]),
                                      read_file(yuklangan), nom)
            except (templates_db.EtalonYoq, templates_db.EtalonOqilmadi) as exc:
                hisob["TEXNIK: etalon"] += 1
                texnik.append((uid, f"etalon: {str(exc)[:100]}"))
                continue
            except ExcelTooLargeError as exc:
                hisob["TEXNIK: xavfsizlik"] += 1
                texnik.append((uid, f"xavfsizlik chegarasi: {str(exc)[:100]}"))
                continue
            except Exception as exc:
                hisob["TEXNIK: " + type(exc).__name__] += 1
                texnik.append((uid, f"{type(exc).__name__}: {str(exc)[:100]}"))
                continue
            finally:
                if yuklangan:
                    common._safe_unlink(yuklangan)

            st = natija["status"]
            hisob[f"status={st} [{et['role']}]"] += 1
            belgi = "OK " if st == 1 else "KAM"
            print(f"  [{belgi}] {nom[:46]:<46} {et['role']}")
            if st == 2 and len(namunalar) < 6:
                namunalar.append((nom, natija["comment_uz"]))
            if k % 25 == 0:
                print(f"        ... {k}/{len(qatorlar)}")

    print()
    print("-" * 74)
    print(f"  YAKUN ({time.time() - boshlandi:.0f} soniya)")
    for k in sorted(hisob):
        print(f"    {k:<28} {hisob[k]}")
    t = sum(v for k, v in hisob.items() if k.startswith("TEXNIK"))
    print(f"\n    Tekshirildi : {sum(hisob.values()) - t}")
    print(f"    Texnik xato : {t}  (bularga status=2 BERILMAYDI — navbatda qoladi)")

    if texnik:
        print("\n  Texnik xatolar:")
        for uid, sabab in texnik[:8]:
            print(f"    {uid}: {sabab}")
    if namunalar:
        print("\n  Kamchilik izohi namunalari:")
        for nom, izoh in namunalar:
            print(f"\n    {nom[:60]}")
            print(f"      {izoh[:220]}")

    print("\n  DIQQAT: bazaga hech narsa yozilmadi.")


# ---------------------------------------------------------------------------
# 5-bosqich: haqiqiy ishga tushirish
# ---------------------------------------------------------------------------

def haqiqiy_ishlash(once):
    from app.db import safe_dsn
    from app.worker.loop import main_loop
    if once:
        sarlavha("BITTA FAYL — HAQIQIY (bazaga YOZADI)")
    else:
        sarlavha("WORKER — DOIMIY (bazaga YOZADI).  To'xtatish: Ctrl+C")
    print(f"  Baza   : {safe_dsn()}")
    print(f"  Jadval : {JOBS_TABLE}   (faqat status va comment yoziladi)")
    print()
    main_loop(once=once)


# ---------------------------------------------------------------------------
# Menyu
# ---------------------------------------------------------------------------

MENYU = """
  1  Holatni ko'rish            (bazadan faqat o'qiydi)
  2  Jadvallarni tayyorlash     (jobs_state — bir marta bajariladi)
  3  QURUQ yurish — 10 ta fayl  (tekshiradi, LEKIN YOZMAYDI)
  4  QURUQ yurish — hammasi     (tekshiradi, LEKIN YOZMAYDI)
  5  Bitta faylni haqiqiy       (BAZAGA YOZADI)
  6  Worker'ni ishga tushirish  (BAZAGA YOZADI, Ctrl+C bilan to'xtaydi)
  0  Chiqish
"""


def menyu():
    while True:
        sarlavha("TENDER TEKSHIRUV TIZIMI — QO'LDA ISHGA TUSHIRISH")
        print(MENYU)
        try:
            tanlov = input("  Tanlang [0-6]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0

        if tanlov == "0":
            return 0
        elif tanlov == "1":
            muhitni_tekshir() and bazani_tekshir()
        elif tanlov == "2":
            tayyorla()
        elif tanlov == "3":
            quruq_yurish(10)
        elif tanlov == "4":
            quruq_yurish(10 ** 9)
        elif tanlov in ("5", "6"):
            print("\n  DIQQAT: bu bazadagi `status` va `comment` ni O'ZGARTIRADI.")
            tasdiq = input("  Davom etamizmi? (ha/yo'q): ").strip().lower()
            if tasdiq in ("ha", "h", "yes", "y"):
                try:
                    haqiqiy_ishlash(once=(tanlov == "5"))
                except KeyboardInterrupt:
                    print("\n  To'xtatildi.")
            else:
                print("  Bekor qilindi.")
        else:
            print("  Noto'g'ri tanlov.")

        try:
            input("\n  Davom etish uchun Enter...")
        except (EOFError, KeyboardInterrupt):
            return 0


# ---------------------------------------------------------------------------

def main():
    _utf8_stdout()
    ap = argparse.ArgumentParser(
        description="Tender tekshiruv tizimini qo'lda ishga tushirish")
    ap.add_argument("--holat", action="store_true",
                    help="Muhit va baza holati (hech narsa o'zgarmaydi)")
    ap.add_argument("--tayyorla", action="store_true",
                    help="jobs_state jadvalini yaratish (bir marta)")
    ap.add_argument("--quruq", nargs="?", type=int, const=10, metavar="N",
                    help="N ta faylni QURUQ tekshirish (bazaga YOZMAYDI, standart 10)")
    ap.add_argument("--bitta", action="store_true",
                    help="Bitta faylni haqiqiy tekshirish (bazaga YOZADI)")
    ap.add_argument("--ishla", action="store_true",
                    help="Doimiy worker (bazaga YOZADI)")
    args = ap.parse_args()

    biror_buyruq = any([args.holat, args.tayyorla, args.quruq is not None,
                        args.bitta, args.ishla])
    if not biror_buyruq:
        return menyu()

    # Muhit hamma rejim uchun shart
    if not muhitni_tekshir():
        return 1

    if args.holat:
        ulandi, tayyor = bazani_tekshir()
        if not ulandi:
            return 1
        print()
        if tayyor:
            print("  HAMMASI TAYYOR. Keyingi qadam:")
            print("    python ishga_tushir.py --quruq 10     # avval quruq sinang")
            print("    python ishga_tushir.py --ishla        # keyin haqiqiy ishga tushiring")
        else:
            print("  Yuqoridagi [XATO] belgilarini bartaraf eting.")
        return 0 if tayyor else 1

    if args.tayyorla:
        tayyorla()
        return 0
    if args.quruq is not None:
        quruq_yurish(args.quruq)
        return 0
    if args.bitta or args.ishla:
        try:
            haqiqiy_ishlash(once=args.bitta)
        except KeyboardInterrupt:
            print("\n  To'xtatildi.")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
