# -*- coding: utf-8 -*-
"""`python jobs_worker.py --health [--toliq]` — tiriklik va yozish huquqlari.

(Ilgari `jobs_worker.py` da — ko'chirilgan.)
"""

import psycopg

from app import templates as templates_db
from app.db import DATABASE_URL, safe_dsn, schema
from app.db.schema import JOBS_TABLE, SxemaXatosi, _yashamayotgan, _yuborilmagan
from app.log import log
from app.worker.audit import _siyosat_versiya
from app.worker.gates import STATUS_IDENTICAL
from app.worker.queue import STATUS_TEXNIK, kutayotganlar_soni


def _yozish_huquqi(conn):
    """Verdikt yozish uchun kerak bo'lgan huquqlar bormi (2026-09-14).

    NEGA KERAK. Worker minimal huquqli rol bilan ishlashi kerak
    (`deploy/huquqlar.sql`). Agar rolda bitta ustun yetishmasa — masalan
    `UPDATE files.updated_at` — HAR verdikt «permission denied» bilan
    tugaydi, `_sessiya` uni «worker exception» deb texnik holatga
    o'tkazadi va navbat JIMGINA to'xtaydi. Ilgari `--health` buni
    ko'rmasdi: baza javob berayotgani uchun SOG'LOM deb ko'rsatardi.

    Haqiqiy UPDATE bajarilmaydi — sherik jadvaliga tegmaymiz.
    PostgreSQL ning `has_*_privilege` katalog funksiyalari yetarli va
    hech qanday nojo'ya ta'siri yo'q.

    Qaytaradi: yetishmayotgan huquqlar ro'yxati (bo'sh = hammasi joyida).
    """
    ustunlar = [(JOBS_TABLE, "status"), (JOBS_TABLE, "comment"),
                (JOBS_TABLE, "updated_at")]
    ustunlar += [(templates_db.TEMPLATES_TABLE, "status"),
                 (templates_db.TEMPLATES_TABLE, "updated_at")]

    jadvallar = [(JOBS_TABLE, "SELECT"),
                 (templates_db.TEMPLATES_TABLE, "SELECT"),
                 ("jobs_state", "SELECT"), ("jobs_state", "INSERT"),
                 ("jobs_state", "UPDATE"),
                 ("jobs_validation_log", "INSERT"),
                 ("validation_evidence", "INSERT")]
    # Outbox (2026-09-23): huquq yo'q bo'lsa HECH BIR verdikt tenderga
    # yetkazilmaydi — `natijani_yoz` ichidagi INSERT «permission denied»
    # bilan yiqiladi va butun verdikt tranzaksiyasi qaytariladi.
    jadvallar += [("yuborish_navbati", "SELECT"),
                  ("yuborish_navbati", "INSERT"),
                  ("yuborish_navbati", "UPDATE")]

    # Avval jadval BORMI — huquqni so'rashdan OLDIN. `has_table_privilege`
    # mavjud bo'lmagan jadvalda istisno ko'taradi va PostgreSQL jurnalini har
    # tekshiruvda `relation "..." does not exist` bilan to'ldiradi (2026-09-24
    # da serverda shunday bo'ldi). `to_regclass` NULL qaytaradi — xato emas.
    yetishmagan = []
    yoq = set()
    for jadval in sorted({j for j, _ in jadvallar} | {j for j, _ in ustunlar}):
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT to_regclass(%s)", (jadval,))
                if cur.fetchone()[0] is None:
                    yoq.add(jadval)
        except Exception:
            conn.rollback()
    for jadval in sorted(yoq):
        # Eng ko'p uchraydigan holat: deploy'da `--init-db` qadami o'tkazib
        # yuborilgan (`validation_evidence` FAQAT shu yerda yaratiladi).
        # Buyruqni ATAYLAB xabarga yozamiz — operator izlab yurmasin.
        yetishmagan.append(
            f"{jadval} jadvali YO'Q → `python jobs_worker.py --init-db`"
            if jadval == "validation_evidence"
            else f"{jadval} jadvali YO'Q (sxema qo'llanmagan)")

    # Yo'q jadvallarga huquq so'ramaymiz — sabab allaqachon yozildi.
    ustunlar = [(j, u) for j, u in ustunlar if j not in yoq]
    jadvallar = [(j, i) for j, i in jadvallar if j not in yoq]

    for jadval, ustun in ustunlar:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT has_column_privilege("
                            "current_user, %s, %s, 'UPDATE')", (jadval, ustun))
                if not cur.fetchone()[0]:
                    yetishmagan.append(f"UPDATE {jadval}.{ustun}")
        except Exception:
            # Ustun/jadval yo'q yoki katalog o'qilmadi — huquq masalasi emas,
            # sxema masalasi; u boshqa joyda (`schema.tekshir`) chiqadi.
            conn.rollback()
    for jadval, imtiyoz in jadvallar:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT has_table_privilege(current_user, %s, %s)",
                            (jadval, imtiyoz))
                if not cur.fetchone()[0]:
                    yetishmagan.append(f"{imtiyoz} {jadval}")
        except Exception:
            conn.rollback()
            yetishmagan.append(f"{imtiyoz} {jadval} (jadval topilmadi)")
    return yetishmagan


