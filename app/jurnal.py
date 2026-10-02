# -*- coding: utf-8 -*-
"""So'rov jurnali — `sorov_jurnali` jadvaliga yozish (migratsiya 0002).

Shu paytgacha doimiy qayd etilmagan narsalar bitta jadvalga tushadi (`tur`):

    sorov   API ga kelgan har so'rov          (oraliq qatlam — app/api/jurnal.py)
    kirish  rad etilgan autentifikatsiya, 401 (o'sha yerdan)
    log     `tender` loggerining satrlari     (`_LogUlagich`, shu modul)
    amal    operator amali: --requeue, --qayta-och   (`amal_yoz`, sinxron)

BOSH QOIDA: jurnal asosiy ishni HECH QACHON to'xtatmaydi, kuttirmaydi va
yiqitmaydi. Baza yotgan bo'lsa ham API odatdagidek javob beradi, worker
tekshiradi, yuboruvchi yuboradi — jurnal yozuvlari esa navbatda kutadi,
navbat to'lsa TASHLANADI (va sanaladi).

Tuzilishi:

    qoy(qator)      navbatga qo'yish — faqat `deque.append`. Qulf, `Queue`,
                    `Event`, `Condition` YO'Q — ataylab: bu tizim SIGTERM
                    ishlovchisi ICHIDAN log yozadi (`sender.cli._toxtatish`).
                    Signal asosiy oqim `Queue.put` ichida turganda kelsa,
                    ishlovchining `log()` i o'sha qulfga qayta kirib, jarayon
                    osilib qolardi (o'lchangan) — yuboruvchida bu to'plam
                    o'rtasi: 60 s dan keyin SIGKILL, keyin takroriy xabar.
    _yozuvchi()     BITTA daemon oqim: har soniyada navbatni O'Z ulanishi
                    bilan bazaga bo'shatadi. API ning yagona ulanishiga
                    (`app/api/store.ULANISH`) va uning qulfiga TEGMAYDI.
    boshla(manba)   log ulagichini ulaydi va oqimni yurgizadi. FAQAT uch joyda
                    chaqiriladi: API lifespan, worker sikli, yuboruvchi sikli.
    toxtat()        oqimga oxirgi bo'shatish uchun ko'pi bilan 2 s beradi.

Nega `app/log.py` dan emas: `setup_logging()` har jarayonda ishlaydi — 60
soniyada bir yuradigan healthcheck buyruqlarida, `migrate` da (egasi roli bilan,
jadval hali yo'q paytda), `--quruq` da («BAZAGA YOZILMAYDI») va barcha operator
vositalarida ham. Shu sababli bu modulni IMPORT qilish hech narsa qilmaydi:
oqim ham, ulanish ham yo'q; psycopg faqat yozish paytida yuklanadi.
"""

import atexit
import collections
import contextvars
import datetime
import logging
import re
import sys
import threading
import time

from app import config
from app.db import DATABASE_URL
from app.log import log, setup_logging

# ---------------------------------------------------------------------------
# Chegaralar — ATAYLAB koddagi doimiylar, muhit o'zgaruvchisi emas
# (sababi `config.jurnal` izohida).
# ---------------------------------------------------------------------------
#: Navbat chegarasi. Qator soni o'zi xotirani CHEGARALAMAYDI: 2 000 qator ×
#: ikkita 64 KiB tana = 250 MB, API chegarasi esa 512 MB — va navbat aynan
#: baza yotganda, sherik qayta-qayta urinayotganda to'ladi. Shuning uchun
#: ikkinchi chegara — matn hajmi.
NAVBAT_MAX_QATOR = 2000
NAVBAT_MAX_BAYT = 8 * 1024 * 1024
TOPLAM = 200                # bitta INSERT dagi qatorlar
SORASH_ORALIGI = 1          # oqim navbatga shuncha soniyada bir qaraydi
KUTISH_MAKS = 60            # ulanish xatosidan keyingi kutish: 1 s → 2 → ... → 60
TOXTASH_KUTISH = 2.0        # to'xtashda oxirgi bo'shatishga beriladigan vaqt

#: Tekshirilmagan so'rovlar chegarasi — bir jarayonda, bir daqiqada yoziladigan
#: `sorov` / `kirish` qatorlari (`tasdiqlangan` TRUE bo'lmaganlari). Ularni
#: kalitsiz har kim yubora oladi (401/404/405 ham qator): chegarasiz internetdan
#: kelgan oqim jadvalni saqlash muddati `DELETE` igacha to'ldirardi va navbatni
#: egallab, sherikning o'z yozuvlarini siqib chiqarardi. Ortig'i TASHLANADI va
#: sanaladi. Tekshirilgan so'rovlar va log satrlari CHEKLANMAYDI.
TEKSHIRILMAGAN_MAX = 600

