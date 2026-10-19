# -*- coding: utf-8 -*-
"""Verdiktni yozish — `files.status`/`comment` + audit log + chiquvchi xabar.

Uchalasi BITTA tranzaksiyada: verdikt yozilib, outbox qatori yozilmasa verdikt
hech qachon tenderga yetmaydi va buni hech narsa ko'rsatmaydi.

(Ilgari `jobs_worker.py` da — ko'chirilgan.)
"""

from psycopg.types.json import Jsonb

from app.db.schema import JOBS_TABLE, _kalit_tengligi, _yashamayotgan, _yuborilmagan
from tender_engine import evidence as _evidence


def natijani_yoz(conn, ish, token, res):
    """`jobs.status` + `jobs.comment` ni yangilaydi (FAQAT send=false bo'lsa).

    Avval `jobs_state` da claim tokeni tekshiriladi (fencing) — mos kelmasa
    hech narsa yozilmaydi. Ikkalasi bitta tranzaksiyada.
    """
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE jobs_state
                SET claimed_at = NULL, claim_token = NULL, last_error = NULL,
                    dead_at = NULL, dead_reason = NULL, retry_after = NULL,
                    -- Urinishlar hisobi ham NOLLANADI. Busiz eski hisob
                    -- (masalan 5) saqlanib qolar va `olik_belgilash` verdikt
                    -- OLGAN faylni yana «o'lik» deb belgilardi: 2026-08-20
                    -- yurishida 295 ta tekshirilgan fayl shu sabab dead-letter
                    -- ro'yxatiga tushib qolgan edi.
                    attempts = 0,
                    template_file_id = %s, role = %s,
                    validated_at = now(), updated_at = now()
                WHERE uuid = %s AND (%s::text IS NULL OR claim_token = %s)
                """,
                (res.get("_template_file_id"), res.get("role"),
                 ish["uuid"], token, token),
            )
            if cur.rowcount == 0:
                return False        # claim boshqa worker'da — yozmaymiz

            cur.execute(
                f"""
                UPDATE {JOBS_TABLE}
                SET status = %s, comment = %s, updated_at = now()
                WHERE {_kalit_tengligi()}
                  AND {_yuborilmagan()}{_yashamayotgan()}
                """,
                (res["status"], res["comment_uz"], ish["uuid"]),
            )
            yozildi = cur.rowcount > 0

            cur.execute(
                """
                INSERT INTO jobs_validation_log
                    (uuid, bidder_id, type, file, role, status, findings)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (ish["uuid"], ish.get("bidder_id"), ish.get("type"),
                 res["file"], res["role"], res["status"], Jsonb(res["findings"])),
            )
            # Chiquvchi navbat (outbox) — SHU tranzaksiyada. Verdikt yozilib,
            # outbox qatori yozilmasa verdikt hech qachon tenderga yetmaydi va
            # buni hech narsa ko'rsatmaydi; shuning uchun ikkalasi birga
            # qaytariladi. `_soya_evidence` dan FARQLI — u xatoni yutishi mumkin,
            # chunki iz yo'qolsa verdikt baribir ishlaydi. Bu yerda esa aksincha.
            if yozildi:
                outbox_ga_qoy(cur, ish, res["status"], res["comment_uz"], "verdikt")
    return yozildi


# ---------------------------------------------------------------------------
# Chiquvchi navbat (outbox) — `yuborish_navbati`
# ---------------------------------------------------------------------------
# Bitta qator = bitta XABAR (bitta fayl emas). Bir faylga ketma-ket ikki xabar
# kerak bo'lishi mumkin — «texnik» (status=0, fayl o'lganda) va keyin haqiqiy
# verdikt. `files` dagi bitta yetkazish holati bunga sig'maydi: birinchi
# xabar yuborilgach ikkinchisi hech qachon yetkazilmasdi.

