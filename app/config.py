# -*- coding: utf-8 -*-
"""Sozlamalar — xizmat o'qiydigan HAR BIR muhit o'zgaruvchisi shu yerda.

Ilgari sozlamalar o'nlab joyda, modul yuklanayotganda `os.environ` dan
o'qilardi. Endi bitta manba: har guruh (baza, worker, yuklab olish, shablon,
API, yuboruvchi, jurnal) o'z funksiyasi orqali BIR MARTA o'qiladi va tekshiriladi.

Guruhlar ATAYLAB alohida va dangasa (lazy): worker yuboruvchi sozlamasini
o'qimaydi. Aks holda bitta `.env` dagi yuboruvchi xatosi (masalan man etilgan
`TENDER_STATUS_XARITA`) worker va API ni ham yiqitardi.

Har qiymat ESKI KODDAGI ifoda bilan AYNAN o'qiladi (standart qiymat, `strip`,
«rost» so'zlar ro'yxati) — xulq o'zgarmasin. Farqi faqat: noto'g'ri son
traceback o'rniga aniq xabar bilan to'xtatadi (chiqish kodi bir xil: 1).

Ish vaqtida (har chaqiruvda) o'qiladiganlar — pastdagi `hozir_*` funksiyalari;
eski kodda ham ular har chaqiruvda o'qilardi.

`tender_engine` o'z xavfsizlik chegaralarini (MAX_SHEET_ROWS, TIKLASH_* va
h.k.) o'zi o'qiydi — dvigatel `app/` ni bilmaydi. Ro'yxati `.env.example` da.
"""

import functools
import os
from dataclasses import dataclass

try:                                    # .env ixtiyoriy — muhit o'zgaruvchisi ham bo'ladi
    from dotenv import load_dotenv
except ImportError:                     # pragma: no cover
    load_dotenv = None

#: Loyiha ildizi. Nisbiy yo'llar (DOWNLOAD_DIR, ETALON_CACHE_DIR, LOG_FILE)
#: CWD ga emas, SHU papkaga nisbatan hal qilinadi — systemd/cron ostida
#: (CWD='/') yo'llar boshqa joyga ishora qilib, jimgina bo'sh ishlamasin.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Muhitdagi qiymat USTUN — `.env` faqat yo'qlarini to'ldiradi (Docker'da
# `.env` rasmga tushmaydi, qiymatlar compose orqali keladi).
if load_dotenv is not None:
    load_dotenv(os.path.join(ROOT, ".env"))


class SozlamaXatosi(SystemExit):
    """Noto'g'ri sozlama — xizmat ishga tushmaydi (chiqish kodi 1)."""


def yol(qiymat, standart):
    """Nisbiy yo'lni loyiha ildiziga nisbatan hal qiladi (bo'sh → standart)."""
    y = qiymat or standart
    return y if os.path.isabs(y) else os.path.normpath(os.path.join(ROOT, y))


def _son(nom, standart):
    xom = os.environ.get(nom, standart)
    try:
        return int(xom)
    except ValueError:
        raise SozlamaXatosi(f"[sozlama] {nom}={xom!r} — butun son bo'lishi kerak") from None


def _matn(nom, standart=""):
    return os.environ.get(nom, standart).strip()


def tozala():
    """Keshlangan sozlamalarni tashlaydi (testlar muhitni o'zgartirgach)."""
    for f in (baza, worker, yuklash, shablon, api, yuboruvchi, migratsiya, jurnal):
        f.cache_clear()


# ---------------------------------------------------------------------------
# Baza
# ---------------------------------------------------------------------------

def build_dsn():
    """DATABASE_URL yoki DB_* bo'laklaridan ulanish satrini yasaydi."""
    dsn = os.environ.get("DATABASE_URL")
    if dsn:
        return dsn
    host = os.environ.get("DB_HOST", "127.0.0.1")
    port = os.environ.get("DB_PORT", "5432")
    baza = os.environ.get("DB_DATABASE", "postgres")
    user = os.environ.get("DB_USERNAME", "postgres")
    parol = os.environ.get("DB_PASSWORD", "")
    return f"postgresql://{user}:{parol}@{host}:{port}/{baza}"


#: Olib tashlangan sozlamalar. Baza endi BIZNIKI va sxemani migratsiyalar
#: yaratadi, shuning uchun jadval/ustun nomlari qat'iy (`app/db/schema.py`).
#: Eski `.env` dagi MOS qiymat (masalan JOBS_TABLE=files) xato emas; BOSHQA
#: qiymat esa endi e'tiborsiz qolmasin — xizmat aniq sabab bilan to'xtaydi.
_QATIY = {
    "JOBS_TABLE": ("files",),
    "TEMPLATES_TABLE": ("templates",),
    "JOBS_PK": ("id",),
    "JOBS_GROUP_COL": ("file_id",),
}