#: Yozish qulfi (`_yoz`). Kalit — faqat shu dastur ishlatadigan ixtiyoriy doimiy
#: son (migratsiya qulfi `migrate._QULF_KALITI` dan keyingisi). Kutish
#: CHEGARALANGAN: boshqa jarayon tranzaksiya o'rtasida qotib qolsa (`docker
#: pause`, uzilgan tarmoq), `amal_yoz` — SINXRON — operator buyrug'ini cheksiz
#: ushlab turardi. Vaqt tugasa `LockNotAvailable` (OperationalError): yozuvchi
#: oqim uni uzilish kabi qayta urinadi, `amal_yoz` ogohlantirib o'tib ketadi.
#: USHLASH ham chegaralangan: qulfni olgan jarayonning O'ZI tranzaksiya
#: o'rtasida qotsa, PostgreSQL uning sessiyasini `QULF_USHLASH` soniyadan keyin
#: uzadi (`idle_in_transaction_session_timeout`) — qulf bo'shaydi, INSERT
#: qaytariladi. Usiz bitta qotgan jarayon qolgan HAMMA jarayonlarning jurnalini
#: to'xtatib qo'yardi (o'lchangan): ularning oqimi har 5 s da `LockNotAvailable`
#: olib, navbati to'lib tashlardi.
YOZISH_QULFI = 740202610
QULF_KUTISH = 5             # soniya
QULF_USHLASH = 10           # soniya

#: Qirqish, belgi. Tanadan boshqa maydonlar ham qirqiladi: yo'l, so'rov qatori,
#: User-Agent va login 401/404 bilan tugagan (tekshirilmagan) so'rovda ham
#: yoziladi — ularning uzunligini mijoz belgilaydi.
TANA_MAX = 65536
XABAR_MAX = 8192
QISQA_MAX = 2000

YASHIRILDI = "[yashirildi]"

USTUNLAR = ("yaratildi", "tur", "hodisa", "daraja", "manba", "sorov_id", "usul", "yol",
            "sorov_qatori", "holat_kodi", "davomiylik_ms", "ip", "login", "tasdiqlangan",
            "user_agent", "sorov_sarlavhalari", "sorov_tanasi", "javob_tanasi", "xabar",
            "qoshimcha")
_JSON_USTUNLAR = ("sorov_sarlavhalari", "qoshimcha")
_CHEGARA = {"sorov_tanasi": TANA_MAX, "javob_tanasi": TANA_MAX, "xabar": XABAR_MAX}

_INSERT = "INSERT INTO sorov_jurnali (" + ", ".join(USTUNLAR) + ") VALUES "
_QATOR = "(" + ", ".join(["%s"] * len(USTUNLAR)) + ")"
# Parametrsiz — bitta so'rovda to'rt buyruq. `SET LOCAL` faqat shu
# tranzaksiyaga. Kutish chegarasi qulf olingach QAYTARILADI: INSERT ning o'zi
# avvalgidek kutadi. Ushlash chegarasi esa COMMIT gacha qoladi — u faqat
# buyruqlar ORASIDAGI bo'sh turishni o'lchaydi (INSERT ning ishlashini emas).
_QULF = (f"SET LOCAL idle_in_transaction_session_timeout = '{QULF_USHLASH}s'; "
         f"SET LOCAL lock_timeout = '{QULF_KUTISH}s'; "
         f"SELECT pg_advisory_xact_lock({YOZISH_QULFI}); "
         f"SET LOCAL lock_timeout = DEFAULT")


# ---------------------------------------------------------------------------
# Holat
# ---------------------------------------------------------------------------
#: Navbat: (qator, taxminiy hajm). O'ngdan qo'shiladi (istalgan oqim, signal
#: ishlovchisi), chapdan FAQAT yozuvchi oqim oladi.
_NAVBAT = collections.deque()
#: Faqat `toxtat` → yozuvchi oqim yo'nalishida; `qoy` unga TEGMAYDI.
_TOXTA = threading.Event()
#: `tashlandi` — yozilmagan HAR yozuv (navbat to'lgan, qator yaroqsiz yoki
#: daqiqalik chegara); `chegaradan` — shulardan daqiqalik chegara tufaylilari.
#: `daqiqa` / `daqiqada` — joriy daqiqa va undagi tekshirilmagan qatorlar soni.
_HOLAT = {"faol": False, "manba": None, "oqim": None, "ulagich": None,
          "bayt": 0, "tashlandi": 0, "chegaradan": 0, "daqiqa": None, "daqiqada": 0,
          "xabar": "soz"}
