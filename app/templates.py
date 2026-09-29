# -*- coding: utf-8 -*-
"""
templates.py — buyurtmachi (etalon) fayllarini BAZADAN olish
============================================================
Etalonlar `templates` jadvalida turadi:

    id         bigserial
    link       varchar     — nisbiy yo'l, FILE_BASE_URL bilan to'ldiriladi
    file_id    bigint
    tender_id  bigint      — BOG'LANISH kaliti
    type       varchar     — excel1 | excel2 | excel3 | loyiha_excel
    status     smallint    — 0 = tekshirilmagan, 1 = tekshirilgan
    deleted_at timestamp   — softDeletes

Ishtirokchi fayli uchun etalon shunday topiladi:

    files.tender_id = templates.tender_id  AND  files.type = templates.type

Bitta etalon o'nlab ishtirokchi fayliga ishlatilgani uchun yuklab olingan
fayl DISKDA KESHLANADI — har safar qayta yuklanmaydi.

Kesh papkasi: ETALON_CACHE_DIR (standart `_etalon_cache`).
(Ilgari `templates_db.py` — ko'chirilgan.)
"""

import hashlib
import os
import re
import shutil
import tempfile
import threading

from app import config
from app import download as _w
from app.db.schema import TEMPLATES_TABLE
from app.log import log
from tender_engine.reader import read_file, ExcelTooLargeError
from tender_engine.roles import detect_role

ETALON_CACHE_DIR = config.shablon().etalon_cache_dir
ETALON_CACHE_MAX_MB = config.shablon().etalon_cache_max_mb


ROLLAR = ("jamlanma", "narxlar", "resurs", "loyiha")

# Platformaning `type` konvensiyasi — struktura bo'yicha aniqlab bo'lmaganda
# ishlatiladigan ZAXIRA. Asosiy usul baribir fayl tuzilmasi: nomlanish
# o'zgarsa ham to'g'ri ishlashi uchun.
TYPE_ROL = {
    "excel1": "jamlanma",
    "excel2": "resurs",
    "excel3": "narxlar",
    "loyiha_excel": "loyiha",     # «5-ILOVA» / SMETA HISOBI (2026-07-31)
}

# {templates.id: {"path":..., "role":..., "link":...}} — jarayon ichidagi kesh
_XOTIRA = {}
_QULF = threading.Lock()


class EtalonYoq(Exception):
    """Shu tender+type uchun `templates` da qator yo'q — TEXNIK holat."""


class EtalonOqilmadi(Exception):
    """Etalon topildi, lekin yuklab/o'qib bo'lmadi — TEXNIK holat."""


# ── KESH KALITI: `templates.id` EMAS, HAVOLA XESHI (2026-09-15) ───────────
#
# NUQSON (2026-08-17 da jonli sodir bo'lgan). Kesh fayli `tpl_<id>.xlsx` deb
# nomlanardi va shu nom topilsa DARHOL ishlatilardi — mazmuni hozirgi
# `templates.link` ga mos kelishi hech qayerda tekshirilmasdi.
#
# Sherik jamoa jadvalni almashtirganda `id` lar 1 dan qayta boshlangan va
# 2 499 shablondan 2 497 tasida keshda BOSHQA TENDERNING fayli turgan edi.
# Tizim buni sezmagan: noto'g'ri etalon bilan solishtirib verdikt yozgan,
# verdikt esa `send=1` bilan muzlagach qaytarilmaydi. Yagona himoya qo'lda
# `--kesh-tozala` bo'lgan, ya'ni runbook intizomi — kod emas.
#
# YECHIM. Kalit `id` dan emas, HAVOLADAN olinadi. Havola — platforma
# yasagan UUID yo'li (`<tender>/<type>/<offer>/<uuid>.xlsx`, bazadagi
# 194 322/194 325 havola shu naqshda), ya'ni ikki xil shablonning havolasi
# hech qachon bir xil bo'lmaydi. `id` qayta ishlatilsa ham kalit o'zgaradi
# va kesh MAJBURAN yangilanadi.
#
# CHEKLOV (halollik uchun): bu «bir xil havola, boshqa mazmun» holatini
# yopmaydi — sherik storage'dagi faylni o'rnida almashtirsa kesh eskirib
# qoladi. Lekin har verdikt bilan etalonning sha256 i `validation_evidence`
# ga yozilgani uchun bunday almashish KEYIN aniqlanadi. 2026-08-17 dagi
# ommaviy nosozlik esa aynan `id` to'qnashuvi edi va u to'liq yopiladi.
_KESH_PREFIKS = "tplh_"          # eski format: `tpl_<id>` — pastda tozalanadi
_ESKI_KESH_RE = re.compile(r"^tpl_\d")


