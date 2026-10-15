# -*- coding: utf-8 -*-
"""Bazaning TO'LIQ nusxasi — `GET /dump`, yuklab olinadigan SQL fayl.

Filtrsiz va shartsiz: hamma jadval (`files`, `templates`, `jobs_state`,
`jobs_validation_log`, `validation_evidence`, `yuborish_navbati`,
`sorov_jurnali`, `schema_migrations` — keyin qo'shiladiganlari ham), ularning
tuzilmasi (ketma-ketliklar, cheklovlar, indekslar, funksiya, trigger) va HAMMA
qatori. Fayl BO'SH bazaga psql bilan tiklanadi:

    createdb tender_v2_nusxa
    psql -X -v ON_ERROR_STOP=1 -d tender_v2_nusxa -f tender_v2_....sql

NEGA pg_dump EMAS. Rasmda pg_dump yo'q (Debian'niki 17-serverni olmaydi), ilova
roli esa jadvallar egasi emas. Shuning uchun nusxa shu yerda, katalogdan
yasaladi: tuzilma — PostgreSQL ning o'z `pg_get_*def` funksiyalaridan,
qatorlar — `COPY ... TO STDOUT` dan (pg_dump ham aynan shu matn formatini
yozadi). Tender-v2 ishlatmaydigan obyekt turi (view, enum, kengaytma, ...)
bazada paydo bo'lsa nusxa CHALA yozilmaydi — 500 bilan rad etiladi
(`_QOLLANMAYDI`).

XAVFSIZLIK
  • O'z kalitlari: DUMP_LOGIN / DUMP_PAROL (parol kamida 24 belgi). Yo'q
    bo'lsa 503 — standart holatda yo'l YOPIQ. Sherikning KIRUVCHI_* va
    jurnalning JURNAL_* kalitlari bu yerda ishlamaydi.
  • Faqat o'qish: alohida ulanish, `REPEATABLE READ, READ ONLY` — bitta
    snapshot, hamma jadval bir lahzadagi holatida. Ilova roli (tender_ai)
    hech narsani o'zgartira olmaydi.
  • Bir vaqtda BITTA nusxa (ikkinchisi — 429). API ning yagona ulanishi va
    qulfiga tegilmaydi: nusxa paytida ham `/check` ishlayveradi.
  • Javob tanasi so'rov jurnaliga yozilmaydi, faqat hajmi (app/api/jurnal.py).
  • `Cache-Control: no-store`, `Content-Disposition: attachment`.

YAXLITLIK. HTTP 200 birinchi baytdan oldin ketadi — o'rtadagi xato holat
kodini o'zgartira olmaydi. Shuning uchun fayl `BEGIN;` bilan boshlanib
`COMMIT;` bilan tugaydi (uzilgan fayl hech narsa tiklamaydi) va oxirgi qatori
`-- NUSXA TO'LIQ`; xato bo'lsa uning o'rnida `ROLLBACK;` va `-- XATO`.
"""

import threading
import time
import zlib

import anyio
import psycopg
from fastapi.responses import JSONResponse, StreamingResponse

from app import config
from app import jurnal as _jurnal
from app.api.jurnal import PAROL_MIN, _kalit_xatosi, _xato
from app.db import DATABASE_URL
from app.log import log

#: Javob shu hajmdagi bo'laklar bilan ketadi: COPY har qatorni alohida beradi,
#: har qator alohida ASGI xabari bo'lsa yuklash sekinlashardi.
BOLAK = 256 * 1024

#: Shuncha soniya oldinga siljimagan band ESKIRGAN hisoblanadi: oqim
#: boshlanmay qolsa yoki mijoz yo'qolib generator yopilmasa ham yo'l abadiy
#: band bo'lib qolmasin.
BAND_ESKIRISH = 600

#: Tizim sxemalaridan tashqari hammasi (amalda faqat `public`).
_FOYDALANUVCHI = ("n.nspname NOT IN ('pg_catalog', 'information_schema') "
                  "AND n.nspname NOT LIKE 'pg\\_%'")