_CHEKLANADIGAN = ("sorov", "kirish")
_soat = time.monotonic      # alohida nom: testlar almashtiradi

#: Joriy HTTP so'rovi — {"id": uuid4 hex, "tasdiqlangan": bool}; oraliq qatlam
#: qo'yadi. Log ulagichi `id` ni o'qiydi: so'rov davomida yozilgan log satrlari
#: o'sha so'rovning `sorov_id` si bilan yoziladi. Sinxron ishlovchi
#: (threadpool) ham AYNAN shu lug'atni ko'radi — kontekst nusxasi sayoz.
JORIY_SOROV = contextvars.ContextVar("jurnal_joriy_sorov", default=None)


def hozir():
    """Hodisa vaqti — har doim UTC, mintaqa bilan (`timestamptz`)."""
    return datetime.datetime.now(datetime.timezone.utc)


def faol():
    """Jurnal yoqilganmi (`boshla` chaqirilgan va `toxtat` hali chaqirilmagan)."""
    return _HOLAT["faol"]


# ---------------------------------------------------------------------------
# Navbatga qo'yish
# ---------------------------------------------------------------------------
def _hajm(qator):
    """Yozuvning TAXMINIY hajmi — matn va bayt qiymatlarining uzunligi."""
    n = 200
    for q in qator.values():
        if isinstance(q, (str, bytes)):
            n += len(q)
        elif isinstance(q, dict):
            n += sum(len(x) for x in q.values() if isinstance(x, (str, bytes)))
    return n


