# -*- coding: utf-8 -*-
"""
korish.py — BAZADA NIMA BORLIGINI KO'RISH
==========================================
Bazadan FAQAT O'QIYDI — hech narsa o'zgartirmaydi, psql ham kerak emas.

    python korish.py                  # umumiy xulosa
    python korish.py --sxema          # jadvallar va ustunlar ro'yxati
    python korish.py --fayllar        # `files` qatorlari (ishtirokchi)
    python korish.py --fayllar 50     # 50 tasini ko'rsatish
    python korish.py --shablonlar     # `templates` qatorlari (buyurtmachi)
    python korish.py --tenderlar      # tender bo'yicha guruhlab
    python korish.py --natijalar      # tekshiruvdan o'tganlari + izohlari
    python korish.py --kamchilik      # faqat kamchilik topilganlari (status=2)
    python korish.py --tender 7177    # bitta tenderning hamma fayli
    python korish.py --sql            # qo'lda tekshirish uchun SQL buyruqlari
"""


import argparse
import sys

from app.db import DATABASE_URL, safe_dsn, schema
from app.db.schema import JOBS_TABLE, _pk, _yashamayotgan
from app.log import _utf8_stdout


STATUS_NOM = {0: "kutilmoqda", 1: "to'ldirilgan", 2: "kamchilik"}


def _chiziq(belgi="=", uzunlik=90):
    print(belgi * uzunlik)


def sarlavha(matn):
    print()
    _chiziq()
    print(f"  {matn}")
    _chiziq()


def _qisqa(matn, n):
    """Matnni n belgigacha qisqartiradi (satr uzilishlarini ham oladi)."""
    if matn is None:
        return ""
    s = " ".join(str(matn).split())
    return s if len(s) <= n else s[:n - 1] + "…"


def _fayl_nomi(link):
    if not link:
        return ""
    return str(link).replace("\\", "/").rstrip("/").split("/")[-1]


def jadval_chiqar(ustunlar, qatorlar, kengliklar):
    """Oddiy matnli jadval chiqaradi."""
    bosh = "  ".join(f"{u:<{k}}" for u, k in zip(ustunlar, kengliklar))
    print("  " + bosh)
    print("  " + "  ".join("-" * k for k in kengliklar))
    for r in qatorlar:
        print("  " + "  ".join(f"{_qisqa(v, k):<{k}}" for v, k in zip(r, kengliklar)))


# ---------------------------------------------------------------------------

def ulan():
    import psycopg
    try:
        conn = psycopg.connect(DATABASE_URL, autocommit=True, connect_timeout=10)
    except Exception as exc:
        print(f"\n  BAZAGA ULANIB BO'LMADI: {type(exc).__name__}: {str(exc)[:150]}")
        print("\n  Sabablari:")
        print("    - baza serveri o'chiq yoki band (vaqtinchalik bo'lishi mumkin)")
        print("    - tarmoq/VPN uzilgan")
        print("    - .env dagi DATABASE_URL da host/port/parol noto'g'ri")
        print("\n  Bir necha daqiqadan keyin qayta urinib ko'ring.\n")
        sys.exit(1)
    schema.tekshir(conn)
    return conn


# ---------------------------------------------------------------------------