_SESSIYA = (
    "SET statement_timeout = 0; SET lock_timeout = '30s'; "
    # Tashlab ketilgan nusxa tranzaksiyasini server o'zi yopsin.
    "SET idle_in_transaction_session_timeout = '10min'; "
    # COPY matni shu sozlamalarga bog'liq — pg_dump ham xuddi shularni qo'yadi.
    "SET client_encoding = 'UTF8'; SET DateStyle = ISO; SET IntervalStyle = postgres; "
    "SET extra_float_digits = 3; SET TimeZone = 'UTC'; "
    # Katalog funksiyalari nomlarni sxemasi bilan yozsin (`public.files`):
    # tiklash fayli `search_path` ga bog'liq bo'lmaydi.
    "SET search_path = pg_catalog")

_MALUMOT = ("SELECT current_database(), current_setting('server_version'), "
            "pg_database_size(current_database()), now()")

# Nusxa CHALA bo'lib qoladigan har narsa — bittasi bo'lsa ham rad etiladi.
_QOLLANMAYDI = f"""
SELECT format('%s — %s', c.oid::regclass, CASE c.relkind
           WHEN 'v' THEN 'view' WHEN 'm' THEN 'materialized view'
           WHEN 'p' THEN 'partitioned table' WHEN 'f' THEN 'foreign table'
           WHEN 'c' THEN 'composite type' WHEN 'I' THEN 'partitioned index'
           ELSE 'relkind ' || c.relkind::text END)
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE {_FOYDALANUVCHI} AND c.relkind NOT IN ('r', 'S', 'i', 't')
UNION ALL
SELECT format('%s — typtype %s', t.oid::regtype, t.typtype)
  FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace
 WHERE {_FOYDALANUVCHI} AND t.typtype IN ('b', 'd', 'e', 'r', 'm')
   -- har jadvalning massiv turi (`_files`) ham 'b' — u jadval bilan birga keladi
   AND NOT EXISTS (SELECT 1 FROM pg_type e WHERE e.typarray = t.oid)
UNION ALL
SELECT format('%s — prokind %s', p.oid::regprocedure, p.prokind)
  FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
 WHERE {_FOYDALANUVCHI} AND p.prokind NOT IN ('f', 'p')
UNION ALL
SELECT format('kengaytma %s', extname) FROM pg_extension WHERE extname <> 'plpgsql'
UNION ALL
SELECT format('%s — policy %s', polrelid::regclass, polname) FROM pg_policy
UNION ALL
SELECT format('%s — inherits %s', inhrelid::regclass, inhparent::regclass) FROM pg_inherits
UNION ALL
SELECT format('%s — rule %s', r.ev_class::regclass, r.rulename)
  FROM pg_rewrite r JOIN pg_class c ON c.oid = r.ev_class
  JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE {_FOYDALANUVCHI} AND c.relkind = 'r'
UNION ALL
SELECT format('sxema %s — USAGE huquqi yo''q', n.nspname) FROM pg_namespace n
 WHERE {_FOYDALANUVCHI} AND NOT has_schema_privilege(n.oid, 'USAGE')
   AND EXISTS (SELECT 1 FROM pg_class c WHERE c.relnamespace = n.oid)
UNION ALL
SELECT format('%s — o''qish huquqi yo''q', c.oid::regclass)
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE {_FOYDALANUVCHI}
   AND ((c.relkind = 'r' AND NOT has_table_privilege(c.oid, 'SELECT'))
     OR (c.relkind = 'S' AND NOT has_sequence_privilege(c.oid, 'SELECT')
                         AND NOT has_sequence_privilege(c.oid, 'USAGE')))
ORDER BY 1"""

_SXEMALAR = f"""
SELECT format('CREATE SCHEMA IF NOT EXISTS %I;', n.nspname) FROM pg_namespace n
 WHERE {_FOYDALANUVCHI} AND n.nspname <> 'public'
   AND (EXISTS (SELECT 1 FROM pg_class c WHERE c.relnamespace = n.oid)
     OR EXISTS (SELECT 1 FROM pg_proc p WHERE p.pronamespace = n.oid))
ORDER BY 1"""