def _chegaradan_oshdi():
    """Tekshirilmagan qatorni joriy daqiqaga sanaydi. True — chegaradan oshdi.

    Daqiqa — qat'iy oyna (soat bo'yicha butun daqiqa), sirpanuvchi emas:
    sanash bitta taqqoslash va bitta qo'shish bo'lib qoladi (`qoy` so'rov
    yo'lida chaqiriladi).
    """
    daqiqa = int(_soat() // 60)
    if _HOLAT["daqiqa"] != daqiqa:
        _HOLAT["daqiqa"], _HOLAT["daqiqada"] = daqiqa, 0
    _HOLAT["daqiqada"] += 1
    return _HOLAT["daqiqada"] > TEKSHIRILMAGAN_MAX


def qoy(qator):
    """Bitta yozuvni navbatga qo'yadi: {ustun: qiymat} (`USTUNLAR`).

    Kutmaydi va bazaga tegmaydi — tozalash, qirqish va sirlarni yashirish
    yozuvchi oqimda (`tozalangan`). Navbat to'la bo'lsa yozuv TASHLANADI va
    sanaladi; soni keyingi muvaffaqiyatli yozishda jurnalga tushadi.

    Tekshirilmagan so'rov qatorlari (`sorov` / `kirish`, `tasdiqlangan` TRUE
    emas) daqiqasiga `TEKSHIRILMAGAN_MAX` tadan oshsa, ortig'i ham TASHLANADI
    va o'sha hisobga qo'shiladi.

    `bayt`, `tashlandi` va daqiqalik hisob qulfsiz yuritiladi, ya'ni TAXMINIY:
    ikki oqim bir paytda oshirsa bittasi yo'qolishi mumkin. Xato to'planmaydi —
    navbat bo'shagan har safar `bayt` nolga qaytadi (`_olindi`).
    """
    if not _HOLAT["faol"]:
        return
    if (qator.get("tur") in _CHEKLANADIGAN and qator.get("tasdiqlangan") is not True
            and _chegaradan_oshdi()):
        _HOLAT["tashlandi"] += 1
        _HOLAT["chegaradan"] += 1
        return
    hajm = _hajm(qator)
    if len(_NAVBAT) >= NAVBAT_MAX_QATOR or _HOLAT["bayt"] + hajm > NAVBAT_MAX_BAYT:
        _HOLAT["tashlandi"] += 1
        return
    _HOLAT["bayt"] += hajm
    _NAVBAT.append((qator, hajm))


def _olindi(n):
    """Yozilgan (yoki tashlangan) `n` ta yozuvni navbat boshidan olib tashlaydi."""
    for _ in range(n):
        _HOLAT["bayt"] -= _NAVBAT.popleft()[1]
    if not _NAVBAT:
        _HOLAT["bayt"] = 0


# ---------------------------------------------------------------------------
# Sirlarni yashirish — saqlanadigan HAR matndan o'tadi
# ---------------------------------------------------------------------------
# Tanalar va log satrlarida havolalar bor (`element=%r`, yuklab olish xatolari);
# sherikning havolasi imzoli bo'lishi, xato matni esa kalitni takrorlashi
# mumkin. `(?:\\?/){2}` — PHP `json_encode` havolani `https:\/\/` deb yozadi.
_SIR_NOMLARI = ("passw", "parol", "pwd", "secret", "token", "credential", "apikey",
                "api_key", "api-key", "private_key", "private-key", "authoriz")
_IMZO_NOMLARI = ("sig", "signature", "expires")

_NAQSHLAR = (
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?"
                r"(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)", re.S), YASHIRILDI),
    (re.compile(r"(?i)\b(basic)\s+[A-Za-z0-9+/=_-]{8,}"), r"\1 " + YASHIRILDI),
    # Token alifbosi bilan (RFC 6750), `\S+` emas: zich JSON da bo'shliq yo'q —
    # `\S+` tokendan keyingi butun tanani ham yutib yuborardi.
    (re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]+"), r"\1 " + YASHIRILDI),
    # `kalit=qiymat` shaklidagi parol — avvalo libpq ulanish satri (`host=...
    # password=...`): DATABASE_URL shu shaklda bo'lsa `safe_dsn()` uni
    # yashirmaydi (u faqat `://login:parol@` ni biladi), API esa ulanish satrini
    # ishga tushishda logga yozadi — ya'ni baza paroli jurnalga tushardi.
    # Qiymat libpq qoidasi bilan o'qiladi: qo'shtirnoq ichida (yopilmagan bo'lsa
    # — matn oxirigacha) yoki bo'shliqqacha. `&` da faqat undan keyin `nom=`
    # kelsa to'xtaydi: so'rov qatorining keyingi parametri joyida qolsin, `&` li
    # parolning esa dumi ochiq qolmasin. Nom oldida `\b` YO'Q: `PGPASSWORD=`,
    # `APP_DB_PASSWORD=`, `JURNAL_PAROL=` ham shu.
    (re.compile(r"(?i)(password|passwd|pwd|parol)[ \t]*=[ \t]*"
                r"(?:'(?:[^'\\]|\\.)*(?:'|\Z)|\"(?:[^\"\\]|\\.)*(?:\"|\Z)"
                r"|(?:[^\s'\"\\&]|\\.|&(?![^\s&=?#'\"]*=))+)", re.S),
     r"\1=" + YASHIRILDI),
    (re.compile(r"/bot\d+:[\w-]+"), "/bot" + YASHIRILDI),          # Telegram, havolada
    (re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{30,}"), YASHIRILDI),      # Telegram, yalang'och
    (re.compile(r"\bsk-[A-Za-z0-9_*-]{8,}"), YASHIRILDI),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{30,}"), YASHIRILDI),
    (re.compile(r"(:(?:\\?/){2})[^/\\\s:@]+:[^/\\\s@]+@"), r"\1***@"),   # login:parol@
)
# So'rov qismi — `?` dan bo'shliq yoki qo'shtirnoqqacha. Sxemaga ATAYLAB
# bog'lanmagan: sherik havolani FILE_BASE_URL ga NISBIY yuboradi
# (`8638/excel2/9518/fayl.xls?token=...`) — tanalar va `element=%r` log
# satrlaridagi odatiy shakl aynan shu. Naqsh `?` ning O'ZIDAN boshlanadi:
# oldiga `[^?]*` qo'yilsa, har belgidan qayta boshlanib kvadratik ishlardi.
_SOROV_QISMI = re.compile(r"\?[^\s\"'<>]+")
# Nomda `?` YO'Q — ataylab: bo'lsa `????...` ustida har `?` dan qayta boshlanib,
# naqsh kvadratik ishlardi (70 KiB — 13 s, o'lchangan). Bu matnni tekshirilmagan
# mijoz yuboradi, naqsh ishlayotganda esa GIL band — API ham to'xtab turardi.
_PARAMETR = re.compile(r"([?&])([^=&#?]+)=([^&#]*)")


