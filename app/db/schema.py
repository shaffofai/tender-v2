# -*- coding: utf-8 -*-
"""Qat'iy sxema — jadval nomlari va SQL bo'laklari.

Baza endi BIZNIKI: `files`/`templates` ni ham, xizmat jadvallarini ham
`app/db/migrations/` yaratadi. Shuning uchun ilgari ishga tushganda
aniqlanadigan tuzilma (`_jobs_ustunlarini_aniqla` — `jobs`/`tender`/Laravel
`files` uchala sxema) endi o'zgarmas fakt:

    files.id        bigint (bigserial) — birlamchi kalit
    files.file_id   bigint — tender tomondagi kalit (guruh ustuni)
    files.tender_id bigint NOT NULL — etalon `templates` dan shu bilan topiladi
    files.send      smallint (0) — «hali yuborilmagan» sharti
    files.deleted_at, files.updated_at — bor;  files.validated_at — YO'Q

Quyidagi funksiyalar ilgari aniqlangan tuzilmaga qarab yasalgan SQL
bo'laklarini AYNAN o'sha ko'rinishda qaytaradi.

`tekshir(conn)` — ilgari aniqlash qilgan vazifa: baza tayyor bo'lmasa
(migratsiya qo'llanmagan) `SxemaXatosi` bilan DARHOL to'xtatish.

Bu modul psycopg ni FAQAT `tekshir` ichida yuklaydi — operator vositalari
(korish, ishga_tushir) psycopg o'rnatilmaganini o'zlari aniq xabar bilan
aytishi uchun.
"""

#: Ishtirokchi fayllari jadvali (nomi tarixiy: ilgari `jobs` edi).
JOBS_TABLE = "files"
TEMPLATES_TABLE = "templates"

#: Shu versiyagacha migratsiyalar qo'llangan bo'lishi SHART (`migrations/`).
KERAKLI_VERSIYA = 2


class SxemaXatosi(Exception):
    """Jadval tuzilmasi kutilganidan farq qiladi — qayta urinish FOYDASIZ."""


def tekshir(conn):
    """Baza sxemasi tayyormi — migratsiyalar qo'llanganmi (jarayonda bir marta).

    Tayyor bo'lmasa `SxemaXatosi`: qayta urinish FOYDASIZ, worker cheksiz
    aylanmasdan darhol to'xtaydi (chiqish kodi 2).
    """
    if _TEKSHIRILDI["ok"]:
        return
    import psycopg
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT max(versiya) FROM schema_migrations")
            versiya = cur.fetchone()[0]
    except psycopg.errors.UndefinedTable:
        conn.rollback()
        raise SxemaXatosi(
            "baza sxemasi qo'llanmagan (schema_migrations yo'q) — "
            "`python -m app.db.migrate` ni ishga tushiring") from None
    if versiya is None or versiya < KERAKLI_VERSIYA:
        raise SxemaXatosi(
            f"baza sxemasi eski (versiya {versiya}, kerak {KERAKLI_VERSIYA}) — "
            f"`python -m app.db.migrate` ni ishga tushiring")
    _TEKSHIRILDI["ok"] = True


_TEKSHIRILDI = {"ok": False}


def _pk():
    return "id"


def _yashamayotgan(alias=""):
    """softDeletes: `AND <alias>deleted_at IS NULL`."""
    a = f"{alias}." if alias else ""
    return f" AND {a}deleted_at IS NULL"


def _yuborilmagan(alias=""):
    """«Hali yuborilmagan» sharti (`send` smallint)."""
    a = f"{alias}." if alias else ""
    return f"{a}send = 0"


def _kalit_tengligi(alias="", param="%s"):
    """«kalit = <qiymat>» shartini INDEKSDAN foydalanadigan ko'rinishda beradi.

    `pk::text = %s` deb yozilsa PostgreSQL birlamchi kalit indeksini
    ISHLATA OLMAYDI — ustunga qo'llangan o'girish indeksni yaroqsiz qiladi
    va butun jadval skanerlanadi. 168 913 qatorli `files` da bu bitta
    so'rovni 26 soniyaga cho'zgan edi (o'lchangan), ya'ni worker soatiga
    ~180 ta fayl ishlagan.

    Yechim: o'girishni USTUNGA emas, QIYMATGA qo'llaymiz.
    """
    p = f"{alias}.{_pk()}" if alias else _pk()
    return f"{p} = {param}::bigint"


def _kalit_join(alias="j", s_alias="s"):
    """`jobs_state.uuid` (matn) bilan asosiy jadval kalitini bog'lash sharti.

    Sabab yuqoridagi bilan bir xil: o'girish `jobs_state` tomonida bo'lsa,
    asosiy jadvalning kalit indeksi ishlaydi. `jobs_state.uuid` qiymatlari
    aynan shu ustundan olinadi (`kashf_qil`), shuning uchun kalit raqamli
    bo'lganda ular ham raqamli bo'ladi.
    """
    return f"{alias}.{_pk()} = {s_alias}.uuid::bigint"