_FUNKSIYALAR = f"""
SELECT pg_get_functiondef(p.oid)
  FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
 WHERE {_FOYDALANUVCHI} AND p.prokind IN ('f', 'p')
ORDER BY p.oid::regprocedure::text"""

# Identity ustunining ketma-ketligi (deptype 'i') jadval bilan birga yaratiladi.
_IDENTITY = ("EXISTS (SELECT 1 FROM pg_depend d WHERE d.classid = 'pg_class'::regclass "
             "AND d.objid = c.oid AND d.deptype = 'i')")

_KETMA_KETLIKLAR = f"""
SELECT format('CREATE SEQUENCE %s AS %s INCREMENT BY %s MINVALUE %s MAXVALUE %s START WITH %s CACHE %s%s;',
              c.oid::regclass, format_type(s.seqtypid, NULL), s.seqincrement, s.seqmin,
              s.seqmax, s.seqstart, s.seqcache, CASE WHEN s.seqcycle THEN ' CYCLE' ELSE '' END)
  FROM pg_sequence s JOIN pg_class c ON c.oid = s.seqrelid
  JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE {_FOYDALANUVCHI} AND NOT {_IDENTITY}
ORDER BY 1"""

_JADVALLAR = f"""
SELECT c.oid, c.oid::regclass::text, c.relpersistence = 'u'
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE {_FOYDALANUVCHI} AND c.relkind = 'r'
ORDER BY 2"""

_USTUNLAR = f"""
SELECT a.attrelid, quote_ident(a.attname), format_type(a.atttypid, a.atttypmod),
       a.attnotnull, pg_get_expr(d.adbin, d.adrelid), a.attidentity::text,
       a.attgenerated::text,
       CASE WHEN a.attcollation <> 0 AND a.attcollation <> t.typcollation THEN
            (SELECT format('%I.%I', cn.nspname, co.collname) FROM pg_collation co
               JOIN pg_namespace cn ON cn.oid = co.collnamespace
              WHERE co.oid = a.attcollation) END
  FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid
  JOIN pg_namespace n ON n.oid = c.relnamespace
  JOIN pg_type t ON t.oid = a.atttypid
  LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
 WHERE {_FOYDALANUVCHI} AND c.relkind = 'r' AND a.attnum > 0 AND NOT a.attisdropped
ORDER BY a.attrelid, a.attnum"""

_IZOHLAR = f"""
SELECT format('COMMENT ON TABLE %s IS %L;', c.oid::regclass, d.description)
  FROM pg_description d JOIN pg_class c ON c.oid = d.objoid
  JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE d.classoid = 'pg_class'::regclass AND d.objsubid = 0 AND c.relkind = 'r'
   AND {_FOYDALANUVCHI}
UNION ALL
SELECT format('COMMENT ON COLUMN %s.%I IS %L;', c.oid::regclass, a.attname, d.description)
  FROM pg_description d JOIN pg_class c ON c.oid = d.objoid
  JOIN pg_namespace n ON n.oid = c.relnamespace
  JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = d.objsubid
 WHERE d.classoid = 'pg_class'::regclass AND d.objsubid > 0 AND c.relkind = 'r'
   AND {_FOYDALANUVCHI}
ORDER BY 1"""

_SAHIBLIK = f"""
SELECT format('ALTER SEQUENCE %s OWNED BY %s.%I;', d.objid::regclass,
              d.refobjid::regclass, a.attname)
  FROM pg_depend d JOIN pg_class c ON c.oid = d.objid
  JOIN pg_namespace n ON n.oid = c.relnamespace
  JOIN pg_attribute a ON a.attrelid = d.refobjid AND a.attnum = d.refobjsubid
 WHERE {_FOYDALANUVCHI} AND d.classid = 'pg_class'::regclass
   AND d.refclassid = 'pg_class'::regclass AND c.relkind = 'S' AND d.deptype = 'a'
ORDER BY 1"""