def _parametr_sirsiz(m):
    nom = m.group(2).lower()
    sirmi = (any(s in nom for s in _SIR_NOMLARI) or nom in _IMZO_NOMLARI
             or nom.startswith("x-amz-"))
    return f"{m.group(1)}{m.group(2)}={YASHIRILDI}" if sirmi else m.group(0)


def _sorov_qismi_sirsiz(m):
    return _PARAMETR.sub(_parametr_sirsiz, m.group(0))


def sorov_qatori_sirsiz(sorov):
    """So'rov qatori (`a=1&token=...`): nomi sirga o'xshagan parametr qiymati yashiriladi."""
    return _PARAMETR.sub(_parametr_sirsiz, "?" + sorov)[1:]


def sirsiz(matn):
    """Matndagi kalit ko'rinishidagi bo'laklarni `[yashirildi]` ga almashtiradi."""
    for naqsh, orniga in _NAQSHLAR:
        matn = naqsh.sub(orniga, matn)
    return _SOROV_QISMI.sub(_sorov_qismi_sirsiz, matn)


# ---------------------------------------------------------------------------
# Bazaga tayyorlash
# ---------------------------------------------------------------------------
def _matn(qiymat, chegara, sorov_qatori=False):
    """Saqlashga yaroqli matn: NUL siz, sirsiz, `chegara` belgigacha.

    PostgreSQL `text` 0x00 ni saqlay olmaydi, `jsonb` esa \\u0000 ni rad
    etadi — mijoz uni bitta so'rov bilan kirita oladi (`GET /x%00y`: uvicorn
    yo'lni dekodlaydi). Tozalanmasa o'sha qator har safar butun to'plamni
    yiqitardi. Yolg'iz surrogat ham shunday (UTF-8 ga o'girilmaydi).
    """
    if isinstance(qiymat, bytes):
        qiymat = qiymat.decode("utf-8", "replace")
    # Avval qo'pol qirqish: naqshlar megabaytli satr ustida yurmasin (bitta
    # rad etilgan element `element=%r` bilan butun log satriga tushadi).
    s = qiymat[:chegara + 4096].replace("\x00", "")
    s = s.encode("utf-8", "replace").decode("utf-8")
    if sorov_qatori:
        s = sorov_qatori_sirsiz(s)
    s = sirsiz(s)
    return s if len(s) <= chegara else s[:chegara - 1] + "…"


def _json_toza(qiymat, chuqurlik=0):
    """`jsonb` ustuni uchun: ichidagi HAR matn `_matn` dan o'tadi."""
    if qiymat is None or isinstance(qiymat, (bool, int, float)):
        return qiymat
    if chuqurlik < 5:
        if isinstance(qiymat, dict):
            return {_matn(str(k), QISQA_MAX): _json_toza(q, chuqurlik + 1)
                    for k, q in qiymat.items()}
        if isinstance(qiymat, (list, tuple)):
            return [_json_toza(q, chuqurlik + 1) for q in qiymat]
    return _matn(qiymat if isinstance(qiymat, (str, bytes)) else str(qiymat), QISQA_MAX)


def tozalangan(qator):
    """Yozuvni bazaga yozishga tayyorlaydi: {ustun: qiymat} — HAR ustun bilan.

    Tanalar bayt ko'rinishida keladi (oraliq qatlam ularni dekodlamaydi —
    so'rov yo'lida ortiqcha ish bo'lmasin); shu yerda matnga aylanadi.
    """
    toza = {}
    for ustun in USTUNLAR:
        q = qator.get(ustun)
        if q is None:
            toza[ustun] = None
        elif ustun in _JSON_USTUNLAR:
            toza[ustun] = _json_toza(q)
        elif isinstance(q, (str, bytes)):
            toza[ustun] = _matn(q, _CHEGARA.get(ustun, QISQA_MAX),
                                sorov_qatori=ustun == "sorov_qatori")
        else:
            toza[ustun] = q
    if toza["yaratildi"] is None:
        toza["yaratildi"] = hozir()
    return toza