def xulosa(conn):
    from app import templates as templates_db
    T = templates_db.TEMPLATES_TABLE
    F = JOBS_TABLE
    pk = _pk()

    sarlavha("UMUMIY XULOSA")
    print(f"  Baza    : {safe_dsn()}")
    print(f"  Jadval  : {F} (ishtirokchi fayllari)   |   {T} (buyurtmachi etalonlari)")

    with conn.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {F} WHERE TRUE{_yashamayotgan()}")
        jami = cur.fetchone()[0]
        cur.execute(f"SELECT status, count(*) FROM {F} "
                    f"WHERE TRUE{_yashamayotgan()} GROUP BY status ORDER BY status")
        st = dict(cur.fetchall())
        cur.execute(f"SELECT send, count(*) FROM {F} "
                    f"WHERE TRUE{_yashamayotgan()} GROUP BY send ORDER BY send")
        send = dict(cur.fetchall())
        cur.execute(f"SELECT count(DISTINCT tender_id) FROM {F} "
                    f"WHERE TRUE{_yashamayotgan()}")
        n_tender = cur.fetchone()[0]
        cur.execute(f"SELECT type, count(*) FROM {F} "
                    f"WHERE TRUE{_yashamayotgan()} GROUP BY type ORDER BY type")
        turlar = cur.fetchall()
        cur.execute(f"SELECT count(*) FROM {F} WHERE comment IS NOT NULL "
                    f"AND comment <> ''{_yashamayotgan()}")
        izohli = cur.fetchone()[0]

    print()
    print(f"  ISHTIROKCHI FAYLLARI ({F}): jami {jami} ta, {n_tender} ta tenderda")
    for s in sorted(st):
        print(f"      status={s}  {STATUS_NOM.get(s, '?'):<14} {st[s]:>5} ta")
    print(f"      izoh yozilgan  {' ':<14} {izohli:>5} ta")
    print(f"      turlar: " + ", ".join(f"{t}={n}" for t, n in turlar))
    print(f"      send  : " + ", ".join(
        f"{'yuborilgan' if k else 'yuborilmagan'}={v}" for k, v in send.items()))

    # Notanish `type` — bu jimgina qolib ketadigan muammo: etalon roli
    # aniqlanmay, fayllar cheksiz «texnik xato» bo'lib navbatda qolaveradi.
    notanish = [(t, n) for t, n in turlar if t not in templates_db.TYPE_ROL]
    if notanish:
        jami_n = sum(n for _, n in notanish)
        print()
        print(f"  DIQQAT: NOTANISH fayl turi — {jami_n} ta fayl:")
        for t, n in notanish:
            print(f"      «{t}» — {n} ta")
        print(f"      Bu turlar `app/templates.py` TYPE_ROL da yo'q. Agar shakli")
        print(f"      tuzilma bo'yicha ham tanilmasa, ular TEKSHIRILMAY")
        print(f"      status=0 da qolib qayta uriniladi; urinishlar tugasa status=4 "
              f"(texnik, taslim — rad javob EMAS, --requeue bilan qaytadi).")

    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT count(*), count(DISTINCT tender_id) FROM {T} "
                        f"WHERE deleted_at IS NULL")
            n_tpl, n_tpl_tender = cur.fetchone()
            cur.execute(f"SELECT status, count(*) FROM {T} "
                        f"WHERE deleted_at IS NULL GROUP BY status ORDER BY status")
            tpl_st = dict(cur.fetchall())
            cur.execute(f"SELECT type, count(*) FROM {T} "
                        f"WHERE deleted_at IS NULL GROUP BY type ORDER BY type")
            tpl_turlar = cur.fetchall()
        print()
        print(f"  BUYURTMACHI ETALONLARI ({T}): jami {n_tpl} ta, "
              f"{n_tpl_tender} ta tenderda")
        for s in sorted(tpl_st):
            nom = "tekshirilmagan" if s == 0 else "tayyor"
            print(f"      status={s}  {nom:<14} {tpl_st[s]:>5} ta")
        print(f"      turlar: " + ", ".join(f"{t}={n}" for t, n in tpl_turlar))
    except Exception:
        print(f"\n  «{T}» jadvali topilmadi yoki o'qilmadi.")

    # Shablonsiz qolgan fayllar
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT count(*) FROM {F} j WHERE TRUE{_yashamayotgan('j')} "
                f"AND NOT EXISTS (SELECT 1 FROM {T} t "
                f"  WHERE t.tender_id = j.tender_id AND t.type = j.type "
                f"    AND t.deleted_at IS NULL)")
            yoq = cur.fetchone()[0]
        print()
        if yoq:
            print(f"  DIQQAT: {yoq} ta faylning etaloni «{T}» da YO'Q — "
                  f"ular tekshirilmaydi.")
        else:
            print(f"  Har bir faylning etaloni «{T}» da bor.")
    except Exception:
        pass

    # Xizmat jadvallari
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.jobs_state') IS NOT NULL")
        bor = cur.fetchone()[0]
        n_state = n_log = n_olik = 0
        if bor:
            cur.execute("SELECT count(*), count(*) FILTER (WHERE dead_at IS NOT NULL) "
                        "FROM jobs_state")
            n_state, n_olik = cur.fetchone()
            cur.execute("SELECT to_regclass('public.jobs_validation_log') IS NOT NULL")
            if cur.fetchone()[0]:
                cur.execute("SELECT count(*) FROM jobs_validation_log")
                n_log = cur.fetchone()[0]
    print()
    print(f"  BIZNING XIZMAT JADVALLARIMIZ:")
    if bor:
        print(f"      jobs_state           {n_state:>5} qator  (o'lik: {n_olik})")
        print(f"      jobs_validation_log  {n_log:>5} qator  (audit)")
    else:
        print(f"      YO'Q — `python ishga_tushir.py --tayyorla` ni bajaring")

    print()
    print("  Batafsil ko'rish:  python korish.py --fayllar | --shablonlar | "
          "--tenderlar | --natijalar")