# `pg_sequences.last_value` NULL — ketma-ketlik hali ishlatilmagan (is_called = false).
_QIYMATLAR = f"""
SELECT format('SELECT pg_catalog.setval(%s, %s, %s);',
       CASE WHEN {_IDENTITY} THEN
            (SELECT format('pg_catalog.pg_get_serial_sequence(%L, %L)',
                           d.refobjid::regclass::text, a.attname)
               FROM pg_depend d
               JOIN pg_attribute a ON a.attrelid = d.refobjid AND a.attnum = d.refobjsubid
              WHERE d.classid = 'pg_class'::regclass AND d.objid = c.oid AND d.deptype = 'i')
            ELSE quote_literal(c.oid::regclass::text) END,
       coalesce(ps.last_value, s.seqstart), (ps.last_value IS NOT NULL)::text)
  FROM pg_sequence s JOIN pg_class c ON c.oid = s.seqrelid
  JOIN pg_namespace n ON n.oid = c.relnamespace
  JOIN pg_sequences ps ON ps.schemaname = n.nspname AND ps.sequencename = c.relname
 WHERE {_FOYDALANUVCHI}
ORDER BY 1"""

# Tashqi kalit (f) — oxirida: u boshqa jadvalning PK/UNIQUE iga tayanadi.
_CHEKLOVLAR = f"""
SELECT format('ALTER TABLE ONLY %s ADD CONSTRAINT %I %s;', c.conrelid::regclass,
              c.conname, pg_get_constraintdef(c.oid))
  FROM pg_constraint c JOIN pg_class r ON r.oid = c.conrelid
  JOIN pg_namespace n ON n.oid = r.relnamespace
 WHERE {_FOYDALANUVCHI} AND r.relkind = 'r' AND c.contype IN ('p', 'u', 'x', 'c', 'f')
ORDER BY position(c.contype IN 'puxcf'), 1"""

_INDEKSLAR = f"""
SELECT pg_get_indexdef(i.indexrelid) || ';'
  FROM pg_index i JOIN pg_class r ON r.oid = i.indrelid
  JOIN pg_namespace n ON n.oid = r.relnamespace
 WHERE {_FOYDALANUVCHI} AND r.relkind = 'r'
   AND NOT EXISTS (SELECT 1 FROM pg_constraint c
                    WHERE c.conindid = i.indexrelid AND c.conrelid = i.indrelid
                      AND c.contype IN ('p', 'u', 'x'))
ORDER BY 1"""

_TRIGGERLAR = f"""
SELECT pg_get_triggerdef(t.oid) || ';' || CASE WHEN t.tgenabled = 'D' THEN
       format(' ALTER TABLE %s DISABLE TRIGGER %I;', t.tgrelid::regclass, t.tgname)
       ELSE '' END
  FROM pg_trigger t JOIN pg_class r ON r.oid = t.tgrelid
  JOIN pg_namespace n ON n.oid = r.relnamespace
 WHERE {_FOYDALANUVCHI} AND NOT t.tgisinternal
ORDER BY 1"""

_SARLAVHA = """--
-- Tender-v2 bazasining TO'LIQ nusxasi (GET /dump)
--
-- Baza:      {baza} (PostgreSQL {versiya}), diskda ~{hajm} MB
-- Snapshot:  {vaqt} — hamma jadval shu lahzadagi holatida
--            (REPEATABLE READ, READ ONLY)
--
-- Tiklash — BO'SH bazaga:
--   createdb tender_v2_nusxa
--   psql -X -v ON_ERROR_STOP=1 -d tender_v2_nusxa -f <shu fayl>
--
-- Fayl bitta tranzaksiya (BEGIN ... COMMIT): uzilgan yoki xatoli fayl hech
-- narsa tiklamaydi. Oxirgi qatori «-- NUSXA TO'LIQ» bo'lmasa — fayl chala.
-- Egalar va huquqlar (rollar, GRANT) kiritilmagan: obyektlar tiklagan
-- foydalanuvchiga tegishli bo'ladi.
--

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET client_min_messages = warning;
SET row_security = off;

BEGIN;
"""


# ---------------------------------------------------------------------------
# Nusxaning matni
# ---------------------------------------------------------------------------