def _yoz(conn, qatorlar):
    """BITTA ko'p qatorli INSERT — atomik: yo hammasi yoziladi, yo hech biri.

    INSERT oldidan, O'SHA tranzaksiyada yozish qulfi olinadi
    (`pg_advisory_xact_lock` — COMMIT da o'zi bo'shaydi). Jurnalga uch jarayon
    (api, worker, sender) va operator buyruqlari yozadi; `id` INSERT paytida
    beriladi, qator esa COMMIT da ko'rinadi. Qulfsiz kichik `id` li qator
    kattasidan KEYIN ko'rinishi mumkin edi — `keyin_id` bilan kuzatayotgan
    o'quvchi uni hech qachon ko'rmasdi (kursor allaqachon o'tib ketgan). Qulf
    bilan COMMIT tartibi `id` tartibiga teng.

    Tranzaksiya ANIQ ochiladi: yozuvchi oqim ulanishi `autocommit` — usiz qulf
    o'z buyrug'i bilan birga bo'shab, hech narsani tartiblamasdi. `amal_yoz`
    ulanishida (autocommit emas) ham shu blok ishlaydi. Qatorlar qulfdan OLDIN
    tayyorlanadi — qulf faqat INSERT ning o'zi davomida ushlanadi.
    """
    from psycopg.types.json import Jsonb

    prm = []
    for qator in qatorlar:
        toza = tozalangan(qator)
        for ustun in USTUNLAR:
            q = toza[ustun]
            prm.append(Jsonb(q) if q is not None and ustun in _JSON_USTUNLAR else q)
    with conn.transaction():
        conn.execute(_QULF)
        conn.execute(_INSERT + ", ".join([_QATOR] * len(qatorlar)), prm)


# ---------------------------------------------------------------------------
# Yozuvchi oqim
# ---------------------------------------------------------------------------
def _aytib_qoy(holat, matn):
    """O'z nosozligini stderr ga aytadi — FAQAT holat o'zgarganda.

    `log()` orqali EMAS (u shu jurnalga qaytib tushardi) va har urinishda
    emas: baza yotganda har soniyada bir satr Docker logini (20 MB × 3)
    to'ldirardi. Matnda faqat istisno TURI — xabarida ulanish satri bo'lishi
    mumkin.
    """
    if _HOLAT["xabar"] != holat:
        _HOLAT["xabar"] = holat
        print(f"[jurnal] {matn}", file=sys.stderr, flush=True)


def _boshat(conn):
    """Navbatni bazaga bo'shatadi — `TOPLAM` tadan.

    Yozuv navbatdan INSERT muvaffaqiyatli bo'lgandan KEYIN olinadi: ulanish
    uzilsa (OperationalError / InterfaceError — chaqiruvchiga ko'tariladi)
    yozuvlar navbatda qoladi va qayta uriniladi. Boshqa har qanday xato —
    ma'lumot xatosi: to'plam bittalab yoziladi, faqat YOMON qator tashlanadi.
    """
    import psycopg

    # Qulf tranzaksiyasi o'rtasida qotib qolgan jarayon sessiyasini PostgreSQL
    # 10 s da uzadi (`_QULF`). U InternalError sinfidan, lekin ma'nosi —
    # ulanish uzilishi: qator yomon emas, navbatda qoladi.
    uzilish = (psycopg.OperationalError, psycopg.InterfaceError,
               psycopg.errors.IdleInTransactionSessionTimeout)
    toza = True
    while _NAVBAT:
        n = min(len(_NAVBAT), TOPLAM)
        try:
            # Indeks bilan, iteratorsiz: boshqa oqim shu paytda o'ngdan
            # qo'shayotgan bo'lsa, deque iteratori RuntimeError beradi.
            _yoz(conn, [_NAVBAT[i][0] for i in range(n)])
        except uzilish:
            raise
        except Exception:
            for _ in range(n):
                try:
                    _yoz(conn, [_NAVBAT[0][0]])
                except uzilish:
                    raise
                except Exception as exc:
                    toza = False
                    _HOLAT["tashlandi"] += 1
                    _aytib_qoy(f"qator:{type(exc).__name__}",
                               f"yozuv tashlandi ({type(exc).__name__})")
                _olindi(1)
            continue
        _olindi(n)

    # Tashlanganlar JIM yo'qolmasin: soni jurnalning o'ziga yoziladi. Yozilmasa
    # hisob saqlanadi — keyingi bo'shatishda qayta uriniladi.
    tashlandi = _HOLAT["tashlandi"]
    if tashlandi:
        sabab = "navbat to'lgan yoki yozib bo'lmagan"
        qoshimcha = {"tashlandi": tashlandi}
        # Daqiqalik chegara tufayli tashlanganlar ALOHIDA ham ko'rsatiladi:
        # «baza yotgan edi» bilan «kalitsiz so'rovlar oqimi» — boshqa-boshqa hodisa.
        chegaradan = min(_HOLAT["chegaradan"], tashlandi)
        if chegaradan:
            chegara = f"tekshirilmagan so'rovlar chegarasi, daqiqasiga {TEKSHIRILMAGAN_MAX}"
            sabab = (chegara if chegaradan == tashlandi
                     else f"{sabab}; shundan {chegaradan} tasi — {chegara}")
            qoshimcha["chegaradan"] = chegaradan
        try:
            _yoz(conn, [{"yaratildi": hozir(), "tur": "log", "hodisa": "jurnal_tashlandi",
                         "daraja": "warning", "manba": _HOLAT["manba"],
                         "xabar": f"[jurnal] {tashlandi} ta yozuv tashlandi ({sabab})",
                         "qoshimcha": qoshimcha}])
            _HOLAT["tashlandi"] -= tashlandi
            _HOLAT["chegaradan"] -= chegaradan
        except uzilish:
            raise
        except Exception:
            toza = False
    if toza:
        _aytib_qoy("soz", "yozish tiklandi")