def _kesh_kaliti(link):
    """Kesh fayl nomining barqaror kaliti — HAVOLA xeshidan (16 hex)."""
    return hashlib.sha256(
        str(link or "").strip().encode("utf-8")).hexdigest()[:16]


def _kesh_yoli(tid, link):
    try:
        os.makedirs(ETALON_CACHE_DIR, exist_ok=True)
    except OSError as exc:
        # Sozlama xatosi (masalan systemd `ProtectSystem=strict` papkani
        # faqat-o'qish qilgan). Xom OSError chaqiruvchida TUTILMAYDI va
        # butun navbat to'xtab qolardi — shuning uchun o'z xatomizga
        # o'raymiz, u TEXNIK holat sifatida ishlanadi.
        raise EtalonOqilmadi(
            f"etalon keshi papkasiga yozib bo'lmadi ({ETALON_CACHE_DIR}): "
            f"{exc}") from exc
    nom = _w._link_filename(link or "etalon.xlsx")
    keng = os.path.splitext(nom)[1] or ".xlsx"
    return os.path.join(ETALON_CACHE_DIR,
                        f"{_KESH_PREFIKS}{_kesh_kaliti(link)}{keng}")


_eski_tozalandi = False


def eski_keshni_tozala():
    """Eski `tpl_<id>.*` nomli fayllarni bir marta o'chiradi.

    Yangi kalitga o'tgach ular hech qachon topilmaydi — 4.5 GB yetim fayl
    bo'lib qolardi. Prefiks ataylab BOSHQACHA (`tplh_`): eski va yangi
    formatni aralashtirib yuborish mumkin emas.

    ESLATMA: `korpus/` dagi ayrim eski o'lchov skriptlari `_etalon_cache`
    dan `tpl_<id>.*` ni ZAXIRA manba sifatida o'qiydi (asosiy manbasi —
    muzlatilgan `korpus/etalonlar/`, unga TEGILMAYDI). Shu tozalash halal
    bersa: ETALON_ESKI_KESHNI_SAQLA=1.
    """
    global _eski_tozalandi
    if config.hozir_etalon_eski_keshni_saqla():
        _eski_tozalandi = True
        return 0
    if _eski_tozalandi or not os.path.isdir(ETALON_CACHE_DIR):
        _eski_tozalandi = True
        return 0
    _eski_tozalandi = True
    n = bayt = 0
    try:
        with os.scandir(ETALON_CACHE_DIR) as it:
            for x in it:
                if not x.is_file() or not _ESKI_KESH_RE.match(x.name):
                    continue
                try:
                    hajm = x.stat().st_size
                except OSError:
                    hajm = 0
                if _w._safe_unlink(x.path):
                    n += 1
                    bayt += hajm
    except OSError:
        return n
    if n:
        log(f"[kesh] eski formatdagi {n} ta etalon o'chirildi "
            f"({bayt // (1024 * 1024)} MB) — kalit endi havola xeshidan")
    return n


def _yangi_ishlatildi(yol):
    """Faylning «oxirgi ishlatilgan» vaqtini yangilaydi (LRU uchun).

    Busiz kesh FIFO bo'lib qolardi: eng ko'p ishlatiladigan shablon ham
    eskirib o'chib ketaverardi va qayta-qayta yuklab olinardi.
    """
    try:
        os.utime(yol, None)
    except OSError:
        pass


def kesh_hajmi():
    """Kesh papkasidagi (bayt, fayl_soni)."""
    if not os.path.isdir(ETALON_CACHE_DIR):
        return 0, 0
    bayt = son = 0
    with os.scandir(ETALON_CACHE_DIR) as it:
        for x in it:
            try:
                if x.is_file():
                    bayt += x.stat().st_size
                    son += 1
            except OSError:
                pass
    return bayt, son


