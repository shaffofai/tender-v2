# -*- coding: utf-8 -*-
"""yuboruvchi — chiquvchi xabarlarni tender tizimiga yuboruvchi cron.

Manba: `yuborish_navbati` (outbox) — har qator bitta xabar:
    {"file_id": ..., "status": 1|2|3|4, "comment": "..."}
    (4 — texnik, taslim: tekshirib bo'lmadi, rad EMAS; 2026-09-23)
    POST https://api.shaffofxarid.uz/api/integration/ai/set-result   (Basic Auth)

Ikki ish bir aylanishda:
  1) TEXNIK xabarlarni yig'ish — `files.status=4` (taslim bo'lingan, `olik_belgilash`
     qo'ygan) fayllar uchun `status=4` qatori (§7.5, H1/H2/H3 himoyalari bilan)
  2) Navbatni yuborish — 200 → yuborildi; 5xx/timeout → qayta; 4xx → tashlandi

Ishga tushirish:
    python yuboruvchi.py             # bitta aylanish
    python yuboruvchi.py --davomiy   # har CRON_INTERVAL (180 s) da
    python yuboruvchi.py --holat     # navbat holati (faqat o'qiydi)
    python yuboruvchi.py --health    # tiriklik: sozlama + baza + jadval

Muvaffaqiyat mezoni — FAQAT HTTP 200 (buyurtmachi tasdiqladi). Javob tanasi
o'qilmaydi; kelajakda belgi kerak bo'lsa `javob_muvaffaqiyatlimi` ga qo'shiladi.

(Ilgari `yuboruvchi.py` — ko'chirilgan.)
"""

import argparse
import signal
import sys
import time

import psycopg

from app import config
from app.db import DATABASE_URL, schema
from app.db.schema import SxemaXatosi
from app.log import log
from app.sender.cycle import aylanish, hisob_jami
from app.sender.delivery import TENDER_API_LOGIN, TENDER_API_PAROL, TENDER_API_URL
from app.sender.outbox import YUBORISH_TOPLAM, holat, qayta_och
from app.sender.statuses import (
    QABUL_STATUSLAR,
    STATUS_TEXNIK,
    STATUS_XARITA,
    _statuslar,
    status_qabulmi,
    tashqi_status,
)
from app.sender.technical import (
    TEXNIK_TOSHQIN_CHEGARA,
    TEXNIK_YUBORISH,
    _texnik_nomzod_sharti,
)

_S = config.yuboruvchi()
CRON_INTERVAL = _S.cron_interval
YUBORISH_TASHLANDI_CHEGARA = _S.yuborish_tashlandi_chegara


_shutdown = False


def _toxtatish(signum, _frame):
    global _shutdown
    if _shutdown:
        sys.exit(1)
    _shutdown = True
    log(f"[to'xtatish] signal {signum} — joriy aylanish tugagach to'xtaymiz.")


# ---------------------------------------------------------------------------
# Sozlama tekshiruvi — §8.1: https bo'lmasa FATAL
# ---------------------------------------------------------------------------
def sozlamalarni_tekshir():
    """Qaytadi: muammolar ro'yxati (bo'sh = hammasi joyida)."""
    m = []
    if not TENDER_API_URL:
        m.append("TENDER_API_URL yo'q")
    elif not TENDER_API_URL.lower().startswith("https://"):
        # Basic Auth parolni base64 bilan o'raydi — bu SHIFR EMAS. HTTP orqali
        # yuborilsa parol ochiq ketadi. Bundan tashqari o'lchangan (2026-09-23):
        # api.shaffofxarid.uz da 80-port umuman javob bermaydi.
        #
        # Yagona istisno — LOOPBACK (127.0.0.1 / localhost): trafik mashinadan
        # chiqmaydi, mahalliy mock tender bilan uchdan-uchiga sinov uchun kerak.
        # Bu `common.host_ruxsatmi` dagi qoidaning AYNAN o'zi; alohida
        # «bypass» kaliti YO'Q va qo'shilmaydi.
        from urllib.parse import urlparse
        host = (urlparse(TENDER_API_URL).hostname or "").lower()
        if host not in ("127.0.0.1", "localhost", "::1"):
            m.append("TENDER_API_URL faqat https:// bo'lishi mumkin (parol ochiq ketmasin)")
    if not TENDER_API_LOGIN or not TENDER_API_PAROL:
        m.append("TENDER_API_LOGIN / TENDER_API_PAROL yo'q")
    return m