@dataclass(frozen=True)
class Baza:
    database_url: str


@functools.lru_cache(maxsize=None)
def baza():
    for nom, ruxsat in _QATIY.items():
        q = os.environ.get(nom, "").strip()
        if q and q not in ruxsat:
            raise SozlamaXatosi(
                f"[sozlama] {nom}={q!r} endi sozlanmaydi — sxema qat'iy "
                f"({ruxsat[0]}); o'zgaruvchini .env dan olib tashlang")
    # Diskdagi etalon zaxirasi (etalon/<lot>/<id>-nom.xlsx) olib tashlandi:
    # fayl nomidagi raqam bo'yicha TASODIFIY mos kelib, boshqa tenderning
    # shabloni bilan yolg'on verdikt berishi mumkin edi. Etalon FAQAT
    # `templates` jadvalidan olinadi.
    if _matn("ETALON_DISK_FALLBACK", "0") in ("1", "true", "yes"):
        raise SozlamaXatosi(
            "[sozlama] ETALON_DISK_FALLBACK olib tashlangan — etalon faqat "
            "`templates` jadvalidan olinadi; o'zgaruvchini .env dan olib tashlang")
    return Baza(database_url=build_dsn())


# ---------------------------------------------------------------------------
# Worker (tekshiruv navbati)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Worker:
    poll_interval: int
    lease_minutes: int
    max_attempts: int
    retry_base_seconds: int
    retry_max_seconds: int
    reconnect_max_wait: int
    etalon_kutish_interval: int
    etalon_kutish_max_soat: int
    discovery_interval: int


@functools.lru_cache(maxsize=None)
def worker():
    return Worker(
        poll_interval=_son("POLL_INTERVAL", "5"),
        # 2026-09-25: 15 → 30. `MAX_DOWNLOAD_MB` 50 dan 900 ga ko'tarildi, ya'ni
        # bitta fayl endi 10 daqiqagacha yuklanib, keyin yana ochilishi/
        # tekshirilishi mumkin. Lease shu budjetdan KATTA bo'lishi shart, aks
        # holda ish tugagunicha claim muddati o'tadi va natija «claim boshqada»
        # bilan tashlab yuboriladi — 2026-09-02 dagi livelock aynan shunday
        # yuzaga kelgan edi.
        lease_minutes=_son("LEASE_MINUTES", "30"),
        max_attempts=_son("MAX_ATTEMPTS", "5"),
        retry_base_seconds=_son("RETRY_BASE_SECONDS", "60"),
        retry_max_seconds=_son("RETRY_MAX_SECONDS", "900"),
        reconnect_max_wait=_son("RECONNECT_MAX_WAIT", "60"),
        # F1: «shablon kutilmoqda» (2026-09-08). Ishtirokchi fayli buyurtmachi
        # shablonidan OLDIN kelsa, u xato emas — kutish holati. Fayl o'lmaydi
        # va urinish hisobi sarflanmaydi; shablon `templates` ga tushgan zahoti
        # `shablon_kelganini_tekshir` uni uyg'otadi. Chegara: shuncha soat
        # kutilsa — o'lik ro'yxatiga (odam ko'rsin), status baribir 0 qoladi.
        etalon_kutish_interval=_son("ETALON_KUTISH_INTERVAL", "600"),   # soniya
        etalon_kutish_max_soat=_son("ETALON_KUTISH_MAX_SOAT", "72"),
        # To'liq kashfiyot skani shuncha soniyada bir marta (`kashf_qil`).
        discovery_interval=_son("DISCOVERY_INTERVAL", "300"),
    )


# ---------------------------------------------------------------------------
# Yuklab olish (ishtirokchi va buyurtmachi fayllari)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Yuklash:
    download_dir: str
    file_base_url: str
    max_download_mb: int
    download_timeout: int
    download_retries: int
    download_verify_tls: bool
    download_total_timeout: int
    ruxsat_hostlar: tuple