def sxema(conn):
    from app import templates as templates_db
    for jadval in (JOBS_TABLE, templates_db.TEMPLATES_TABLE,
                   "jobs_state", "jobs_validation_log"):
        with conn.cursor() as cur:
            cur.execute(
                "SELECT column_name, data_type, is_nullable, column_default "
                "FROM information_schema.columns WHERE table_name = %s "
                "ORDER BY ordinal_position", (jadval,))
            ust = cur.fetchall()
        sarlavha(f"JADVAL: {jadval}")
        if not ust:
            print("  (jadval topilmadi)")
            continue
        jadval_chiqar(["USTUN", "TIP", "NULL?", "STANDART"],
                      [(u[0], u[1], u[2], u[3] or "") for u in ust],
                      [22, 26, 6, 26])


def fayllar(conn, limit, tender=None, faqat_status=None):
    F = JOBS_TABLE
    pk = _pk()
    shart = f"WHERE TRUE{_yashamayotgan()}"
    args = []
    if tender is not None:
        shart += " AND tender_id = %s"
        args.append(tender)
    if faqat_status is not None:
        shart += " AND status = %s"
        args.append(faqat_status)
    tender_ust = "tender_id"
    guruh = "file_id"

    with conn.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {F} {shart}", args)
        jami = cur.fetchone()[0]
        cur.execute(
            f"SELECT {pk}, {tender_ust}, {guruh}, type, status, send, link, comment "
            f"FROM {F} {shart} ORDER BY {tender_ust}, type, {pk} LIMIT %s",
            args + [limit])
        qatorlar = cur.fetchall()

    bosh = f"ISHTIROKCHI FAYLLARI ({F})"
    if tender is not None:
        bosh += f" — tender {tender}"
    if faqat_status is not None:
        bosh += f" — status={faqat_status} ({STATUS_NOM.get(faqat_status, '')})"
    sarlavha(f"{bosh} — {len(qatorlar)}/{jami}")
    jadval_chiqar(
        ["ID", "TENDER", "GURUH", "TUR", "HOLAT", "SEND", "FAYL"],
        [(r[0], r[1], r[2], r[3], STATUS_NOM.get(r[4], r[4]), r[5], _fayl_nomi(r[6]))
         for r in qatorlar],
        [7, 8, 8, 7, 13, 5, 44])

    izohlilar = [r for r in qatorlar if r[7]]
    if izohlilar:
        print()
        print(f"  IZOHLAR ({len(izohlilar)} ta):")
        for r in izohlilar[:10]:
            print(f"\n    id={r[0]}  {_fayl_nomi(r[6])[:56]}")
            print(f"      {_qisqa(r[7], 300)}")
    if jami > len(qatorlar):
        print(f"\n  ... yana {jami - len(qatorlar)} ta. Ko'proq ko'rish: "
              f"python korish.py --fayllar {jami}")


