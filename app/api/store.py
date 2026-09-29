# -*- coding: utf-8 -*-
"""Bazaga yozish — `files`/`templates` qatorlari va tekshiruv navbati.

(Ilgari `api_server.py` da — ko'chirilgan; bitta ulanish va qulf endi
`YagonaUlanish` sinfida.)
"""

import threading

import psycopg

from app.db import DATABASE_URL, schema
from app.db.schema import JOBS_TABLE, TEMPLATES_TABLE
from app.log import log
from app.worker.queue import shablon_kelganini_tekshir

FILES_TABLE = JOBS_TABLE


# Baza ulanishi
# ---------------------------------------------------------------------------
# Bitta ulanish + qulf ishlatiladi, ulanishlar hovuzi (psycopg_pool) EMAS.
#
# Nega: (a) yuk past — cho'qqi 200 so'rov/daqiqa = 3,3/s, har so'rov ~10 ms
# ishlaydi, ya'ni bitta ulanish ~3% band bo'ladi; (b) yangi paket qo'shilsa
# manifest BESH joyda yangilanishi kerak (Dockerfile, ornatish.sh, PROD_FAYLLAR
# va b.) — arzimas foyda uchun qo'shimcha xavf; (c) har so'rovga yangi ulanish
# OCHIB BO'LMAYDI: sovuq ulanish 20 soniyagacha cho'zilishi o'lchangan.
#
# (b) endi o'rinli emas — manifest bitta (requirements.txt); (a) va (c) o'z kuchida.

class YagonaUlanish:
    """Jarayondagi YAGONA ulanish va uni himoyalovchi qulf.

    Foydalanish — faqat qulf ichida:

        with ULANISH.qulf:
            conn = ULANISH.ol()
    """

    def __init__(self):
        self.conn = None
        self.qulf = threading.Lock()

    def _yangi(self):
        conn = psycopg.connect(DATABASE_URL, connect_timeout=30)
        conn.autocommit = False
        schema.tekshir(conn)          # sxema qo'llanganmi (jarayonda bir marta)
        return conn

    def ol(self):
        """Tirik ulanish qaytaradi; uzilgan bo'lsa qayta ulanadi."""
        conn = self.conn
        if conn is not None and not conn.closed:
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                conn.rollback()
                return conn
            except (psycopg.OperationalError, psycopg.InterfaceError):
                try:
                    conn.close()
                except Exception:
                    pass
        self.conn = self._yangi()
        return self.conn

    def yop(self):
        conn = self.conn
        if conn is not None and not conn.closed:
            try:
                conn.close()
            except Exception:                     # pragma: no cover
                pass


ULANISH = YagonaUlanish()


# ---------------------------------------------------------------------------
# Bazaga yozish
# ---------------------------------------------------------------------------
def _mavjudni_top(cur, file_id):
    """(jadval, id, link) yoki None — `file_id` IKKALA jadvalda qidiriladi.

    Nega ikkalasida: `files` va `templates` da ikkita MUSTAQIL UNIQUE bor, ya'ni
    bir xil `file_id` ikkalasida ham bo'lishi hech qaysi cheklovni buzmaydi.
    «Butun bazada noyob» talabini shu tekshiruv ta'minlaydi. Sherikning slot
    xatosi bu loyihada allaqachon ommaviy bo'lgan (364/633 fayl).
    """
    cur.execute(
        f"SELECT 'files' AS jadval, id, link, tender_id, type FROM {FILES_TABLE} "
        f"WHERE file_id = %s "
        f"UNION ALL "
        f"SELECT 'templates', id, link, tender_id, type FROM {TEMPLATES_TABLE} "
        f"WHERE file_id = %s",
        (file_id, file_id))
    qator = cur.fetchone()
    return qator if qator else None


def _navbatga_qoy(cur, fayl_id):
    """`jobs_state` ga navbat yozuvi.

    INVARIANT: `jobs_state.uuid` = `files.id::text` — boshqa hech narsa emas.
    `ish_ol` join'da `uuid::bigint` cast qiladi, ya'ni BITTA raqamsiz qiymat
    butun so'rovni istisno bilan yiqitadi va navbat hech kimga ish bermaydi.
    """
    cur.execute("INSERT INTO jobs_state (uuid) VALUES (%s) "
                "ON CONFLICT (uuid) DO NOTHING", (str(fayl_id),))


def _navbatni_tozala(cur, fayl_id):
    """Fayl qayta tekshirilishi uchun `jobs_state` ni nollaydi.

    BUSIZ FAYL HECH QACHON QAYTA TEKSHIRILMAYDI: `ish_ol` nomzodga qat'iy shart
    qo'yadi (`dead_at IS NULL`, `attempts < MAX_ATTEMPTS`, `retry_after`), va
    `kashf_qil` mavjud qatorga TEGMAYDI (`ON CONFLICT DO NOTHING`). Ya'ni fayl
    ilgari o'lik bo'lgan, shablon kutgan yoki urinishlarini sarflagan bo'lsa,
    faqat `files.status = 0` yozish uni qaytarmaydi.

    Namuna: `qayta_navbat.py:103-108`.
    """
    cur.execute("""UPDATE jobs_state
                      SET claimed_at = NULL, claim_token = NULL, attempts = 0,
                          last_error = NULL, retry_after = NULL,
                          dead_at = NULL, dead_reason = NULL, validated_at = NULL,
                          updated_at = now()
                    WHERE uuid = %s""", (str(fayl_id),))


