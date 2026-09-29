# -*- coding: utf-8 -*-
"""Yuklab olish — himoyalangan `download()`, host oq ro'yxati, fayl tozalash.

Himoyalar: hajm (MAX_DOWNLOAD_MB), umumiy vaqt (DOWNLOAD_TOTAL_TIMEOUT),
Excel imzosi (magic bytes), faqat 404/410 «umidsiz», yo'naltirishdan KEYINGI
host ham oq ro'yxatda bo'lishi shart. Sozlamalar — `app/config.py`.

(Ilgari `common.py` da — ko'chirilgan.)
"""

import json
import os
import re
import time
from urllib.parse import urlparse

import httpx

from app import config

_S = config.yuklash()
DOWNLOAD_DIR = _S.download_dir
FILE_BASE_URL = _S.file_base_url
MAX_DOWNLOAD_MB = _S.max_download_mb
MAX_DOWNLOAD_BYTES = MAX_DOWNLOAD_MB * 1024 * 1024
DOWNLOAD_TIMEOUT = _S.download_timeout
DOWNLOAD_RETRIES = _S.download_retries
DOWNLOAD_VERIFY_TLS = _S.download_verify_tls
DOWNLOAD_TOTAL_TIMEOUT = _S.download_total_timeout


_ABS_URL_RE = re.compile(r"^[a-z][a-z0-9+.-]*://", re.I)
_WIN_YOL_RE = re.compile(r"^([A-Za-z]:[\\/]|\\\\)")      # C:\... yoki \\server\share


# ---------------------------------------------------------------------------
# Host oq ro'yxati (allowlist) — SSRF himoyasi
# ---------------------------------------------------------------------------
# Havolani endi TENDER TIZIMI beradi (API integratsiyasi, 2026-09-23), ya'ni u
# tashqi kirish. Ikki joyda tekshiriladi:
#   (a) `api_server` — kiruvchi `link` bazaga yozilishidan oldin
#   (b) SHU YERDA — yuklab olishda, YO'NALTIRISHDAN KEYINGI yakuniy URL
#
# (b) nega shart: mijoz `follow_redirects=True` bilan ochiladi. Ruxsat etilgan
# host 302 bilan ichki manzilga (169.254.169.254 — bulut metadata, 10.x.x.x —
# ichki tarmoq) yo'naltirsa, (a) buni TUTMAYDI va worker so'zsiz o'sha yerga
# boradi. Shuning uchun yakuniy `resp.url` ham tekshiriladi.
#
# Standart XAVFSIZ tomonga: ro'yxat bo'sh qoldirilmagan. Mahalliy sinov uchun
# (`mock_server.py`, `jobs_seed.py --http http://127.0.0.1:8200`) host qo'shing:
#   RUXSAT_HOSTLAR=apisitender.mc.uz,127.0.0.1
# Butunlay o'chirish: RUXSAT_HOSTLAR=*  (ishlab chiqarishda QILMANG)
RUXSAT_HOSTLAR = _S.ruxsat_hostlar


def host_ruxsatmi(url):
    """(ok, sabab) — URL sxemasi va hosti ruxsat etilganmi.

    Faqat `https` (va oq ro'yxatda aniq ko'rsatilgan bo'lsa `http`) qabul
    qilinadi; `file://`, `ftp://` va h.k. rad etiladi.
    """
    if "*" in RUXSAT_HOSTLAR:
        return True, ""
    try:
        p = urlparse(str(url))
    except (ValueError, TypeError):
        return False, "havolani tahlil qilib bo'lmadi"
    sxema = (p.scheme or "").lower()
    if sxema not in ("http", "https"):
        return False, f"faqat https ruxsat etilgan (kelgani: {sxema or 'sxemasiz'})"
    host = (p.hostname or "").lower()
    if not host:
        return False, "havolada host yo'q"
    if host not in RUXSAT_HOSTLAR:
        return False, f"host ruxsat etilmagan: {host}"
    if sxema == "http" and host not in ("127.0.0.1", "localhost", "::1"):
        return False, "faqat https ruxsat etilgan (http kelgan)"
    return True, ""