def shablonlar(conn, limit):
    from app import templates as templates_db
    T = templates_db.TEMPLATES_TABLE
    with conn.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {T} WHERE deleted_at IS NULL")
        jami = cur.fetchone()[0]
        cur.execute(
            f"SELECT id, tender_id, type, status, file_id, link FROM {T} "
            f"WHERE deleted_at IS NULL ORDER BY tender_id, type LIMIT %s", (limit,))
        qatorlar = cur.fetchall()

    sarlavha(f"BUYURTMACHI ETALONLARI ({T}) — {len(qatorlar)}/{jami}")
    jadval_chiqar(
        ["ID", "TENDER", "TUR", "HOLAT", "FILE_ID", "SHABLON FAYLI"],
        [(r[0], r[1], r[2], "tayyor" if r[3] == 1 else "tekshirilmagan",
          r[4], _fayl_nomi(r[5])) for r in qatorlar],
        [6, 8, 8, 15, 10, 44])
    print()
    print("  `status` 0 → etalon hali yuklab ko'rilmagan.")
    print("  Biz uni muvaffaqiyatli o'qiganimizdan keyin 1 ga o'zgartiramiz.")


def tenderlar(conn, limit=25):
    from app import templates as templates_db
    T = templates_db.TEMPLATES_TABLE
    F = JOBS_TABLE
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT j.tender_id,
                   count(*)                                    AS fayl,
                   count(*) FILTER (WHERE j.status = 0)        AS kutmoqda,
                   count(*) FILTER (WHERE j.status = 1)        AS toldirilgan,
                   count(*) FILTER (WHERE j.status = 2)        AS kamchilik,
                   count(*) FILTER (WHERE j.status = 4)        AS texnik,
                   (SELECT count(*) FROM {T} t
                      WHERE t.tender_id = j.tender_id AND t.deleted_at IS NULL) AS etalon
            FROM {F} j
            WHERE TRUE{_yashamayotgan('j')}
            GROUP BY j.tender_id ORDER BY j.tender_id LIMIT %s""", (limit,))
        qatorlar = cur.fetchall()
        cur.execute(f"SELECT count(DISTINCT tender_id) FROM {F} "
                    f"WHERE TRUE{_yashamayotgan()}")
        jami = cur.fetchone()[0]

    sarlavha(f"TENDER BO'YICHA — {len(qatorlar)}/{jami} ta tender")
    jadval_chiqar(
        ["TENDER", "FAYL", "KUTMOQDA", "TO'LDIRILGAN", "KAMCHILIK", "TEXNIK", "ETALON"],
        qatorlar, [8, 6, 10, 14, 11, 7, 7])
    if jami > len(qatorlar):
        print(f"\n  ... yana {jami - len(qatorlar)} ta tender. "
              f"Ko'proq: python korish.py --tenderlar {min(jami, 500)}")


def natijalar(conn, faqat_kamchilik=False):
    F = JOBS_TABLE
    pk = _pk()
    shart = "status = 2" if faqat_kamchilik else "status IN (1, 2)"
    with conn.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {F} WHERE {shart}{_yashamayotgan()}")
        jami = cur.fetchone()[0]

    if not jami:
        sarlavha("TEKSHIRUV NATIJALARI")
        print("  Hali hech qanday fayl tekshirilmagan (hammasi status=0).")
        print("  Boshlash uchun:  python main.py")
        return

    tender_ust = "tender_id"
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {pk}, {tender_ust}, type, status, link, comment FROM {F} "
            f"WHERE {shart}{_yashamayotgan()} ORDER BY status DESC, {pk} LIMIT 40")
        qatorlar = cur.fetchall()

    sarlavha(f"TEKSHIRUV NATIJALARI — {jami} ta")
    for r in qatorlar:
        belgi = "KAMCHILIK" if r[3] == 2 else "TO'LDIRILGAN"
        print(f"\n  [{belgi}]  id={r[0]}  tender={r[1]}  {r[2]}")
        print(f"     {_fayl_nomi(r[4])[:70]}")
        if r[5]:
            print(f"     izoh: {_qisqa(r[5], 300)}")
    if jami > len(qatorlar):
        print(f"\n  ... yana {jami - len(qatorlar)} ta.")


def sql_buyruqlar():
    from app import templates as templates_db
    F, T, pk = JOBS_TABLE, templates_db.TEMPLATES_TABLE, _pk()
    sarlavha("QO'LDA TEKSHIRISH UCHUN SQL")
    print(f"""
  -- 1) Umumiy holat
  SELECT status, count(*) FROM {F} GROUP BY status ORDER BY status;

  -- 2) Kamchilik topilganlari va izohi
  SELECT {pk}, tender_id, type, comment FROM {F}
  WHERE status = 2 ORDER BY {pk} LIMIT 20;

  -- 3) Tender bo'yicha
  SELECT tender_id, count(*) fayl,
         count(*) FILTER (WHERE status = 1) toldirilgan,
         count(*) FILTER (WHERE status = 2) kamchilik
  FROM {F} GROUP BY tender_id ORDER BY tender_id;

  -- 4) Buyurtmachi etalonlari
  SELECT id, tender_id, type, status, link FROM {T}
  WHERE deleted_at IS NULL ORDER BY tender_id, type;

  -- 5) Etaloni YO'Q fayllar (bular tekshirilmaydi)
  SELECT j.{pk}, j.tender_id, j.type FROM {F} j
  WHERE NOT EXISTS (SELECT 1 FROM {T} t
      WHERE t.tender_id = j.tender_id AND t.type = j.type
        AND t.deleted_at IS NULL);

  -- 6) Bizning audit jurnalimiz
  SELECT * FROM jobs_validation_log ORDER BY id DESC LIMIT 20;