def _ustun(nom, tur, notnull, standart, identity, generated, collation):
    t = f"    {nom} {tur}"
    if collation:
        t += f" COLLATE {collation}"
    if generated in ("s", "v"):
        t += f" GENERATED ALWAYS AS ({standart}) {'STORED' if generated == 's' else 'VIRTUAL'}"
    elif identity in ("a", "d"):
        t += f" GENERATED {'ALWAYS' if identity == 'a' else 'BY DEFAULT'} AS IDENTITY"
    elif standart is not None:
        t += f" DEFAULT {standart}"
    if notnull:
        t += " NOT NULL"
    return t


def qismlar(conn, hisob):
    """Nusxa matni bo'lakma-bo'lak (str yoki COPY ning baytlari).

    Tartib pg_dump nikiga o'xshash: tuzilma → qatorlar → ketma-ketlik
    qiymatlari → cheklovlar, indekslar, triggerlar (qatorlar yozilgach —
    tezroq, va trigger yuklashga aralashmaydi). `conn` — `ulan()` bergan
    ulanish. `hisob` ga jadval va qator soni yoziladi; `copy_ichida` — xato
    COPY bloki o'rtasida bo'ldimi (chaqiruvchi blokni yopishi uchun).
    """
    cur = conn.cursor()

    def royxat(sql):
        return [q[0] for q in cur.execute(sql).fetchall()]

    def bolim(nom, satrlar):
        return f"\n-- {nom}\n" + "\n".join(satrlar) + "\n" if satrlar else ""

    baza, versiya, hajm, vaqt = cur.execute(_MALUMOT).fetchone()
    yield _SARLAVHA.format(baza=baza, versiya=versiya, hajm=round(hajm / 1048576),
                           vaqt=vaqt.isoformat(" ", "seconds"))
    yield bolim("Sxemalar", royxat(_SXEMALAR))
    funksiyalar = royxat(_FUNKSIYALAR)
    if funksiyalar:
        yield "\n-- Funksiyalar\n" + "".join(f.rstrip("\n") + ";\n\n" for f in funksiyalar)
    yield bolim("Ketma-ketliklar", royxat(_KETMA_KETLIKLAR))

    jadvallar = cur.execute(_JADVALLAR).fetchall()
    ustunlar = {}
    for oid, *ustun in cur.execute(_USTUNLAR).fetchall():
        ustunlar.setdefault(oid, []).append(ustun)
    yield "\n-- Jadvallar\n"
    for oid, nom, logsiz in jadvallar:
        tavsif = ",\n".join(_ustun(*u) for u in ustunlar.get(oid, []))
        yield (f"CREATE {'UNLOGGED ' if logsiz else ''}TABLE {nom} (\n"
               f"{tavsif}\n);\n\n" if tavsif else f"CREATE TABLE {nom} ();\n\n")
    yield bolim("Izohlar", royxat(_IZOHLAR))
    yield bolim("Ketma-ketlik egalari", royxat(_SAHIBLIK))

    for oid, nom, _ in jadvallar:
        # Hisoblanadigan (GENERATED) ustun COPY ga kirmaydi — tiklashda o'zi hisoblanadi.
        nomlar = ", ".join(u[0] for u in ustunlar.get(oid, []) if u[5] not in ("s", "v"))
        if not nomlar:
            yield f"\n-- Qatorlar: {nom} — ustunsiz jadval\n"
            continue
        yield f"\n-- Qatorlar: {nom}\nCOPY {nom} ({nomlar}) FROM stdin;\n"
        hisob["copy_ichida"] = True
        with cur.copy(f"COPY {nom} ({nomlar}) TO STDOUT") as copy:
            for data in copy:
                yield data
        hisob["copy_ichida"] = False
        n = max(cur.rowcount, 0)
        hisob["jadval"] += 1
        hisob["qator"] += n
        yield f"\\.\n-- {n} qator\n"

    yield bolim("Ketma-ketlik qiymatlari", royxat(_QIYMATLAR))
    yield bolim("Cheklovlar", royxat(_CHEKLOVLAR))
    yield bolim("Indekslar", royxat(_INDEKSLAR))
    yield bolim("Triggerlar", royxat(_TRIGGERLAR))
    yield (f"\nCOMMIT;\n\n-- NUSXA TO'LIQ: {hisob['jadval']} jadval, "
           f"{hisob['qator']} qator.\n")