@functools.lru_cache(maxsize=None)
def yuklash():
    return Yuklash(
        download_dir=yol(os.environ.get("DOWNLOAD_DIR"), "_downloads"),
        # Bazadagi `link` NISBIY yo'l bo'lishi mumkin:
        #   8638/excel2/9518/1650516762-...xls
        # To'liq manzil = FILE_BASE_URL + link
        #   https://apisitender.mc.uz/storage/ + 8638/excel2/...
        file_base_url=_matn("FILE_BASE_URL"),
        # 2026-09-25: 50 → 900 (buyurtmachi qarori). Sabab: jonli yurishda
        # 107 MB li HALOL hujjat «fayl juda katta» deb texnik holatga tushdi va
        # baholanmay qoldi. DIQQAT — bu FAQAT yuklab olish chegarasi. Fayl
        # HAQIQATAN o'qiladimi, yana uchta narsaga bog'liq va ular alohida
        # chegaralar bilan himoyalangan:
        #   • `reader.MAX_XLSX_PART_BYTES` (200 MB) — bitta varaq XML i
        #   • `reader.MAX_XLSX_QIRQISH_BYTES` (1 GB) — qirqish oqimi (zip-bomba)
        #   • konteyner xotirasi (compose: WORKER_MEMORY)
        # Bu chegaralardan oshgan fayl `ExcelTooLargeError` beradi — ya'ni
        # TEXNIK holat (status 4), RAD ETISH emas: OLTIN QOIDA saqlanadi.
        max_download_mb=_son("MAX_DOWNLOAD_MB", "900"),
        download_timeout=_son("DOWNLOAD_TIMEOUT", "60"),
        download_retries=_son("DOWNLOAD_RETRIES", "3"),
        download_verify_tls=os.environ.get("DOWNLOAD_VERIFY_TLS", "1") not in ("0", "false", "no"),
        # Butun yuklab olish uchun umumiy vaqt chegarasi — `timeout` faqat har
        # bir operatsiyaga tegishli, sekin oqim bilan cheksiz cho'zilishi
        # mumkin edi. 2026-09-25: 300 → 600 (900 MB uchun 1,5 MB/s yetarli).
        # NEGA 1800 EMAS — LEASE_MINUTES bilan bog'liq: yuklab olish lease'dan
        # uzoq davom etsa, fayl yuklanib bo'lgunicha claim muddati o'tadi, ish
        # BEHUDA ketadi va boshqa worker uni qaytadan boshlaydi (2026-09-02 dagi
        # O(qator²) livelock aynan shu naqsh edi).
        # Budjet: lease 30 daq = yuklab olish ≤10 daq + o'qish/tekshiruv ≥20 daq.
        download_total_timeout=_son("DOWNLOAD_TOTAL_TIMEOUT", "600"),
        # Host oq ro'yxati (SSRF himoyasi) — `app/download.host_ruxsatmi`.
        # Standart XAVFSIZ tomonga: ro'yxat bo'sh qoldirilmagan. Mahalliy sinov
        # uchun host qo'shing: RUXSAT_HOSTLAR=apisitender.mc.uz,127.0.0.1
        # Butunlay o'chirish: RUXSAT_HOSTLAR=*  (ishlab chiqarishda QILMANG)
        ruxsat_hostlar=tuple(
            h.strip().lower() for h in
            os.environ.get("RUXSAT_HOSTLAR", "apisitender.mc.uz").split(",")
            if h.strip()),
    )


def hozir_download_user_agent():
    return os.environ.get("DOWNLOAD_USER_AGENT",
                          "Mozilla/5.0 (compatible; TenderValidator/1.0)")


def hozir_download_headers():
    """DOWNLOAD_HEADERS — JSON, masalan {"Authorization":"Bearer ..."}."""
    return os.environ.get("DOWNLOAD_HEADERS", "").strip()


# ---------------------------------------------------------------------------
# Buyurtmachi shablonlari (etalon keshi)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Shablon:
    etalon_cache_dir: str
    etalon_cache_max_mb: int


@functools.lru_cache(maxsize=None)
def shablon():
    kesh = os.environ.get("ETALON_CACHE_DIR", "_etalon_cache")
    return Shablon(
        etalon_cache_dir=(kesh if os.path.isabs(kesh)
                          else os.path.normpath(os.path.join(ROOT, kesh))),
        # Kesh hajmi chegarasi. Bazada 31 000 dan ortiq shablon bor — hammasi
        # keshlansa disk to'lib qoladi. Chegaradan oshsa eng KAM ISHLATILGANLARI
        # o'chiriladi (LRU): `etalon_ol` har foydalanishda faylning vaqtini
        # yangilaydi.
        etalon_cache_max_mb=_son("ETALON_CACHE_MAX_MB", "2000"),
    )


def hozir_etalon_eski_keshni_saqla():
    return os.environ.get("ETALON_ESKI_KESHNI_SAQLA", "").strip() in ("1", "true", "yes")