def _yop(conn):
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass


def _yozuvchi():
    """Yozuvchi oqim: har `SORASH_ORALIGI` da navbatni bazaga bo'shatadi.

    O'Z ulanishi — `autocommit=True`: bitta yomon INSERT ulanishni «yiqilgan
    tranzaksiya» holatida qoldirmaydi. `schema.tekshir` chaqirilmaydi: sxema
    eski bo'lsa xizmatning o'zi allaqachon to'xtagan.

    Ulanmasa (yoki uzilsa) — 1 s dan boshlab ikki baravar oshib, 60 s gacha
    kutadi; yozuvlar bu orada navbatda yig'iladi (chegarasigacha).
    """
    import psycopg

    conn, kutish = None, 1
    while True:
        oxirgi = _TOXTA.is_set()
        # Navbat bo'sh bo'lsa ham tashlanganlar soni yoziladi: daqiqalik chegara
        # navbat BO'SH paytda ham tashlaydi — usiz son keyingi yozuvgacha
        # yozilmay turar, jarayon undan oldin to'xtasa esa yo'qolardi.
        if _NAVBAT or _HOLAT["tashlandi"]:
            try:
                if conn is None or conn.closed:
                    conn = psycopg.connect(DATABASE_URL, autocommit=True, connect_timeout=10)
                _boshat(conn)
                if not _HOLAT["tashlandi"] or _NAVBAT:
                    kutish = 1
                else:
                    # Navbat bo'sh, lekin tashlanganlar soni yozilmadi (masalan INSERT
                    # huquqi olib qo'yilgan): har soniyada emas — uzilishdagidek kutib,
                    # oshirib boriladi. Aks holda bo'sh jarayon bazaning logiga
                    # sutkasiga 86 400 ta xato satr yozardi.
                    if oxirgi:
                        break
                    _TOXTA.wait(kutish)
                    kutish = min(kutish * 2, KUTISH_MAKS)
                    continue
            except Exception as exc:
                _aytib_qoy(f"uzildi:{type(exc).__name__}",
                           f"bazaga yozilmadi ({type(exc).__name__}) — yozuvlar navbatda, "
                           f"qayta uriniladi")
                _yop(conn)
                conn = None
                if oxirgi:
                    break
                _TOXTA.wait(kutish)
                kutish = min(kutish * 2, KUTISH_MAKS)
                continue
        if oxirgi:
            break
        _TOXTA.wait(SORASH_ORALIGI)
    _yop(conn)