def _sarlavhalar():
    """DOWNLOAD_HEADERS (JSON) + standart User-Agent."""
    h = {
        "User-Agent": config.hozir_download_user_agent(),
        "Accept": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,"
                   "application/vnd.ms-excel,application/octet-stream,*/*"),
    }
    xom = config.hozir_download_headers()
    if xom:
        try:
            qo = json.loads(xom)
            if isinstance(qo, dict):
                h.update({str(k): str(v) for k, v in qo.items()})
        except json.JSONDecodeError:
            pass      # noto'g'ri JSON — standart sarlavhalar bilan davom etamiz
    return h


# ---------------------------------------------------------------------------
# Yuklab olish
# ---------------------------------------------------------------------------

class PermanentDownloadError(Exception):
    """Qayta urinish foydasiz xato (404/410, noto'g'ri format, hajm oshgan)."""


# Excel fayllarining boshlanish imzosi (magic bytes)
_XLSX_MAGIC = b"PK\x03\x04"                       # .xlsx = ZIP konteyner
_XLS_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # .xls  = OLE2 konteyner

# Faqat shu kodlar «umidsiz» — qolgan barcha 4xx (401/403/408/429) va 5xx
# QAYTA URINILADI. 403 ko'pincha vaqtinchalik: WAF, muddati o'tgan imzo,
# token yangilanishi. Ularni darhol o'lik qilish butun navbatni yo'q qilardi.
_UMIDSIZ_HTTP = {404, 410}


def _excel_ext(path):
    """Fayl mazmuniga qarab haqiqiy kengaytmani qaytaradi ('.xlsx'/'.xls'),
    Excel bo'lmasa None. (HTML xato sahifasini ajratish uchun ham.)"""
    try:
        with open(path, "rb") as fh:
            head = fh.read(8)
    except OSError:
        return None
    if head.startswith(_XLSX_MAGIC):
        return ".xlsx"
    if head.startswith(_XLS_MAGIC):
        return ".xls"
    return None


def _looks_like_excel(path):
    return _excel_ext(path) is not None


def _link_filename(link):
    """Havoladan fayl nomini ajratadi — query string (?token=...) tashlanadi.

    Kengaytma bo'lmasa `.xlsx` qo'shiladi; haqiqiy format yuklab olingandan
    keyin magic-byte bo'yicha aniqlanib, kerak bo'lsa tuzatiladi.
    """
    nom = os.path.basename(link.split("?")[0].split("#")[0]).strip()
    if not nom:
        return "download.xlsx"
    if not nom.lower().endswith((".xls", ".xlsx")):
        nom += ".xlsx"
    return nom


def toliq_havola(link):
    """Nisbiy havolani to'liq URL ga aylantiradi.

    Bazada `link` ikki ko'rinishda bo'lishi mumkin:
      • to'liq URL   — https://apisitender.mc.uz/storage/8638/...  (o'zgarmaydi)
      • nisbiy yo'l  — 8638/excel2/9518/fayl.xls  → FILE_BASE_URL qo'shiladi

    Lokal fayl yo'llari (sinov uchun) ham tegilmaydi.
    """
    if not link:
        return link
    h = str(link).strip()
    if _ABS_URL_RE.match(h):             # allaqachon to'liq URL (http/https/file)
        return h
    if os.path.isfile(h):                # haqiqatan mavjud lokal fayl (sinov)
        return h
    if _WIN_YOL_RE.match(h):             # C:\... yoki \\server\share
        return h
    if not FILE_BASE_URL:
        return h                         # baza URL berilmagan — o'zgartirmaymiz
    # Eslatma: «/8638/...» ham NISBIY hisoblanadi — Windows'da `isabs` unga
    # True qaytaradi, shuning uchun bu yerda `isabs` ishlatilmaydi.
    return FILE_BASE_URL.rstrip("/") + "/" + h.lstrip("/")