""")


# ---------------------------------------------------------------------------

def main():
    _utf8_stdout()
    ap = argparse.ArgumentParser(description="Bazadagi ma'lumotlarni ko'rish (faqat o'qiydi)")
    ap.add_argument("--sxema", action="store_true", help="Jadvallar va ustunlar")
    ap.add_argument("--fayllar", nargs="?", type=int, const=30, metavar="N",
                    help="`files` qatorlari (standart 30 ta)")
    ap.add_argument("--shablonlar", nargs="?", type=int, const=40, metavar="N",
                    help="`templates` qatorlari")
    ap.add_argument("--tenderlar", nargs="?", type=int, const=25, metavar="N",
                    help="Tender bo'yicha guruhlab (standart 25 ta)")
    ap.add_argument("--natijalar", action="store_true", help="Tekshiruvdan o'tganlari")
    ap.add_argument("--kamchilik", action="store_true", help="Faqat status=2 va izohlari")
    ap.add_argument("--tender", type=int, metavar="ID", help="Bitta tenderning fayllari")
    ap.add_argument("--sql", action="store_true", help="Qo'lda tekshirish SQL lari")
    args = ap.parse_args()

    try:
        import psycopg  # noqa: F401
    except ImportError:
        print("XATO: psycopg o'rnatilmagan → python -m pip install \"psycopg[binary]\"")
        return 1

    conn = ulan()
    with conn:
        if args.sql:
            sql_buyruqlar()
        elif args.sxema:
            sxema(conn)
        elif args.shablonlar is not None:
            shablonlar(conn, args.shablonlar)
        elif args.tenderlar is not None:
            tenderlar(conn, args.tenderlar)
        elif args.kamchilik:
            natijalar(conn, faqat_kamchilik=True)
        elif args.natijalar:
            natijalar(conn)
        elif args.tender is not None:
            fayllar(conn, 200, tender=args.tender)
        elif args.fayllar is not None:
            fayllar(conn, args.fayllar)
        else:
            xulosa(conn)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