def ulan():
    """Nusxa uchun ALOHIDA ulanish, bitta o'qish snapshot'i ochilgan holda."""
    conn = psycopg.connect(DATABASE_URL, autocommit=True, connect_timeout=10)
    try:
        conn.execute(_SESSIYA)
        conn.execute("BEGIN ISOLATION LEVEL REPEATABLE READ, READ ONLY")
    except BaseException:
        conn.close()
        raise
    return conn


class _Yozuvchi:
    """Bo'laklarni `BOLAK` gacha yig'adi; `gzip=1` bo'lsa siqadi."""

    def __init__(self, siqilgan):
        self._bufer = bytearray()
        self._z = zlib.compressobj(6, zlib.DEFLATED, 31) if siqilgan else None
        self.jami = 0                                  # siqilmagan bayt

    def qosh(self, qism):
        """Yig'ilgan bo'lak (yuborish vaqti kelgan bo'lsa) yoki None."""
        if isinstance(qism, str):
            qism = qism.encode("utf-8")
        self.jami += len(qism)
        self._bufer += self._z.compress(qism) if self._z else qism
        if len(self._bufer) < BOLAK:
            return None
        bolak = bytes(self._bufer)
        self._bufer.clear()
        return bolak

    def oxiri(self):
        if self._z:
            self._bufer += self._z.flush()
        bolak = bytes(self._bufer)
        self._bufer.clear()
        return bolak


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

class _Band:
    """Bir vaqtda bitta nusxa. Egasi har bo'lakda `yangila` qiladi; shuncha
    vaqt jim qolgan band (`BAND_ESKIRISH`) keyingi so'rovga beriladi."""

    def __init__(self):
        self._qulf = threading.Lock()
        self._egasi = None
        self._oxirgi = 0.0

    def ol(self):
        with self._qulf:
            if (self._egasi is not None
                    and time.monotonic() - self._oxirgi < BAND_ESKIRISH):
                return None
            self._egasi = object()
            self._oxirgi = time.monotonic()
            return self._egasi

    def yangila(self, egasi):
        with self._qulf:
            if self._egasi is egasi:
                self._oxirgi = time.monotonic()

    def qoy(self, egasi):
        with self._qulf:
            if self._egasi is egasi:
                self._egasi = None


_BAND = _Band()


def _ruxsat(request):
    """None — ruxsat bor; aks holda tayyor rad javobi (503 yoki 401)."""
    s = config.dump()
    if not s.login or len(s.parol) < PAROL_MIN:
        return _xato(503, "baza nusxasi sozlanmagan: DUMP_LOGIN / DUMP_PAROL "
                          "(parol kamida %d belgi)" % PAROL_MIN)
    sabab = _kalit_xatosi(request.headers.get("authorization", ""), s)
    if sabab:
        return JSONResponse({"xato": sabab}, status_code=401,
                            headers={"WWW-Authenticate": 'Basic realm="tender-dump"'})
    sorov = _jurnal.JORIY_SOROV.get()
    if sorov is not None:
        sorov["tasdiqlangan"] = True
    return None


def _siqilganmi(prm):
    nomalum = sorted(set(prm.keys()) - {"gzip"})
    if nomalum:
        raise ValueError("noma'lum parametr: %s (mavjudi: gzip)" % ", ".join(nomalum))
    qiymat = prm.get("gzip", "0")
    if qiymat not in ("0", "1"):
        raise ValueError("gzip: 0 yoki 1")
    return qiymat == "1"