def _keshni_qisqartir():
    """Chegaradan oshsa eng eski ishlatilgan fayllarni o'chiradi (LRU)."""
    chegara = ETALON_CACHE_MAX_MB * 1024 * 1024
    if chegara <= 0 or not os.path.isdir(ETALON_CACHE_DIR):
        return 0
    fayllar = []
    jami = 0
    with os.scandir(ETALON_CACHE_DIR) as it:
        for x in it:
            try:
                if x.is_file():
                    st = x.stat()
                    fayllar.append((st.st_mtime, st.st_size, x.path))
                    jami += st.st_size
            except OSError:
                pass
    if jami <= chegara:
        return 0
    # 80% gacha tushiramiz — har safar chegarada turmaslik uchun
    nishon = int(chegara * 0.8)
    fayllar.sort()                     # eng eski ishlatilgani birinchi
    ochirildi = 0
    for _mtime, hajm, yol in fayllar:
        if jami <= nishon:
            break
        if _w._safe_unlink(yol):
            jami -= hajm
            ochirildi += 1
    if ochirildi:
        with _QULF:
            for tid in [k for k, v in _XOTIRA.items()
                        if not os.path.isfile(v.get("path", ""))]:
                _XOTIRA.pop(tid, None)
        log(f"[kesh] {ochirildi} ta eski etalon o'chirildi "
            f"({jami // (1024 * 1024)} MB qoldi)")
    return ochirildi