# ---------------------------------------------------------------------------
# Log ulagichi
# ---------------------------------------------------------------------------
class _LogUlagich(logging.Handler):
    """`tender` loggerining har satrini navbatga qo'yadi (`tur = 'log'`).

    `emit` bazaga tegmaydi va hech qachon istisno ko'tarmaydi. Vaqt —
    `record.created` (satr YOZILGAN payt, bazaga tushgan payt emas).
    `flush` — ataylab bo'sh (meros): chiqishda `logging.shutdown` uni
    chaqiradi, navbatni kutsa baza yotganda jarayon chiqishi osilib qolardi.
    """

    def __init__(self):
        super().__init__(level=logging.INFO)

    def emit(self, record):
        try:
            sorov = JORIY_SOROV.get()
            vaqt = datetime.datetime.fromtimestamp(record.created, datetime.timezone.utc)
            daraja = ("error" if record.levelno >= logging.ERROR else
                      "warning" if record.levelno >= logging.WARNING else "info")
            qoy({"yaratildi": vaqt, "tur": "log", "hodisa": "log", "daraja": daraja,
                 "manba": _HOLAT["manba"], "sorov_id": sorov["id"] if sorov else None,
                 "xabar": record.getMessage()})
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Yoqish / to'xtatish
# ---------------------------------------------------------------------------
def boshla(manba):
    """Jurnalni yoqadi: log ulagichi + yozuvchi oqim. `manba` — jarayon nomi
    ('api' | 'worker' | 'sender'), har yozuvga tushadi.

    Bir jarayonda bir marta ishlaydi (qayta chaqirilsa — hech narsa).
    `JURNAL_YOQILGAN=0` bo'lsa hech narsa yoqilmaydi.
    """
    if _HOLAT["oqim"] is not None or not config.jurnal().yoqilgan:
        return
    _TOXTA.clear()
    _HOLAT["manba"] = manba
    _HOLAT["faol"] = True
    if _HOLAT["ulagich"] is None:
        _HOLAT["ulagich"] = _LogUlagich()
        setup_logging().addHandler(_HOLAT["ulagich"])
        # Worker va yuboruvchi oddiy chiqadi (SIGTERM → sikl tugaydi) — oxirgi
        # satrlar («[to'xtatish] ...») shu yerda yoziladi. API da uvicorn
        # SIGTERM ni qayta ko'taradi va `atexit` ISHLAMAYDI — u `toxtat` ni
        # lifespan oxirida o'zi chaqiradi.
        atexit.register(toxtat)
    oqim = threading.Thread(target=_yozuvchi, name="jurnal-yozuvchi", daemon=True)
    try:
        oqim.start()
    except Exception as exc:
        # Oqim yurmadi (`can't start new thread`: pids cgroup / `ulimit -u`
        # tugagan). Jurnal YOQILMAYDI, xizmat esa ishga tushaveradi — bu yer API
        # lifespan, worker va yuboruvchi siklining boshi: istisno ko'tarilsa
        # xizmatning o'zi turmasdi. `oqim` holatga faqat yurgandan KEYIN
        # yoziladi — aks holda `toxtat` yurmagan oqimni `join` qilib yiqilardi.
        _HOLAT["faol"] = False
        _aytib_qoy("oqim",
                   f"yozuvchi oqim yurmadi ({type(exc).__name__}) — jurnal yoqilmadi")
        return
    _HOLAT["oqim"] = oqim


def toxtat(kutish=TOXTASH_KUTISH):
    """Yangi yozuv qabul qilishni to'xtatadi va oqimga navbatni oxirgi marta
    bo'shatishga ko'pi bilan `kutish` soniya beradi — UNDAN UZOQ EMAS.

    Oqim daemon: baza yotgan bo'lsa (ulanish 10 s gacha cho'ziladi) jarayon
    uni kutmasdan chiqadi, navbatdagi yozuvlar yo'qoladi. Aks holda
    `docker stop` va SIGTERM bilan to'xtash ulanish vaqtiga cho'zilardi.
    """
    oqim = _HOLAT["oqim"]
    if oqim is None:
        return
    _HOLAT["faol"] = False
    _HOLAT["oqim"] = None
    _TOXTA.set()
    oqim.join(kutish)


# ---------------------------------------------------------------------------
# Operator amallari
# ---------------------------------------------------------------------------
def amal_yoz(conn, manba, hodisa, qoshimcha=None):
    """Operator amalini yozadi (`tur = 'amal'`) — BITTA sinxron INSERT,
    buyruqning O'Z ulanishida, buyruq o'z ishini COMMIT qilgandan KEYIN.
    Yozish qulfi bilan, yozuvchi oqim kabi (`_yoz`); qulf `QULF_KUTISH` soniyada
    olinmasa — bu ham quyidagi «xato».

    Yozuvchi oqim orqali emas: buyruq bir zumda tugaydi, oqim esa ulanishga
    ulgurmasligi mumkin. Xato YUTILADI (ROLLBACK + ogohlantirish) — jurnal
    yozilmagani uchun operator buyrug'i yiqilmasin; buyruqning o'z ishi bu
    paytda allaqachon saqlangan.
    """
    if not config.jurnal().yoqilgan:
        return
    try:
        _yoz(conn, [{"yaratildi": hozir(), "tur": "amal", "hodisa": hodisa,
                     "daraja": "info", "manba": manba, "qoshimcha": qoshimcha}])
        conn.commit()
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        log(f"[jurnal] amal yozilmadi ({hodisa}): {type(exc).__name__}", "warning")