def _oqim(conn, siqilgan, egasi):
    boshlandi = time.monotonic()
    yozuvchi = _Yozuvchi(siqilgan)
    hisob = {"jadval": 0, "qator": 0, "copy_ichida": False}
    manba = qismlar(conn, hisob)
    tugadi = False
    try:
        try:
            for qism in manba:
                bolak = yozuvchi.qosh(qism)
                if bolak:
                    _BAND.yangila(egasi)
                    yield bolak
        except Exception as exc:
            log(f"[dump] UZILDI: {type(exc).__name__}: {exc}", "error")
            dum = (("\n\\.\n" if hisob["copy_ichida"] else "\n")
                   + f"ROLLBACK;\n\n-- XATO: nusxa TO'LIQ EMAS ({type(exc).__name__}). "
                     "Bu faylni ishlatmang.\n")
            bolak = yozuvchi.qosh(dum)
            if bolak:
                yield bolak
        else:
            log(f"[dump] tugadi: {hisob['jadval']} jadval, {hisob['qator']} qator, "
                f"{yozuvchi.jami / 1048576:.1f} MB, {time.monotonic() - boshlandi:.0f} s")
        yield yozuvchi.oxiri()
        tugadi = True
    finally:
        if not tugadi:
            log("[dump] to'xtatildi — mijoz uzildi", "warning")
        for yop in (manba.close, conn.close):
            try:
                yop()
            except Exception:
                pass
        _BAND.qoy(egasi)


class _NusxaOqimi(StreamingResponse):
    """`StreamingResponse` + oqim har qanday yo'l bilan tugaganda generatorni
    DARHOL yopadi.

    Starlette mijoz uzilganda generatorni yopmaydi — u axlat yig'uvchiga
    qoladi. Shungacha COPY server tomonda osilib turadi va band bo'shamaydi
    (sinovda: mijoz uzilgach sessiya «active COPY» holida qoldi, keyingi
    nusxa 429 oldi). Yopish threadpool'da (ulanishni yopish bloklaydi) va
    bekor qilishdan himoyalangan holda: uzilish CancelledError bilan kelsa
    ham bajariladi. `to_thread.run_sync` bekor qilinganda oqimni tashlab
    ketmaydi, ya'ni bu yerga kelganda generator boshqa oqimda ishlamayapti.
    """

    def __init__(self, manba, **kw):
        super().__init__(manba, **kw)
        self._manba = manba

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            with anyio.CancelScope(shield=True):
                await anyio.to_thread.run_sync(self._manba.close)


def nusxa_javobi(request):
    """`GET /dump` — butun baza, SQL fayl sifatida (`?gzip=1` — .sql.gz)."""
    rad = _ruxsat(request)
    if rad is not None:
        return rad
    try:
        siqilgan = _siqilganmi(request.query_params)
    except ValueError as exc:
        return _xato(400, str(exc))
    egasi = _BAND.ol()
    if egasi is None:
        return JSONResponse({"xato": "boshqa nusxa olinmoqda — keyinroq urinib ko'ring"},
                            status_code=429, headers={"Retry-After": "60"})
    conn = None
    berildi = False
    try:
        try:
            conn = ulan()
            qollanmaydi = [q[0] for q in conn.execute(_QOLLANMAYDI).fetchall()]
            vaqt = conn.execute("SELECT now()").fetchone()[0]
        except (psycopg.OperationalError, psycopg.InterfaceError) as exc:
            log(f"[dump] boshlanmadi: {type(exc).__name__}: {exc}", "warning")
            return _xato(503, "baza mavjud emas")
        if qollanmaydi:
            log("[dump] rad etildi — qo'llanmaydigan obyektlar: %s"
                % "; ".join(qollanmaydi), "error")
            return _xato(500, "nusxa to'liq bo'lmaydi, bu obyektlar qo'llanmaydi: "
                              + "; ".join(qollanmaydi[:20]))
        fayl = "tender_v2_%s.sql%s" % (vaqt.strftime("%Y%m%d_%H%M%SZ"),
                                        ".gz" if siqilgan else "")
        log(f"[dump] boshlandi: {fayl}")
        javob = _NusxaOqimi(
            _oqim(conn, siqilgan, egasi),
            media_type="application/gzip" if siqilgan else "application/sql; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{fayl}"',
                     "Cache-Control": "no-store",
                     "X-Content-Type-Options": "nosniff",
                     # edge (nginx) javobni diskka yig'masdan uzatsin
                     "X-Accel-Buffering": "no"})
        berildi = True
        return javob
    finally:
        if not berildi:
            if conn is not None:
                conn.close()
            _BAND.qoy(egasi)