def _bosh_qism(dest, n=200):
    """Xato xabariga qo'shish uchun faylning boshini o'qiydi (HTML challenge
    kabi holatlarni ajratish oson bo'lsin)."""
    try:
        with open(dest, "rb") as fh:
            return fh.read(n).decode("utf-8", "replace").replace("\n", " ").strip()
    except OSError:
        return ""


def download(link, dest_dir):
    """Havoladan faylni yuklab oladi. Muvaffaqiyat: local path. Xato: Exception.

    Himoyalar:
      • MAX_DOWNLOAD_MB dan katta javob rad etiladi (oqim bo'ylab)
      • DOWNLOAD_TOTAL_TIMEOUT — butun yuklab olishga umumiy chegara
      • javob Excel konteynerimi — magic-byte bilan tekshiriladi
      • DOWNLOAD_HEADERS orqali Authorization/Referer yuborish mumkin
      • faqat 404/410 «umidsiz»; 401/403/429/5xx qayta uriniladi

    `link` nisbiy bo'lsa (`8638/excel2/...`) FILE_BASE_URL qo'shiladi.

    Lokal test uchun: `link` mavjud lokal fayl yo'li yoki file:// bo'lsa,
    httpx o'rniga to'g'ridan-to'g'ri o'sha fayl ishlatiladi.
    """
    link = toliq_havola(link)
    local = link[len("file:///"):] if link.startswith("file:///") else (
        link[len("file://"):] if link.startswith("file://") else link)
    if os.path.isfile(local):
        return local

    # Oq ro'yxat (a): so'rov YUBORILMASDAN oldin. Bu doimiy xato — qayta
    # urinish foyda bermaydi, shuning uchun `PermanentDownloadError`.
    ok, sabab = host_ruxsatmi(link)
    if not ok:
        raise PermanentDownloadError(f"havola rad etildi — {sabab}")

    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, _link_filename(link))
    sarlavhalar = _sarlavhalar()
    oxirgi_xato = None

    for urinish in range(1, DOWNLOAD_RETRIES + 1):
        boshlandi = time.monotonic()
        try:
            with httpx.Client(timeout=DOWNLOAD_TIMEOUT, follow_redirects=True,
                              headers=sarlavhalar, verify=DOWNLOAD_VERIFY_TLS) as client:
                with client.stream("GET", link) as resp:
                    # Oq ro'yxat (b): YO'NALTIRISHDAN KEYINGI yakuniy manzil.
                    # `follow_redirects=True` bo'lgani uchun ruxsat etilgan host
                    # bizni ichki tarmoqqa (169.254.169.254, 10.x.x.x) olib
                    # ketishi mumkin edi — (a) tekshiruvi buni ko'rmaydi.
                    ok, sabab = host_ruxsatmi(resp.url)
                    if not ok:
                        raise PermanentDownloadError(
                            f"yo'naltirish rad etildi — {sabab}")
                    if resp.status_code in _UMIDSIZ_HTTP:
                        raise PermanentDownloadError(
                            f"havola mavjud emas (HTTP {resp.status_code})")
                    if resp.status_code >= 400:
                        # 401/403/429/5xx — vaqtinchalik deb hisoblaymiz
                        raise RuntimeError(
                            f"HTTP {resp.status_code} — qayta uriniladi")

                    e_len = resp.headers.get("content-length")
                    if e_len and e_len.isdigit() and int(e_len) > MAX_DOWNLOAD_BYTES:
                        raise PermanentDownloadError(
                            f"fayl juda katta ({int(e_len) // 1048576} MB > {MAX_DOWNLOAD_MB} MB)")

                    jami = 0
                    with open(dest, "wb") as fh:
                        for bolak in resp.iter_bytes(65536):
                            jami += len(bolak)
                            if jami > MAX_DOWNLOAD_BYTES:
                                raise PermanentDownloadError(
                                    f"fayl juda katta (> {MAX_DOWNLOAD_MB} MB)")
                            if time.monotonic() - boshlandi > DOWNLOAD_TOTAL_TIMEOUT:
                                raise RuntimeError(
                                    f"yuklab olish {DOWNLOAD_TOTAL_TIMEOUT} soniyadan oshdi")
                            fh.write(bolak)

            if jami == 0:
                raise PermanentDownloadError("bo'sh fayl yuklab olindi (0 bayt)")

            haqiqiy = _excel_ext(dest)
            if haqiqiy is None:
                bosh = _bosh_qism(dest)
                raise PermanentDownloadError(
                    "yuklab olingan ma'lumot Excel fayli emas "
                    f"(javob boshi: {bosh[:120]!r})")

            # Kengaytma haqiqiy formatga mos bo'lmasa tuzatamiz: read_file
            # kengaytma bo'yicha o'quvchi tanlaydi, mos kelmasa hujjat aybsiz
            # bo'lsa ham "o'qib bo'lmadi" (status 2) bo'lib qolardi.
            if not dest.lower().endswith(haqiqiy):
                yangi = os.path.splitext(dest)[0] + haqiqiy
                try:
                    if os.path.abspath(yangi) != os.path.abspath(dest):
                        _safe_unlink(yangi)
                        os.replace(dest, yangi)
                    dest = yangi
                except OSError:
                    pass
            return dest

        except PermanentDownloadError:
            _safe_unlink(dest)
            raise
        except Exception as exc:      # tarmoq / 401 / 403 / 5xx / timeout
            _safe_unlink(dest)
            oxirgi_xato = exc
            if urinish < DOWNLOAD_RETRIES:
                time.sleep(min(2 ** urinish, 10))
    raise RuntimeError(
        f"Yuklab olishda xato ({DOWNLOAD_RETRIES} urinish): {oxirgi_xato}")