def outbox_ga_qoy(cur, ish, status, comment, sabab):
    """`yuborish_navbati` ga xabar qo'shadi. Qaytadi: True — qo'shildi/tiklandi.

    Takror: `yn_takror_idx` bir fayl uchun AYNAN bir xil (status, comment) ni
    ikki marta qo'ymaydi. Lekin oldingi qator `holat=3` (3 urinish tugagan)
    bo'lsa, u QAYTA OCHILADI — aks holda takroran chiqqan bir xil verdikt
    hech qachon yetkazilmasdi. Allaqachon yuborilgan (2) yoki navbatda
    turgan (0/1) qatorga tegilmaydi.
    """
    try:
        fayl_id = int(ish["uuid"])
    except (TypeError, ValueError):
        return False
    file_id = ish.get("bidder_id")            # = files.file_id
    if file_id is None:
        return False
    cur.execute(
        """
        INSERT INTO yuborish_navbati (fayl_id, file_id, status, comment, sabab)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (fayl_id, status, md5(comment)) DO UPDATE
            SET holat = 0, urinish = 0, band_until = NULL, xato = NULL
            WHERE yuborish_navbati.holat = 3
        """,
        (fayl_id, int(file_id), int(status), comment or "", sabab))
    return cur.rowcount > 0


#: Shablon (buyurtmachi fayli) topilmaganda ishtirokchiga va `files.comment`
#: ga yoziladigan matn — buyurtmachi qarori, 2026-10-19.
IZOH_SHABLON_TOPILMADI = "Ushbu lot uchun buyurtmachi fayllari topilmadi."


def shablon_topilmadi_yoz(conn, ish, token, sabab):
    """Shablon yo'q — KUTILMAYDI (2026-10-19, buyurtmachi qarori): darhol status 4.

    Ilgari fayl shablonni `ETALON_KUTISH_MAX_SOAT` (72, keyin 18) soat kutar,
    keyin yuboruvchi yana 6 soat ushlab, «shablon mavjud emas» deb yozardi —
    shablon umuman kelmaydigan tenderlarda ishtirokchi ~1–3 kun javobsiz
    qolardi. Endi BITTA tranzaksiyada:

      • `jobs_state` — o'lik, `dead_reason` = `sabab` (`ETALON_KUTILMOQDA:`
        bilan boshlanadi): shablon keyin kelsa `shablon_kelganini_tekshir`
        faylni TIRILTIRADI va haqiqiy verdikt ikkinchi xabar bo'lib ketadi (N1);
        yuboruvchining toshqin to'xtatgichi ham bu sababni ushlamaydi;
      • `files.status = 4`, `files.comment` = `IZOH_SHABLON_TOPILMADI`;
      • chiquvchi xabar (status 4, shu matn) — kechikishsiz. Yuboruvchi o'z
        texnik xabarini yozmaydi: `created_at >= dead_at` (bir tranzaksiya).

    Claim tokeni mos kelmasa (fayl boshqa jarayonda) hech narsa yozilmaydi.
    Qaytaradi: yozildimi.
    """
    status = _evidence.STATUS_TEXNIK
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE jobs_state
                SET claimed_at = NULL, claim_token = NULL,
                    attempts = greatest(attempts - 1, 0),
                    last_error = %s, dead_at = now(), dead_reason = %s,
                    retry_after = NULL, updated_at = now()
                WHERE uuid = %s AND dead_at IS NULL
                  AND (%s::text IS NULL OR claim_token = %s)
                """,
                (sabab[:2000], sabab[:2000], ish["uuid"], token, token),
            )
            if cur.rowcount == 0:
                return False
            cur.execute(
                f"""
                UPDATE {JOBS_TABLE}
                SET status = %s, comment = %s, updated_at = now()
                WHERE {_kalit_tengligi()}
                  AND {_yuborilmagan()}{_yashamayotgan()}
                """,
                (status, IZOH_SHABLON_TOPILMADI, ish["uuid"]),
            )
            yozildi = cur.rowcount > 0
            if yozildi:
                outbox_ga_qoy(cur, ish, status, IZOH_SHABLON_TOPILMADI, "texnik")
    return yozildi