def qatorni_top(conn, tender_id, typ):
    """`templates` dan (tender_id, type) bo'yicha qatorni qaytaradi yoki None."""
    if tender_id is None or not typ:
        return None
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT id, link, file_id, tender_id, type, status
            FROM {TEMPLATES_TABLE}
            WHERE tender_id = %s AND type = %s AND deleted_at IS NULL
            ORDER BY id DESC
            LIMIT 5
            """, (tender_id, typ))
        qatorlar = cur.fetchall()
    if not qatorlar:
        return None
    r = qatorlar[0]
    # F2 (2026-09-10): bitta tender+type uchun BIR NECHTA shablon bo'lishi
    # mumkin (2026-09-08 da bazada 2 500 shunday juftlik, 2 401 tasi bir xil
    # fayl). TANLOV O'ZGARMAYDI — eng yangi `id` (platforma qayta yuklashi
    # ma'nosida). Farqi: nechta nomzod bo'lgani va qolganlarining id lari
    # qaytariladi — `jobs_worker` buni audit iziga (ETALON_KOP, note) yozadi.
    return {"id": r[0], "link": r[1], "file_id": r[2],
            "tender_id": r[3], "type": r[4], "status": r[5],
            "nomzodlar": len(qatorlar),
            "boshqa_idlar": [x[0] for x in qatorlar[1:]]}


def etalon_ol(conn, tender_id, typ):
    """Etalonni tayyorlaydi: bazadan topadi, yuklab oladi, keshlaydi.

    Returns: {"id","path","role","link","file_id","tender_id","type"}
    Xatolar: EtalonYoq (qator yo'q), EtalonOqilmadi (yuklab/o'qib bo'lmadi).
    Ikkalasi ham TEXNIK — ishtirokchi aybdor emas.
    """
    qator = qatorni_top(conn, tender_id, typ)
    if not qator:
        raise EtalonYoq(
            f"«{TEMPLATES_TABLE}» da tender_id={tender_id} type={typ} uchun "
            f"etalon topilmadi")

    tid = qator["id"]
    # Xotira keshi ham HAVOLA bo'yicha kalitlanadi: `id` qayta ishlatilsa
    # jarayon ichidagi kesh ham eski shablonni qaytarib yuborardi.
    kalit = _kesh_kaliti(qator["link"])
    with _QULF:
        keshda = _XOTIRA.get(kalit)
    if keshda and os.path.isfile(keshda["path"]):
        _yangi_ishlatildi(keshda["path"])
        return {**qator, **keshda}

    eski_keshni_tozala()             # bir marta: `tpl_<id>.*` yetimlari
    yol = _kesh_yoli(tid, qator["link"])
    if os.path.isfile(yol):
        _yangi_ishlatildi(yol)
    else:
        _keshni_qisqartir()          # yuklashdan OLDIN joy bo'shatamiz
        # ATOMIK YUKLASH (2026-09-02): ilgari fayl to'g'ridan-to'g'ri kesh
        # papkasiga, havoladan olingan NOM bilan yuklanardi. Bir nechta
        # worker parallel ishlaganda ikkalasi AYNI faylni ayni yo'lga yozib,
        # yarim yozilgan nusxa o'qilishi mumkin edi (verdikt esa `send`
        # bilan muzlaydi — qaytarib bo'lmaydi). Endi: har jarayon o'z
        # vaqtinchalik papkasiga yuklaydi, so'ng `os.replace` bilan atomik
        # ko'chiradi — yarim fayl hech qachon ko'rinmaydi.
        vaqtinchalik = tempfile.mkdtemp(prefix="et_", dir=ETALON_CACHE_DIR)
        try:
            try:
                yuklangan = _w.download(qator["link"], vaqtinchalik)
            except Exception as exc:
                raise EtalonOqilmadi(
                    f"etalon yuklab olinmadi (templates.id={tid}): {exc}") from exc
            # `download` magic-byte bo'yicha kengaytmani TUZATGAN bo'lishi
            # mumkin (.xlsx nomli fayl aslida .xls bo'lsa) — kesh nomi ham
            # HAQIQIY formatga ergashsin, aks holda `read_file` noto'g'ri
            # o'quvchini tanlab, sog'lom etalonni «o'qilmadi» qilardi.
            haqiqiy_keng = os.path.splitext(yuklangan)[1].lower()
            if haqiqiy_keng and not yol.lower().endswith(haqiqiy_keng):
                yol = os.path.join(ETALON_CACHE_DIR,
                                   f"{_KESH_PREFIKS}{kalit}{haqiqiy_keng}")
            try:
                os.replace(yuklangan, yol)       # ATOMIK
            except OSError:
                # Windows: boshqa jarayon shu faylni O'QIYOTGAN bo'lsa
                # almashtirish rad etiladi (sharing violation). Bunday
                # holatda faylni O'ZIMIZGA XOS nom bilan keshda qoldiramiz —
                # `vaqtinchalik` papka pastda o'chiriladi, shuning uchun
                # unga ishora qilib qolish MUMKIN EMAS (aks holda o'z
                # faylimizni o'chirib, «Truncated file header» olardik).
                zaxira = os.path.join(
                    ETALON_CACHE_DIR,
                    f"{_KESH_PREFIKS}{kalit}.{os.getpid()}"
                    f"{os.path.splitext(yol)[1]}")
                try:
                    os.replace(yuklangan, zaxira)
                    yol = zaxira
                except OSError:
                    # Oxirgi chora: faylni o'z vaqtinchalik papkamizda
                    # qoldiramiz — pastdagi `finally` uni TEGMAYDI.
                    yol = yuklangan
        finally:
            # Faqat FAYL boshqa joyga ko'chirilgan bo'lsa tozalaymiz.
            if os.path.dirname(os.path.abspath(yol)) != os.path.abspath(vaqtinchalik):
                shutil.rmtree(vaqtinchalik, ignore_errors=True)

    try:
        varaqlar = read_file(yol)
    except ExcelTooLargeError as exc:
        # Chegara — DETERMINIK: qayta yuklash yordam bermaydi.
        raise EtalonOqilmadi(f"etalon xavfsizlik chegarasidan oshdi: {exc}") from exc
    except Exception as exc:
        # Keshdagi nusxa buzuq bo'lishi mumkin (masalan parallel worker uni
        # almashtirayotgan payt o'qidik). ILGARI bu yerda umumiy kesh fayli
        # O'CHIRILARDI — bitta o'tkinchi xato boshqa workerlarning faylini
        # ham yo'q qilib, «File is not a zip file» zanjirini keltirib
        # chiqarardi (2026-09-02 da jonli kuzatildi).
        #
        # Endi: XUSUSIY nusxada bir marta qayta urinamiz. Muvaffaqiyatli
        # bo'lsa — kesh eskirgan ekan, yangilaymiz; yana yiqilsa — etalon
        # haqiqatan o'qilmaydi (TEXNIK holat, hech kimga rad yozilmaydi).
        xususiy = tempfile.mkdtemp(prefix="et_qayta_", dir=ETALON_CACHE_DIR)
        try:
            try:
                nusxa = _w.download(qator["link"], xususiy)
                varaqlar = read_file(nusxa)
            except Exception:
                raise EtalonOqilmadi(
                    f"etalon o'qilmadi (templates.id={tid}): {exc}") from exc
            log(f"[etalon] keshdagi nusxa buzuq edi (templates.id={tid}) — "
                f"qayta yuklab tuzatildi", "warning")
            try:
                os.replace(nusxa, yol)
            except OSError:
                yol = nusxa
        finally:
            if os.path.dirname(os.path.abspath(yol)) != os.path.abspath(xususiy):
                shutil.rmtree(xususiy, ignore_errors=True)

    rol = detect_role(varaqlar)
    if rol not in ROLLAR:
        zaxira = TYPE_ROL.get(str(qator["type"]).strip().lower())
        if not zaxira:
            raise EtalonOqilmadi(
                f"etalon roli aniqlanmadi (templates.id={tid}, rol={rol}) — "
                f"shablon jamlanma/narxlar/resurs shakllaridan biriga mos emas")
        log(f"[etalon] templates.id={tid}: tuzilma bo'yicha rol aniqlanmadi, "
            f"«{qator['type']}» turi bo'yicha «{zaxira}» deb olindi", "warning")
        rol = zaxira

    natija = {"path": yol, "role": rol}
    with _QULF:
        _XOTIRA[kalit] = natija
    log(f"[etalon] tender={tender_id} {typ} → templates.id={tid} rol={rol} "
        f"({os.path.getsize(yol) // 1024} KB)")
    return {**qator, **natija}


def tekshirilgan_deb_belgila(conn, template_id):
    """Etalon muvaffaqiyatli yuklab o'qilgach `status = 1` qiladi.

    Faqat status=0 qatorga tegadi — allaqachon 1 bo'lsa qayta yozilmaydi.
    """
    with conn.cursor() as cur:
        cur.execute(
            f"UPDATE {TEMPLATES_TABLE} SET status = 1, updated_at = now() "
            f"WHERE id = %s AND status = 0",
            (template_id,))
        return cur.rowcount > 0


def kutilayotgan_soni(conn):
    """Hali tekshirilmagan (status=0) etalonlar soni."""
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT count(*) FROM {TEMPLATES_TABLE} "
            f"WHERE status = 0 AND deleted_at IS NULL")
        return cur.fetchone()[0]


def hammasini_tayyorla(conn, limit=None):
    """status=0 bo'lgan BARCHA etalonlarni yuklab, o'qib, status=1 qiladi.

    Worker ishga tushganda bir marta chaqiriladi — shunda ishtirokchi
    fayllari kelganda etalon allaqachon keshda bo'ladi.

    Returns: (tayyor, xato)
    """
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT id, tender_id, type FROM {TEMPLATES_TABLE}
            WHERE status = 0 AND deleted_at IS NULL
            ORDER BY id
            {f'LIMIT {int(limit)}' if limit else ''}
            """)
        qatorlar = cur.fetchall()
    if not qatorlar:
        return 0, 0

    log(f"[etalon] {len(qatorlar)} ta yangi etalon tayyorlanmoqda...")
    tayyor = xato = 0
    for tid, tender_id, typ in qatorlar:
        try:
            etalon_ol(conn, tender_id, typ)
            if tekshirilgan_deb_belgila(conn, tid):
                conn.commit()
            tayyor += 1
        except Exception as exc:
            xato += 1
            log(f"[etalon] tayyorlanmadi (id={tid} tender={tender_id} {typ}): "
                f"{exc}", "warning")
    log(f"[etalon] tayyor: {tayyor}, xato: {xato}")
    return tayyor, xato


def keshni_tozala():
    """Yuklab olingan etalon nusxalarini o'chiradi (kesh qayta quriladi)."""
    with _QULF:
        _XOTIRA.clear()
    shutil.rmtree(ETALON_CACHE_DIR, ignore_errors=True)