def elementni_yoz(conn, el):
    """Bitta elementni bazaga yozadi. Qaytadi: (holat, id, xato).

    Har element O'Z tranzaksiyasida — bittasi yiqilsa qolganlari ishlanadi.
    """
    jadval = TEMPLATES_TABLE if el["role"] == "consulting" else FILES_TABLE
    shablonmi = el["role"] == "consulting"

    with conn.transaction():
        with conn.cursor() as cur:
            mavjud = _mavjudni_top(cur, el["file_id"])

            if mavjud is not None:
                qayerda, mavjud_id, eski_link, eski_tender, eski_type = mavjud
                kutilgan = "templates" if shablonmi else "files"
                if qayerda != kutilgan:
                    return ("xato", None,
                            "file_id %d allaqachon `%s` jadvalida (role mos emas) — "
                            "mavjud qator o'chirilmadi" % (el["file_id"], qayerda))

                # `file_id` bir xil, lekin BOSHQA tender/slot — bu takror emas,
                # to'qnashuv. Jim `takror` deb o'tkazsak, tender B uchun shablon
                # HECH QACHON yozilmaydi va uning fayllari abadiy «shablon
                # kutilmoqda» da qoladi (e2e da aynan shu bo'ldi, 2026-09-23).
                # Mavjud qatorni boshqa tenderga «ko'chirish» ham mumkin emas —
                # u allaqachon verdikt olgan bo'lishi mumkin.
                if (eski_tender, eski_type) != (el["tender_id"], el["type"]):
                    return ("xato", None,
                            "file_id %d allaqachon tender_id=%s type=%s uchun yozilgan "
                            "(kelgani: tender_id=%s type=%s) — file_id butun bazada "
                            "noyob bo'lishi kerak"
                            % (el["file_id"], eski_tender, eski_type,
                               el["tender_id"], el["type"]))

                if (eski_link or "") == el["link"]:
                    return "takror", mavjud_id, None

                if shablonmi:
                    # O4: buyurtmachi «shablon almashtirilmaydi» dedi — demak bu
                    # ANOMALIYA. Qatorni yangilaymiz, lekin o'sha tender+type
                    # bo'yicha allaqachon verdikt olgan fayllarni QAYTA NAVBATGA
                    # QO'YMAYMIZ: ular tenderga yuborilgan, qayta tekshirsak
                    # yuborilgan verdikt o'zgarardi (qabul -> rad). Qaror ODAM
                    # tomonidan qabul qilinsin (`qayta_navbat.py`).
                    cur.execute(
                        f"UPDATE {TEMPLATES_TABLE} SET link = %s, status = 0, "
                        f"updated_at = now() WHERE id = %s",
                        (el["link"], mavjud_id))
                    log("[ANOMALIYA] shablon link o'zgardi: file_id=%s tender_id=%s "
                        "type=%s — eski verdiktlar qayta tekshirilmadi, "
                        "operator ko'rigi kerak"
                        % (el["file_id"], el["tender_id"], el["type"]), "warning")
                    return "yangilandi", mavjud_id, None

                # Ishtirokchi fayli: verdikt nollanadi va navbat TOZALANADI.
                # Outbox'dagi eski xabarga TEGILMAYDI — u allaqachon yuborilgan
                # tarix; yangi verdikt outbox'ga YANGI qator qo'shadi (A2).
                cur.execute(
                    f"UPDATE {FILES_TABLE} SET link = %s, status = 0, comment = NULL, "
                    f"updated_at = now() WHERE id = %s", (el["link"], mavjud_id))
                _navbatni_tozala(cur, mavjud_id)
                _navbatga_qoy(cur, mavjud_id)
                return "yangilandi", mavjud_id, None

            # ── Yangi qator ──────────────────────────────────────────────────
            try:
                cur.execute(
                    f"INSERT INTO {jadval} (link, file_id, tender_id, type, status, "
                    f"created_at, updated_at) "
                    f"VALUES (%s, %s, %s, %s, 0, now(), now()) RETURNING id",
                    (el["link"], el["file_id"], el["tender_id"], el["type"]))
                yangi_id = cur.fetchone()[0]
            except psycopg.errors.UniqueViolation:
                # Parallel so'rov bizdan oldin ulgurdi — bu xato emas, takror.
                return "takror", None, None

            if not shablonmi:
                _navbatga_qoy(cur, yangi_id)
            return "qabul_qilindi", yangi_id, None


def _shablon_uygot():
    """Shablon kelgani haqida kutayotgan fayllarni uyg'otadi (F1).

    ALOHIDA ulanishda: `shablon_kelganini_tekshir` ichida `conn.commit()` bor,
    ya'ni asosiy ulanishda chaqirilsa ochiq tranzaksiyani yopib yuborardi.
    Xato bu yerda YUTILADI — uyg'otish qulaylik, `ETALON_KUTISH_INTERVAL`
    (600 s) baribir faylni o'zi qaytaradi.
    """
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=30) as c:
            schema.tekshir(c)
            n = shablon_kelganini_tekshir(c)
            if n:
                log("[shablon keldi] %d ta kutayotgan fayl navbatga qaytarildi" % n)
    except Exception as exc:                      # pragma: no cover
        log("[uyg'otish] shablon_kelganini_tekshir: %s" % exc, "warning")