def health(conn=None, toliq=False):
    """Tizim sog'lomligini tekshiradi. exit 0 = sog'lom, 1 = muammo.

        python jobs_worker.py --health          # TIRIKLIK (konteyner uchun)
        python jobs_worker.py --health --toliq  # + navbat holati (odam uchun)

    TIRIKLIK tekshiruvi FAQAT quyidagilarga qaraydi:
      • baza javob beryaptimi
      • jadval tuzilmasi joyidami
      • etalon manbai bormi

    Navbatning uzunligi va o'lik qatorlar soni TIRIKLIKKA TA'SIR QILMAYDI.
    Sabab: 168 000 qatorli navbat normal ish holati — agar u «nosog'lom»
    deb hisoblansa, Docker konteynerni cheksiz qayta ishga tushiraveradi va
    ish umuman bitmaydi. Bunday ogohlantirishlar faqat `--toliq` da chiqadi.
    """
    muammolar = []      # TIRIKLIKKA ta'sir qiladi (exit 1)
    ogohlar = []        # faqat ma'lumot uchun
    satrlar = []

    yopish = False
    try:
        if conn is None:
            conn = psycopg.connect(DATABASE_URL, autocommit=True, connect_timeout=10)
            yopish = True
        schema.tekshir(conn)
        satrlar.append(f"baza        : {safe_dsn()}  jadval={JOBS_TABLE}")

        # Hukm/audit qatlami (tender_engine — endi paketning majburiy qismi)
        satrlar.append(f"hukm qatlami: faol "
                       f"(siyosat={_siyosat_versiya() or 'belgilanmagan'})")

        # Etalon manbai — bazadagi `templates`
        n_tpl = 0
        try:
            with conn.cursor() as cur:
                cur.execute(f"SELECT count(*) FROM {templates_db.TEMPLATES_TABLE} "
                            f"WHERE deleted_at IS NULL")
                n_tpl = cur.fetchone()[0]
        except Exception as exc:
            conn.rollback()
            ogohlar.append(f"«{templates_db.TEMPLATES_TABLE}» o'qilmadi: "
                           f"{type(exc).__name__}")
        satrlar.append(f"etalon      : baza={n_tpl} ta")
        if not n_tpl:
            # `templates` O'QILDI, lekin hali bo'sh — bu YANGI o'rnatishning
            # normal holati (sherik shablonlarni keyinroq yozadi), kod nosoz
            # emas. Ilgari bu TIRIKLIK muammosi edi va bo'sh bazada konteyner
            # doim «unhealthy» ko'rinardi (2026-09-22 da Docker sinovida
            # topildi). Jadval umuman o'qilmasa — yuqorida ogohlantirish bor,
            # navbat bo'lsa esa pastda haqiqiy muammo sifatida chiqadi.
            xabar = (f"«{templates_db.TEMPLATES_TABLE}» bo'sh — shablon hali "
                     f"yuklanmagan (yangi o'rnatishda normal)")
            try:
                with conn.cursor() as cur:
                    cur.execute(f"SELECT count(*) FROM {JOBS_TABLE} WHERE status = 0 "
                                f"AND {_yuborilmagan()}{_yashamayotgan()}")
                    kutayotgan = cur.fetchone()[0]
            except Exception:
                conn.rollback()
                kutayotgan = 0
            if kutayotgan:
                muammolar.append(f"etalon manbai YO'Q, lekin navbatda {kutayotgan} ta "
                                 f"fayl kutmoqda — ular tekshirilmaydi")
            else:
                ogohlar.append(xabar)

        # Yozish huquqlari — yetishmasa navbat JIMGINA to'xtaydi (2026-09-14)
        yoq = _yozish_huquqi(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT current_user, "
                        "COALESCE((SELECT rolsuper FROM pg_roles "
                        "          WHERE rolname = current_user), false)")
            kim, super_mi = cur.fetchone()
        satrlar.append(f"huquqlar    : {kim}"
                       + ("  [SUPERUSER]" if super_mi else "")
                       + ("  yozish: OK" if not yoq else ""))
        if yoq:
            muammolar.append("yozish huquqi yetishmaydi: " + ", ".join(yoq)
                             + "  → deploy/huquqlar.sql")
        if super_mi:
            ogohlar.append(
                f"«{kim}» SUPERUSER — audit jadvalining append-only himoyasi "
                f"chetlab o'tilishi mumkin; minimal huquqli rolga o'ting "
                f"(deploy/huquqlar.sql)")

        if toliq:
            with conn.cursor() as cur:
                cur.execute(f"SELECT status, count(*) FROM {JOBS_TABLE} "
                            f"WHERE TRUE{_yashamayotgan()} GROUP BY status")
                h = dict(cur.fetchall())
                cur.execute("SELECT count(*) FROM jobs_state WHERE dead_at IS NOT NULL")
                olik = cur.fetchone()[0]
            kutayotgan, eng_eski = kutayotganlar_soni(conn)
            satrlar.append(f"navbat      : kutilmoqda={h.get(0, 0)} "
                           f"to'ldirilgan={h.get(1, 0)} kamchilik={h.get(2, 0)} "
                           f"aynan_nusxa={h.get(STATUS_IDENTICAL, 0)} "
                           f"texnik={h.get(STATUS_TEXNIK, 0)}")
            if kutayotgan:
                satrlar.append(f"shablon kutmoqda: {kutayotgan} ta fayl "
                               f"(eng eskisi {eng_eski:%Y-%m-%d %H:%M})")
            satrlar.append(f"o'lik       : {olik}")
            if olik:
                ogohlar.append(f"{olik} ta qator qayta ishlanmagan "
                               f"(python jobs_worker.py --dead-letters)")
    except SxemaXatosi as exc:
        muammolar.append(f"sxema: {exc}")
    except Exception as exc:
        muammolar.append(f"bazaga ulanib bo'lmadi: {type(exc).__name__}: {exc}")
    finally:
        if yopish and conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    for s in satrlar:
        log("  " + s)
    for o in ogohlar:
        log(f"  [eslatma] {o}", "warning")
    if muammolar:
        for m in muammolar:
            log(f"  [MUAMMO] {m}", "error")
        log("HOLAT: NOSOG'LOM")
        return 1
    log("HOLAT: SOG'LOM")
    return 0