# ---------------------------------------------------------------------------
# Kiruvchi API
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Api:
    kiruvchi_login: str
    kiruvchi_parol: str
    max_toplam: int
    docs_enabled: bool
    api_host: str
    api_port: int


@functools.lru_cache(maxsize=None)
def api():
    return Api(
        kiruvchi_login=_matn("KIRUVCHI_LOGIN"),
        kiruvchi_parol=_matn("KIRUVCHI_PAROL"),
        # Bitta so'rovdagi element chegarasi. Bu HIMOYA chegarasi, kutilgan
        # hajm emas — buyurtmachi: loyiha tenderida 1 ta, pudrat tenderida 3 ta
        # fayl keladi.
        max_toplam=_son("MAX_TOPLAM", "500"),
        # /docs, /redoc, /openapi.json — ishlab chiqarishda YOPIQ (API sxemasi
        # hujumchiga xarita bo'lmasin). Mahalliy ishlab chiqishda: DOCS_ENABLED=1
        docs_enabled=_matn("DOCS_ENABLED", "0") in ("1", "true", "yes"),
        # Faqat `python api_server.py` (uvicorn'siz) uchun; Docker uvicorn ishlatadi.
        api_host=os.environ.get("API_HOST", "0.0.0.0"),
        api_port=_son("API_PORT", "8000"),
    )


# ---------------------------------------------------------------------------
# Chiquvchi yuboruvchi (verdiktlar tender tizimiga)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Yuboruvchi:
    tender_api_url: str
    tender_api_login: str
    tender_api_parol: str
    tender_timeout: int
    yuborish_oqim: int
    yuborish_toplam: int
    yuborish_max_urinish: int
    cron_interval: int
    band_daqiqa: int
    yuborish_retry_baza: int
    yuborish_retry_maks: int
    yuborish_tashlandi_chegara: int
    texnik_yuborish: bool
    yuborish_texnik_kechikish: int
    yuborish_texnik_lease: int
    texnik_toshqin_chegara: int
    tender_qabul_statuslar: str
    tender_status_xarita: str


@functools.lru_cache(maxsize=None)
def yuboruvchi():
    return Yuboruvchi(
        tender_api_url=_matn("TENDER_API_URL"),
        tender_api_login=_matn("TENDER_API_LOGIN"),
        tender_api_parol=_matn("TENDER_API_PAROL"),
        tender_timeout=_son("TENDER_TIMEOUT", "30"),
        # Parallel yuborish. Sherik 300 ko'taradi (O2) — 20 ataylab: 2 000
        # fayl/kun uchun 20 ham ortiqcha, chegaraga yaqinlashish esa 429/503
        # xavfini tug'diradi.
        yuborish_oqim=_son("YUBORISH_OQIM", "20"),
        yuborish_toplam=_son("YUBORISH_TOPLAM", "100"),
        # ⚠️ `yn_urinish_chk` (0..3) — 3 dan katta qo'yilsa CheckViolation;
        # shuning uchun 3 bilan qisiladi.
        yuborish_max_urinish=min(_son("YUBORISH_MAX_URINISH", "3"), 3),
        cron_interval=_son("CRON_INTERVAL", "180"),
        # Band oynasi — jarayon o'rtada o'lsa qator shundan keyin o'zi ozod bo'ladi.
        band_daqiqa=_son("YUBORISH_BAND_DAQIQA", "2"),
        # Qayta urinish BACKOFF'i — `RETRY_BASE_SECONDS` ning analogi.
        # 2026-09-25 gacha backoff YO'Q edi: xatoli qator DARHOL qayta nomzod
        # bo'lardi va uni faqat sikl orasidagi `CRON_INTERVAL` uyqusi ushlab
        # turardi. Drain-loop kiritilganda bu himoya yo'qolardi: 10 soniyalik
        # tarmoq uzilishi butun to'plamni bir necha soniyada «tashlandi» ga
        # o'tkazardi. Endi: urinish 0 → 300 s, urinish 1 → 600 s (jami ~15
        # daqiqa chidamlilik), va bu `CRON_INTERVAL` dan MUSTAQIL.
        yuborish_retry_baza=_son("YUBORISH_RETRY_BAZA", "300"),
        yuborish_retry_maks=_son("YUBORISH_RETRY_MAKS", "3600"),
        # `--health` QIZIL beradigan chegara. Ilgari tashlangan xabarlar faqat
        # chop etilardi — butun navbat tashlansa ham konteyner SOG'LOM
        # ko'rinardi va hech kim bilmasdi.
        yuborish_tashlandi_chegara=_son("YUBORISH_TASHLANDI_CHEGARA", "20"),
        # Favqulodda kalit: 0 — texnik xabarlar butunlay o'chadi (verdiktlar
        # ketaveradi).
        texnik_yuborish=_matn("TEXNIK_YUBORISH", "1") not in ("0", "false", "no"),
        # H1 — 6 soat. HTTP 404 faylni BITTA urinishda o'ldiradi; saqlash
        # tizimi buzilsa butun navbat bir necha daqiqada o'ladi va minglab
        # ishtirokchiga BIZNING xatomiz uchun xabar ketardi. 6 soat operatorga
        # sezib, tuzatib, `--requeue` qilishga vaqt beradi (dead_at tozalanadi
        # → xabar umuman yozilmaydi).
        yuborish_texnik_kechikish=_son("YUBORISH_TEXNIK_KECHIKISH", "21600"),
        # (3)-shart — sekin fayl ustida worker hamon ishlayotgan bo'lishi mumkin
        # (o'lchangan: bitta fayl 60 daqiqa). `olik_belgilash` `claimed_at` ni
        # tozalamaydi.
        yuborish_texnik_lease=_son("YUBORISH_TEXNIK_LEASE", "1800"),
        # H2 — toshqin to'xtatgichi. SONIGA qaraydi, tezligiga emas: tezlikka
        # qaragan to'xtatgich toshqinni to'xtatmaydi, faqat kechiktiradi.
        texnik_toshqin_chegara=_son("TEXNIK_TOSHQIN_CHEGARA", "50"),
        # Xom qiymatlar — tahlili va OLTIN QOIDA tekshiruvi `app/sender/statuses.py` da.
        tender_qabul_statuslar=os.environ.get("TENDER_QABUL_STATUSLAR", "1,2"),
        tender_status_xarita=os.environ.get("TENDER_STATUS_XARITA", "3:2"),
    )