# ---------------------------------------------------------------------------
# 6) Holat / health / CLI
# ---------------------------------------------------------------------------
def health():
    """0 — sog'lom, 1 — muammo. Sozlama + baza + jadval + toshqin holati."""
    muammolar = list(sozlamalarni_tekshir())
    print(f"  tender qabul qiladi: status {','.join(map(str, sorted(QABUL_STATUSLAR)))}"
          + (f"; xarita {STATUS_XARITA}" if STATUS_XARITA else ""))
    if TEXNIK_YUBORISH and not status_qabulmi(tashqi_status(STATUS_TEXNIK)):
        print(f"  eslatma: texnik xabar (status={STATUS_TEXNIK}) yuborilmaydi — "
              f"tender {STATUS_TEXNIK} ni olmaydi")
    try:
        with psycopg.connect(DATABASE_URL, autocommit=True, connect_timeout=10) as c:
            with c.cursor() as cur:
                cur.execute("SELECT to_regclass('yuborish_navbati')")
                if cur.fetchone()[0] is None:
                    muammolar.append("yuborish_navbati jadvali yo'q (jobs_schema.sql)")
                else:
                    cur.execute("SELECT count(*) FROM yuborish_navbati WHERE holat = 3")
                    n3 = cur.fetchone()[0]
                    # Ilgari bu FAQAT chop etilardi — butun navbat tashlansa
                    # ham `--health` YASHIL qolar, Docker konteynerni sog'lom
                    # deb bilardi va hech kim xabar topmasdi. Toshqin himoyasi
                    # (pastda) allaqachon shu naqshda ishlaydi.
                    if n3 > YUBORISH_TASHLANDI_CHEGARA:
                        muammolar.append(
                            f"{n3} ta xabar TASHLANDI (holat=3, chegara "
                            f"{YUBORISH_TASHLANDI_CHEGARA}) — verdiktlar tenderga "
                            f"yetmadi; `python yuboruvchi.py --qayta-och` bilan qaytaring")
                    elif n3:
                        print(f"  ogohlantirish: {n3} ta xabar tashlandi (holat=3)")
            if TEXNIK_YUBORISH:
                try:
                    schema.tekshir(c)
                    with c.cursor() as cur:
                        cur.execute(f"SELECT count(*) {_texnik_nomzod_sharti()}")
                        nomzod = int(cur.fetchone()[0] or 0)
                    if nomzod > TEXNIK_TOSHQIN_CHEGARA:
                        muammolar.append(
                            f"TOSHQIN: {nomzod} ta o'lik fayl kutmoqda "
                            f"(chegara {TEXNIK_TOSHQIN_CHEGARA}) — texnik xabarlar to'xtagan")
                except SxemaXatosi as exc:
                    muammolar.append(f"sxema: {exc}")
    except Exception as exc:
        muammolar.append(f"baza: {exc}")
    for m in muammolar:
        print(f"  MUAMMO: {m}")
    print("  SOG'LOM" if not muammolar else f"  {len(muammolar)} ta muammo")
    return 0 if not muammolar else 1


def main(argv=None):
    p = argparse.ArgumentParser(description="Chiquvchi xabarlarni tenderga yuborish")
    p.add_argument("--davomiy", action="store_true", help="har CRON_INTERVAL da")
    p.add_argument("--holat", action="store_true", help="navbat holati (faqat o'qiydi)")
    p.add_argument("--health", action="store_true")
    p.add_argument("--qayta-och", dest="qayta_och", nargs="?", const="hammasi",
                   metavar="STATUSLAR",
                   help="tashlangan (holat=3) xabarlarni navbatga qaytarish; "
                        "ixtiyoriy: faqat shu statuslar, masalan `3,4`")
    a = p.parse_args(argv)

    if a.health:
        return health()

    muammolar = sozlamalarni_tekshir()
    if muammolar and not (a.holat or a.qayta_och):
        for m in muammolar:
            log(f"[sozlama] {m}", "error")
        return 2                                  # FATAL — chetlab o'tish kaliti yo'q

    signal.signal(signal.SIGINT, _toxtatish)
    signal.signal(signal.SIGTERM, _toxtatish)

    conn = psycopg.connect(DATABASE_URL, connect_timeout=30)
    conn.autocommit = False
    try:
        schema.tekshir(conn)
        conn.rollback()
        if a.qayta_och:
            st = None if a.qayta_och == "hammasi" else _statuslar(a.qayta_och)
            qayta_och(conn, st)
            return 0
        if a.holat:
            holat(conn)
            return 0
        while not _shutdown:
            try:
                h = aylanish(conn)
            except (psycopg.OperationalError, psycopg.InterfaceError) as exc:
                log(f"[baza] uzildi: {exc} — qayta ulanamiz", "warning")
                try:
                    conn.close()
                except Exception:
                    pass
                time.sleep(5)
                conn = psycopg.connect(DATABASE_URL, connect_timeout=30)
                conn.autocommit = False
                schema.tekshir(conn)
                conn.rollback()
                continue
            if any(h[k] for k in ("yuborildi", "xato", "tashlandi", "texnik_yozildi")):
                log(f"[aylanish] yuborildi={h['yuborildi']} qayta={h['xato']} "
                    f"tashlandi={h['tashlandi']} texnik={h['texnik_yozildi']}")
            if not a.davomiy:
                break
            # DRAIN (2026-09-25): to'plam TO'LA qaytdi ⇒ navbatda yana ish bor
            # ⇒ UXLAMAYMIZ. To'plam to'lmadi ⇒ navbat bo'shadi ⇒ uxlaymiz.
            # Bu `main.py` dagi worker sikli bilan bir xil tamoyil: ish bor
            # ekan ishlaymiz, ish yo'q bo'lsagina kutamiz. Ilgari uyqu
            # SHARTSIZ edi — navbatda 4 000 xabar tursa ham 180 s kutilardi
            # (o'lchandi: bekorchilik 99,4%, kechikish o'rtacha 66,5 daqiqa).
            # Xavfsizligi BACKOFF ga tayanadi: xatoli qator `band_until` bilan
            # parkga tushadi, shuning uchun drain uni qayta-qayta olmaydi va
            # urinishlarni bir necha soniyada sarflab yubormaydi.
            ishlandi = hisob_jami(h)
            if ishlandi >= YUBORISH_TOPLAM and not _shutdown:
                continue
            for _ in range(CRON_INTERVAL):
                if _shutdown:
                    break
                time.sleep(1)
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":                        # pragma: no cover
    sys.exit(main())