# ---------------------------------------------------------------------------
# Fayl tozalash
# ---------------------------------------------------------------------------

def _safe_unlink(path):
    """Faylni o'chiradi. True — o'chirildi, False — yo'q edi yoki o'chmadi.

    QAYTISH QIYMATI MUHIM (2026-09-14). `templates_db._keshni_qisqartir`
    unga qarab o'chirilgan hajmni ayiradi:

        if _w._safe_unlink(yol):
            jami -= hajm

    Ilgari funksiya hech narsa qaytarmagani uchun bu shart HAR DOIM yolg'on
    bo'lardi — `jami` kamaymas, `break` hech qachon ishlamas edi va LRU
    sikli butun keshni o'chirib chiqardi (`ochirildi` 0 qolgani uchun log
    ham yozilmasdi). Natijada kesh chegaraga yetganda 80% gacha emas,
    NOLDAN boshlanar va 36 000 dan ortiq shablon qayta yuklanardi.
    """
    try:
        if path and os.path.isfile(path):
            os.remove(path)
            return True
    except OSError:
        pass
    return False


def _prune_empty_dir(path):
    try:
        if path and os.path.isdir(path) and not os.listdir(path):
            os.rmdir(path)
    except OSError:
        pass


def _cleanup_downloads(paths, work_dir=None):
    """Qayta ishlangandan keyin YUKLAB OLINGAN fayllarni o'chiradi.
    Lokal fixture fayllar (DOWNLOAD_DIR dan tashqarida) tegilmaydi."""
    root = os.path.abspath(DOWNLOAD_DIR) + os.sep
    for p in paths:
        try:
            if p and (os.path.abspath(p) + os.sep).startswith(root):
                _safe_unlink(p)
        except OSError:
            pass
    if work_dir:
        _prune_empty_dir(work_dir)
        _prune_empty_dir(os.path.dirname(work_dir))