# ---------------------------------------------------------------------------
# Migratsiya (faqat `python -m app.db.migrate`)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Migratsiya:
    app_db_user: str
    app_db_password: str


@functools.lru_cache(maxsize=None)
def migratsiya():
    # Ilova (API, worker, yuboruvchi) ulanadigan MINIMAL huquqli rol. Parol
    # berilsa rol yaratiladi (bor bo'lsa paroli yangilanadi); huquqlar har
    # migratsiyada qayta beriladi (`app/db/huquqlar.sql`, idempotent).
    return Migratsiya(
        app_db_user=_matn("APP_DB_USER", "tender_ai"),
        app_db_password=os.environ.get("APP_DB_PASSWORD", ""),
    )


# ---------------------------------------------------------------------------
# So'rov jurnali (`sorov_jurnali` — app/jurnal.py, app/api/jurnal.py)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Jurnal:
    yoqilgan: bool
    login: str
    parol: str


@functools.lru_cache(maxsize=None)
def jurnal():
    # ATAYLAB faqat `_matn`: bu guruhni api, worker va yuboruvchi — UCHALASI
    # o'qiydi. Son sozlamasi bo'lsa, bitta imlo xatosi (SozlamaXatosi) uchala
    # xizmatni to'xtatardi — jurnal esa asosiy ishni hech qachon to'xtatmasligi
    # kerak. Navbat hajmi, qirqish chegaralari va oraliqlar shu sababli muhit
    # o'zgaruvchisi EMAS, koddagi doimiylar (app/jurnal.py).
    return Jurnal(
        # Favqulodda kalit: 0 — jurnal butunlay o'chadi (so'rovlar, loglar va
        # operator amallari yozilmaydi; `/jurnal` mavjud yozuvlarni o'qiyveradi).
        yoqilgan=_matn("JURNAL_YOQILGAN", "1") not in ("0", "false", "no"),
        # `GET /jurnal` kalitlari — KIRUVCHI_* dan ALOHIDA: sherik (tender
        # tizimi) jurnalni o'qiy olmasin. Yo'q yoki parol qisqa bo'lsa — 503.
        login=_matn("JURNAL_LOGIN"),
        parol=_matn("JURNAL_PAROL"),
    )


# ---------------------------------------------------------------------------
# Loglash (birinchi log yozuvida o'qiladi)
# ---------------------------------------------------------------------------

def hozir_log_level():
    return os.environ.get("LOG_LEVEL", "INFO")


def hozir_log_file():
    """Bo'sh bo'lsa faqat konsolga yoziladi."""
    return os.environ.get("LOG_FILE", "").strip()
