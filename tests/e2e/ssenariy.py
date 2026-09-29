# -*- coding: utf-8 -*-
"""Butun tizim ssenariysi — tender-v2 FAQAT ochiq interfeyslari orqali.

Kod ishlab chiqarishdagidek yuritiladi:
  * HTTP    — sherikning API ga POST lari (uvicorn jarayoni)
  * CLI     — hujjatlashtirilgan har buyruq (worker, yuboruvchi, operator vositalari)
  * baza    — o'zining vaqtinchalik PostgreSQL bazasi (`tender_v2_e2e`); ilova
              jarayonlari ishlab chiqarishdagidek MINIMAL huquqli rol bilan
  * tashqi  — soxta fayl serveri (yuklab olish) va soxta tender tizimi
              (verdikt yetkazish), ikkalasi har so'rovni yozib boradi

Kuzatiladigan HAMMA narsa qayd etiladi: HTTP javoblar, chiqish kodlari,
normallashgan chiqish, har bosqichdan keyingi jadvallar, sxema va huquqlar,
yuklab olishlar, yetkazilgan xabarlar. `test_butun_tizim.py` xulqni (matndan
tashqari) `kutilgan.json` bilan solishtiradi.

Bu 2026-09-29 refaktorida eski va yangi kodni solishtirgan ssenariyning
o'zi — o'shanda ikkalasi AYNAN bir xil xulq ko'rsatgan.

Bazani o'zi yaratadi va o'chiradi (`tender_v2_e2e`, rol `tender_v2_e2e_app`);
boshqa bazaga TEGMAYDI. Kod vaqtinchalik papkaga ko'chirilib ishlatiladi —
repozitoriydagi `.env` ga ham tegilmaydi.
"""
import base64
import http.server
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse

import httpx
import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIX = os.path.join(REPO, "tests", "e2e", "fixtures")
PY = sys.executable

# Vaqtinchalik papka, baza manzillari — `sozla()` to'ldiradi.
RUN = TREE = DATA = SP = ""
ADMIN_URL = MAINT_URL = APP_URL = ""
DB = "tender_v2_e2e"
APP_ROLE = "tender_v2_e2e_app"
APP_PW = "e2e" + os.urandom(8).hex()           # har yurishda yangi (rol vaqtinchalik)
DEAD_URL = "postgresql://nobody:x@127.0.0.1:1/nothing"

# Portlar QAT'IY: havolalar (files.link) bazaga shu port bilan yoziladi.
P_FILES, P_TENDER, P_API = 18731, 18732, 18733
LOGIN, PAROL = "golden", "golden-secret"
T_LOGIN, T_PAROL = "tender-login", "tender-secret"


def sozla(admin_url):
    """E2E_ADMIN_URL (superuser, istalgan bazaga) → vaqtinchalik papka va manzillar."""
    global RUN, TREE, DATA, SP, ADMIN_URL, MAINT_URL, APP_URL
    RUN = tempfile.mkdtemp(prefix="tender_v2_e2e_")
    SP = RUN
    TREE = os.path.join(RUN, "tree")
    DATA = os.path.join(RUN, "data")
    d = conninfo_to_dict(admin_url)
    MAINT_URL = make_conninfo(**d)
    ADMIN_URL = make_conninfo(**{**d, "dbname": DB})
    APP_URL = make_conninfo(**{**d, "dbname": DB, "user": APP_ROLE, "password": APP_PW})
    return RUN


def L(name):
    return f"http://127.0.0.1:{P_FILES}/storage/{name}"


# ─────────────────────────────────────────────────────────────────────────────
# Fake file host: 127.0.0.1 (allowlisted) and 127.0.0.2 (redirect target, not
# allowlisted).  /storage/<name> serves a fixture, err500/ → 500,
# redirect/ → 302 to 127.0.0.2, anything missing → 404.
# ─────────────────────────────────────────────────────────────────────────────
LOCK = threading.Lock()
HITS = {}
TENDER = {"calls": [], "seen": {}, "mode": "normal",
          "always400": set(), "fail_once_500": set(), "fail_once_401": set()}


class FileHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        host = self.server.server_address[0]
        path = urllib.parse.unquote(self.path.split("?")[0])
        with LOCK:
            HITS[f"{host}{path}"] = HITS.get(f"{host}{path}", 0) + 1
        rest = path[len("/storage/"):] if path.startswith("/storage/") else None
        if rest is None:
            return self._send(404, b"not found")
        if rest.startswith("err500/"):
            return self._send(500, b"storage exploded")
        if rest.startswith("redirect/"):
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.2:{P_FILES}/storage/"
                                         + rest[len("redirect/"):])
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        p = os.path.join(FIX, rest)
        if not os.path.isfile(p):
            return self._send(404, b"no such file")
        with open(p, "rb") as fh:
            data = fh.read()
        self._send(200, data, "application/octet-stream")

    def _send(self, code, data, ctype="text/plain"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_a):
        pass


class TenderHandler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("content-length") or 0)
        raw = self.rfile.read(n)
        try:
            body = json.loads(raw)
            item = body["results"][0]
            fid, st = int(item["file_id"]), int(item["status"])
        except Exception:
            body, fid, st = raw.decode("utf-8", "replace"), None, None
        with LOCK:
            k = TENDER["seen"][fid] = TENDER["seen"].get(fid, 0) + 1
            code = 200
            if fid in TENDER["always400"]:
                code = 400
            elif fid in TENDER["fail_once_500"] and k == 1:
                code = 500
            elif fid in TENDER["fail_once_401"] and k == 1:
                code = 401
            elif TENDER["mode"] == "strict" and st in (3, 4):
                code = 422
            TENDER["calls"].append({
                "path": self.path, "auth": self.headers.get("authorization"),
                "ua": self.headers.get("user-agent"),
                "ctype": self.headers.get("content-type"),
                "body": body, "answer": code})
        out = json.dumps({"ok": code == 200, "file_id": fid}).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *_a):
        pass


def serve(handler, host, port):
    srv = http.server.ThreadingHTTPServer((host, port), handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


# ─────────────────────────────────────────────────────────────────────────────
# Normalisation — only genuinely non-deterministic parts are masked
# ─────────────────────────────────────────────────────────────────────────────
def norm(s):
    if s is None:
        return None
    s = s.replace(TREE, "<TREE>").replace(DATA, "<DATA>").replace(RUN, "<RUN>")
    s = s.replace(FIX, "<FIX>")
    s = re.sub(r"(?m)^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} ", "", s)       # log prefix
    s = re.sub(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2}(\.\d+)?)?"
               r"([+-]\d{2}(:?\d{2})?)?", "<DATE>", s)
    s = re.sub(r"\b[0-9a-f]{32}\b", "<HEX32>", s)
    s = re.sub(r"/[0-9a-f]{12}(?=[/'\"\s]|$)", "/<TOK>", s)
    s = re.sub(r"et_(qayta_)?[A-Za-z0-9_]{8}\b", r"et_\1<TMP>", s)
    s = re.sub(r"process \[\d+\]", "process [PID]", s)
    s = re.sub(r"127\.0\.0\.1:\d+ - \"", "127.0.0.1:<PORT> - \"", s)
    s = re.sub(r"\d+ soat \d+ daq|\d+ daq \d+ son|\d+ soniya", "<T>", s)
    s = re.sub(r"\b\d+(\.\d+)?(s|daq|soat)\b", "<DUR>", s)
    s = re.sub(r"kuzatuv_<DATE>|kuzatuv_\d{4}-\d{2}-\d{2}_\d{4}", "kuzatuv_<STAMP>", s)
    return s


def normj(v):
    if isinstance(v, str):
        return norm(v)
    if isinstance(v, list):
        return [normj(x) for x in v]
    if isinstance(v, dict):
        return {k: normj(x) for k, x in v.items()}
    return v


# ─────────────────────────────────────────────────────────────────────────────
# Process helpers
# ─────────────────────────────────────────────────────────────────────────────
OUT = {"steps": [], "setup": []}


def step(name, **data):
    rec = {"name": name, **data}
    OUT["steps"].append(rec)
    brief = {k: v for k, v in rec.items() if k in ("rc", "status")}
    print(f"  · {name} {brief}", flush=True)
    return rec


def base_env(**extra):
    env = {
        "PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "/tmp"),
        "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "TZ": "UTC",
        "PYTHONHASHSEED": "0", "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8",
        "DATABASE_URL": APP_URL,
        "JOBS_TABLE": "files", "TEMPLATES_TABLE": "templates",
        "FILE_BASE_URL": f"http://127.0.0.1:{P_FILES}/storage/",
        "RUXSAT_HOSTLAR": "127.0.0.1",
        "DOWNLOAD_DIR": os.path.join(DATA, "downloads"),
        "ETALON_CACHE_DIR": os.path.join(DATA, "etalon_cache"),
        "ETALON_DISK_FALLBACK": "0",
        "LOG_FILE": "",
        "KIRUVCHI_LOGIN": LOGIN, "KIRUVCHI_PAROL": PAROL, "MAX_TOPLAM": "60",
        "TENDER_API_URL": f"http://127.0.0.1:{P_TENDER}/api/integration/ai/set-result",
        "TENDER_API_LOGIN": T_LOGIN, "TENDER_API_PAROL": T_PAROL,
        "TENDER_QABUL_STATUSLAR": "1,2,3,4", "TENDER_STATUS_XARITA": "",
        "YUBORISH_OQIM": "1", "YUBORISH_TEXNIK_KECHIKISH": "0",
        "YUBORISH_RETRY_BAZA": "0", "CRON_INTERVAL": "1",
        "RETRY_BASE_SECONDS": "0", "MAX_ATTEMPTS": "3", "POLL_INTERVAL": "1",
        "DOWNLOAD_RETRIES": "1", "DOWNLOAD_TIMEOUT": "10",
    }
    env.update({k: v for k, v in extra.items() if v is not None})
    for k in [k for k, v in extra.items() if v is None]:
        env.pop(k, None)
    return env


def _posts_since(n):
    """POSTs the fake tender received since index n — as a SORTED list: within
    one sender run PostgreSQL returns claimed rows in no guaranteed order
    (UPDATE ... RETURNING), so only the per-run set is a stable property."""
    with LOCK:
        new = TENDER["calls"][n:]
    return sorted([c["body"]["results"][0]["file_id"], c["body"]["results"][0]["status"],
                   c["answer"]] if isinstance(c["body"], dict) else [str(c["body"])]
                  for c in new)


def run(name, argv, env=None, timeout=300, stdin=None):
    with LOCK:
        n0 = len(TENDER["calls"])
    p = subprocess.run(argv, cwd=TREE, env=env or base_env(), capture_output=True,
                       text=True, timeout=timeout, input=stdin)
    return step(name, kind="cli", argv=argv[1:], rc=p.returncode,
                out=norm(p.stdout), err=norm(p.stderr), posts=_posts_since(n0))


def run_signalled(name, argv, env=None, after=4.0, loose=True):
    """Start a long-running command, SIGTERM it after `after` seconds."""
    with LOCK:
        n0 = len(TENDER["calls"])
    p = subprocess.Popen(argv, cwd=TREE, env=env or base_env(), text=True,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    time.sleep(after)
    p.send_signal(signal.SIGTERM)
    try:
        out, err = p.communicate(timeout=60)
    except subprocess.TimeoutExpired:
        p.kill()
        out, err = p.communicate()
    out, err = norm(out), norm(err)
    if loose:                              # idle-loop repetitions vary with timing
        out = "\n".join(sorted(set(re.split(r"[\r\n]+", out))))
        err = "\n".join(sorted(set(re.split(r"[\r\n]+", err))))
    return step(name, kind="cli-signal", argv=argv[1:], rc=p.returncode, out=out, err=err,
                posts=_posts_since(n0))


# ─────────────────────────────────────────────────────────────────────────────
# Database helpers
# ─────────────────────────────────────────────────────────────────────────────
def admin(sql, params=None, url=None):
    url = url or ADMIN_URL
    assert url, "sozla() chaqirilmagan — baza manzili yo'q"
    with psycopg.connect(url, autocommit=True) as c:
        c.execute(sql, params)


TABLES = {
    "files": "SELECT id, link, file_id, tender_id, type, status, send, comment, "
             "created_at IS NOT NULL, updated_at IS NOT NULL, deleted_at IS NOT NULL "
             "FROM files ORDER BY id",
    "templates": "SELECT id, link, file_id, tender_id, type, status, "
                 "created_at IS NOT NULL, updated_at IS NOT NULL, deleted_at IS NOT NULL "
                 "FROM templates ORDER BY id",
    "jobs_state": "SELECT uuid, claimed_at IS NOT NULL, claim_token IS NOT NULL, attempts, "
                  "last_error, retry_after IS NOT NULL, coalesce(retry_after > now(), false), "
                  "dead_at IS NOT NULL, dead_reason, template_file_id, role, "
                  "validated_at IS NOT NULL FROM jobs_state ORDER BY uuid::bigint",
    "jobs_validation_log": "SELECT id, uuid, bidder_id, type, file, role, status, findings "
                           "FROM jobs_validation_log ORDER BY id",
    "yuborish_navbati": "SELECT id, fayl_id, file_id, status, comment, sabab, holat, urinish, "
                        "band_until IS NOT NULL, coalesce(band_until > now(), false), "
                        "yuborilgan_at IS NOT NULL, xato FROM yuborish_navbati ORDER BY id",
    "validation_evidence": "SELECT id, fayl_id, link, tender_id, type, fayl_hash, "
                           "etalon_tpl_id, etalon_hash, rol, ichki_status, tashqi_status, "
                           "sinf, kodlar, notelar, comment_uz, varaq_dalil, validator_version "
                           "FROM validation_evidence ORDER BY id",
}


def snapshot(name):
    data = {}
    with psycopg.connect(ADMIN_URL, autocommit=True) as c:
        for t, sql in TABLES.items():
            rows = c.execute(sql).fetchall()
            data[t] = [normj(list(r)) for r in rows]
    with LOCK:
        hits = dict(sorted(HITS.items()))
    return step(name, kind="db", tables=data, hits=hits)


def sxema_tavsifi(c):
    """Sxemaning PostgreSQL VERSIYASIGA bog'liq bo'lmagan tavsifi — katalogdan.

    pg_dump EMAS: uning chiqishi mijoz (pg_dump) versiyasiga bog'liq, eski
    pg_dump yangi serverni umuman dump qilmaydi (CI: pg_dump 16 ↔ server 17).
    PG18 dagi NOT NULL cheklov yozuvlari (contype 'n') olinmaydi — ular
    `attnotnull` da allaqachon bor.
    """
    q = lambda sql: [list(r) for r in c.execute(sql).fetchall()]   # noqa: E731
    return {
        "ustunlar": q("""
            SELECT c.relname, a.attname, format_type(a.atttypid, a.atttypmod),
                   a.attnotnull, pg_get_expr(d.adbin, d.adrelid)
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = 'public'
            JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
            LEFT JOIN pg_attrdef d ON d.adrelid = c.oid AND d.adnum = a.attnum
            WHERE c.relkind = 'r' AND c.relname <> 'schema_migrations'
            ORDER BY c.relname, a.attnum"""),
        "cheklovlar": q("""
            SELECT r.relname, k.conname, k.contype::text, pg_get_constraintdef(k.oid)
            FROM pg_constraint k
            JOIN pg_class r ON r.oid = k.conrelid
            JOIN pg_namespace n ON n.oid = r.relnamespace AND n.nspname = 'public'
            WHERE k.contype IN ('p', 'u', 'c', 'f', 'x') AND r.relname <> 'schema_migrations'
            ORDER BY 1, 2"""),
        "indekslar": q("""
            SELECT tablename, indexname, indexdef FROM pg_indexes
            WHERE schemaname = 'public' AND tablename <> 'schema_migrations'
            ORDER BY 1, 2"""),
        "triggerlar": q("""
            SELECT r.relname, t.tgname, pg_get_triggerdef(t.oid)
            FROM pg_trigger t
            JOIN pg_class r ON r.oid = t.tgrelid
            JOIN pg_namespace n ON n.oid = r.relnamespace AND n.nspname = 'public'
            WHERE NOT t.tgisinternal ORDER BY 1, 2"""),
        "funksiyalar": q("""
            SELECT p.proname, pg_get_function_identity_arguments(p.oid),
                   format_type(p.prorettype, NULL), l.lanname, md5(p.prosrc)
            FROM pg_proc p
            JOIN pg_namespace n ON n.oid = p.pronamespace AND n.nspname = 'public'
            JOIN pg_language l ON l.oid = p.prolang
            ORDER BY 1, 2"""),
        "ketma_ketliklar": q("""
            SELECT sequencename, data_type::text, start_value, increment_by
            FROM pg_sequences WHERE schemaname = 'public' ORDER BY 1"""),
    }


def schema_snapshot(name):
    with psycopg.connect(ADMIN_URL, autocommit=True) as c:
        tavsif = sxema_tavsifi(c)
        grants = c.execute(
            "SELECT table_name, privilege_type FROM information_schema.role_table_grants "
            "WHERE grantee = %s AND table_name <> 'schema_migrations' ORDER BY 1, 2",
            (APP_ROLE,)).fetchall()
        colgrants = c.execute(
            "SELECT table_name, column_name, privilege_type "
            "FROM information_schema.column_privileges WHERE grantee = %s "
            "AND table_name NOT IN (SELECT table_name FROM information_schema.role_table_grants "
            "   WHERE grantee = %s AND privilege_type = 'UPDATE') "
            "AND privilege_type = 'UPDATE' ORDER BY 1, 2, 3", (APP_ROLE, APP_ROLE)).fetchall()
        seqs = c.execute(
            "SELECT c.relname, has_sequence_privilege(%s, c.oid, 'USAGE') "
            "FROM pg_class c WHERE c.relkind = 'S' AND c.relname <> 'schema_migrations_id_seq' "
            "ORDER BY 1", (APP_ROLE,)).fetchall()
        role = c.execute(
            "SELECT rolsuper, rolinherit, rolcreaterole, rolcreatedb, rolcanlogin "
            "FROM pg_roles WHERE rolname = %s", (APP_ROLE,)).fetchall()
        # Egasi — ROL sifatida (ismi emas): migratsiya roli egasi, ilova roli EMAS.
        # Ism yozilsa, boshqa superuser bilan yurgizganda yolg'on farq chiqardi.
        owners = c.execute(
            "SELECT c.relname, pg_get_userbyid(c.relowner) = current_user, "
            "       pg_get_userbyid(c.relowner) = %s FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public' "
            "AND c.relkind IN ('r','S') AND c.relname NOT LIKE 'schema_migrations%%' "
            "ORDER BY 1", (APP_ROLE,)).fetchall()
        dbconnect = c.execute("SELECT has_database_privilege(%s, %s, 'CONNECT')",
                              (APP_ROLE, DB)).fetchone()
    return step(name, kind="schema", dump=tavsif,
                grants=[list(g) for g in grants], colgrants=[list(g) for g in colgrants],
                seqs=[list(s) for s in seqs], role=[list(r) for r in role],
                owners=[list(o) for o in owners], dbconnect=list(dbconnect))


# ─────────────────────────────────────────────────────────────────────────────
# API helpers
# ─────────────────────────────────────────────────────────────────────────────
def basic(user, pw):
    return "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()


AUTH = basic(LOGIN, PAROL)
API_BASE = f"http://127.0.0.1:{P_API}"
CHECK = "/api-v2/tender-v2/check"


def api(name, body=None, raw=None, auth=AUTH, method="POST", path=CHECK):
    h = {}
    if auth is not None:
        h["Authorization"] = auth
    if raw is not None:
        h["Content-Type"] = "application/json"
    with httpx.Client(timeout=120) as cl:
        if method == "GET":
            r = cl.get(API_BASE + path, headers=h)
        elif raw is not None:
            r = cl.post(API_BASE + path, content=raw, headers=h)
        else:
            r = cl.post(API_BASE + path, json=body, headers=h)
    try:
        payload = r.json()
    except Exception:
        payload = r.text
    return step(name, kind="http", status=r.status_code, body=normj(payload),
                www=r.headers.get("www-authenticate"))


class Api:
    def __init__(self, argv, env):
        self.argv, self.env = argv, env

    def __enter__(self):
        self.p = subprocess.Popen(self.argv, cwd=TREE, env=self.env, text=True,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        t0 = time.time()
        while time.time() - t0 < 60:
            try:
                httpx.get(API_BASE + "/openapi.json", timeout=2)
                return self
            except Exception:
                if self.p.poll() is not None:
                    break
                time.sleep(0.2)
        raise SystemExit("API did not start:\n" + self.p.stderr.read())

    def __exit__(self, *exc):
        self.p.send_signal(signal.SIGTERM)
        try:
            out, err = self.p.communicate(timeout=60)
        except subprocess.TimeoutExpired:
            self.p.kill()
            out, err = self.p.communicate()
        step("api-process-output", kind="api-log", rc=self.p.returncode,
             out=norm(out), err=norm(err))


# ─────────────────────────────────────────────────────────────────────────────
# Profiles: how each tree is set up and started (the scenario is shared)
# ─────────────────────────────────────────────────────────────────────────────
#: Ishga tushirish: sxema va ilova roli — migratsiya buyrug'i (ishlab chiqarishdagidek).
PROFIL = {
    "setup": [
        ("cli", ["-m", "app.db.migrate"], "migrate"),
    ],
    "api": ["-m", "uvicorn", "api_server:app", "--host", "127.0.0.1", "--port", str(P_API)],
    "main": ["main.py"], "worker": ["jobs_worker.py"], "sender": ["yuboruvchi.py"],
    "korish": ["korish.py"], "kuzatuv": ["kuzatuv.py"], "ishga": ["ishga_tushir.py"],
}


def T(tender, files, user_type="consulting", lot=None):
    g = {"user_type": user_type, "tender_id": tender, "files": files}
    if lot:
        g["lot_type"] = lot
    return g


def F(file_id, link, typ):
    return {"file_id": file_id, "link": link, "type": typ}


def scenario(prof):  # noqa: C901 — ssenariy ATAYLAB bitta ro'yxat
    X = lambda key: [PY] + prof[key]                     # noqa: E731
    env = base_env()

    # ── 0. schema (recorded apart: the two trees set up differently) ─────────
    for kind, what, _ in prof["setup"]:
        rec = run(f"setup {' '.join(what)}", [PY] + what,
                  env=base_env(DATABASE_URL=ADMIN_URL, APP_DB_USER=APP_ROLE,
                               APP_DB_PASSWORD=APP_PW))
        OUT["steps"].remove(rec)
        OUT["setup"].append(rec)
        if rec.get("rc"):
            raise RuntimeError(f"sozlash yiqildi: {rec}")
    schema_snapshot("schema")

    # ── 1. operator commands on an empty database ──────────────────────────
    run("worker --health (empty db)", X("worker") + ["--health"])
    run("worker --health --toliq (empty db)", X("worker") + ["--health", "--toliq"])
    run("sender --health (empty db)", X("sender") + ["--health"])
    run("main -y (empty db)", X("main") + ["-y"])
    run("worker --once (empty db)", X("worker") + ["--once"])
    run("worker --health (db down)", X("worker") + ["--health"],
        env=base_env(DATABASE_URL=DEAD_URL))
    run("main (no settings)", X("main") + ["-y"],
        env=base_env(DATABASE_URL=None))
    run("sender (http url)", X("sender"),
        env=base_env(TENDER_API_URL="http://example.com/set-result"))
    run("sender --health (http url)", X("sender") + ["--health"],
        env=base_env(TENDER_API_URL="http://example.com/set-result"))
    run("sender (4:2 map)", X("sender") + ["--holat"],
        env=base_env(TENDER_STATUS_XARITA="4:2"))
    run("sender (no credentials)", X("sender"),
        env=base_env(TENDER_API_LOGIN="", TENDER_API_PAROL=""))
    run("worker (bad table name)", X("worker") + ["--health"],
        env=base_env(JOBS_TABLE="files; drop table files"))

    # ── 2. API: unconfigured and database-down variants ────────────────────
    with Api(X("api"), base_env(KIRUVCHI_LOGIN="", KIRUVCHI_PAROL="")):
        api("unconfigured: post", [F(1, L("x.xlsx"), "excel1")])
        api("unconfigured: health", method="GET", path="/health")
    with Api(X("api"), base_env(DATABASE_URL=DEAD_URL)):
        api("db down: post", [T(1001, [F(1, L("x.xlsx"), "excel1")], "offeror")])
        api("db down: health", method="GET", path="/health")

    # ── 3. API: the real traffic ───────────────────────────────────────────
    with Api(X("api"), env):
        api("health (fresh)", method="GET", path="/health")
        api("auth: none", [F(1, L("a.xlsx"), "excel1")], auth=None)
        api("auth: wrong password", [F(1, L("a.xlsx"), "excel1")], auth=basic(LOGIN, "nope"))
        api("auth: wrong user", [F(1, L("a.xlsx"), "excel1")], auth=basic("x", PAROL))
        api("auth: bearer", [F(1, L("a.xlsx"), "excel1")], auth="Bearer abc")
        api("auth: broken base64", [F(1, L("a.xlsx"), "excel1")], auth="Basic %%%")
        api("auth: lowercase scheme", [], auth="basic " + AUTH[6:])
        api("body: not json", raw=b"{not json")
        api("body: string", raw=b'"hello"')
        api("body: number", raw=b"42")
        api("body: empty list", [])
        api("body: empty object", {})
        api("body: too many", [F(i, L("a.xlsx"), "excel1") for i in range(1, 62)])
        api("body: fayllar empty", {"fayllar": []})

        # customer templates — the partner's real (grouped) format
        api("templates batch", [
            T(1001, [F(9001, L("jam_shablon.xlsx"), "excel1")], lot="pudrat"),
            T(1002, [F(9002, L("narx_shablon.xlsx"), "excel3")], lot="pudrat"),
            T(1003, [F(9003, "res_shablon.xlsx", "excel2")]),
            T(1004, [F(9004, L("loy_shablon.xlsx"), "loyiha_excel")], lot="loyiha"),
            T(1005, [F(9005, "odd_shablon.xlsx", "excel1")]),
            T(1006, [F(9006, L("buzuq_zip.xlsx"), "excel1")]),
            T(1008, [F(9008, L("yoq_shablon.xlsx"), "excel1")]),
            T(1010, [F(9010, L("narx_shablon.xlsx"), "excel3"),
                     F(9011, L("narx_qayta_saqlangan.xlsx"), "excel3")]),
            T(1011, [F(9012, L("err500/narx_shablon.xlsx"), "excel3")]),
        ])
        api("templates again (duplicate)", [
            T(1001, [F(9001, L("jam_shablon.xlsx"), "excel1")], lot="pudrat")])

        # participant files — flat list, both `user_type` and `role` spellings
        P = []
        jam = ["jam_toldirilgan", "jam_qisman_9nol_1narx", "jam_faqat_nollar",
               "jam_siyrak_1narx", "jam_aynan_nusxa", "jam_qayta_saqlangan", "jam_shablon"]
        for i, n in enumerate(jam, 1):
            P.append({"file_id": i, "tender_id": 1001, "type": "excel1",
                      "user_type": "offeror", "link": L(n + ".xlsx")})
        broken = [(8, "buzuq_zip.xlsx"), (9, "buzuq_html.xlsx"), (10, "bosh_fayl.xlsx"),
                  (11, "yoq_fayl.xlsx"), (12, "err500/jam_toldirilgan.xlsx"),
                  (13, "redirect/jam_toldirilgan.xlsx")]
        for fid, n in broken:
            P.append({"file_id": fid, "tender_id": 1001, "type": "excel1",
                      "role": "offeror", "link": L(n)})
        api("participants batch 1", P)

        narx = ["narx_toldirilgan", "narx_siyrak", "narx_ustun_ochirilgan",
                "narx_qoshimcha_ustun", "narx_xato_qiymat", "narx_qayta_saqlangan",
                "narx_shablon"]
        res = ["res_toldirilgan", "res_bir_varaq", "res_qoshimcha_ustun", "res_siyrak",
               "res_shablon"]
        loy = ["loy_toldirilgan", "loy_bosh", "loy_ustun_ochirilgan", "loy_shablon"]
        api("participants batch 2 (grouped)", [
            T(1002, [F(20 + i, n + ".xlsx", "excel3") for i, n in enumerate(narx, 1)],
              "offeror", lot="pudrat"),
            T(1003, [F(30 + i, L(n + ".xlsx"), "excel2") for i, n in enumerate(res, 1)],
              "offeror", lot="pudrat"),
            T(1004, [F(40 + i, L(n + ".xlsx"), "loyiha_excel") for i, n in enumerate(loy, 1)],
              "offeror", lot="loyiha"),
            T(1005, [F(51, L("odd_toldirilgan.xlsx"), "excel1"),
                     F(52, L("odd_shablon.xlsx"), "excel1")], "offeror"),
            T(1006, [F(61, L("jam_toldirilgan.xlsx"), "excel1")], "offeror"),
            T(1007, [F(71, L("narx_toldirilgan.xlsx"), "excel3"),
                     F(72, L("narx_siyrak.xlsx"), "excel3")], "offeror"),
            T(1008, [F(81, L("jam_toldirilgan.xlsx"), "excel1")], "offeror"),
            T(1010, [F(91, L("narx_toldirilgan.xlsx"), "excel3")], "offeror"),
            T(1011, [F(95, L("narx_toldirilgan.xlsx"), "excel3")], "offeror"),
            T(1001, [F(99, L("narx_toldirilgan.xlsx"), "excel3")], "offeror"),
        ])

        # every validation branch, in one mixed request
        long_link = L("a" * 260 + ".xlsx")
        api("validation errors", [
            "not an object",
            {"tender_id": 1, "type": "excel1", "user_type": "offeror", "link": L("a.xlsx")},
            {"file_id": 0, "tender_id": 1, "type": "excel1", "user_type": "offeror", "link": L("a.xlsx")},
            {"file_id": "abc", "tender_id": 1, "type": "excel1", "user_type": "offeror", "link": L("a.xlsx")},
            {"file_id": True, "tender_id": 1, "type": "excel1", "user_type": "offeror", "link": L("a.xlsx")},
            {"file_id": -5, "tender_id": 1, "type": "excel1", "user_type": "offeror", "link": L("a.xlsx")},
            {"file_id": 1.5, "tender_id": 1, "type": "excel1", "user_type": "offeror", "link": L("a.xlsx")},
            {"file_id": 200, "type": "excel1", "user_type": "offeror", "link": L("a.xlsx")},
            {"file_id": 201, "tender_id": "0", "type": "excel1", "user_type": "offeror", "link": L("a.xlsx")},
            {"file_id": 202, "tender_id": 1, "type": "excel9", "user_type": "offeror", "link": L("a.xlsx")},
            {"file_id": 203, "tender_id": 1, "type": "excel1", "user_type": "admin", "link": L("a.xlsx")},
            {"file_id": 204, "tender_id": 1, "type": "excel1", "user_type": "offeror", "link": ""},
            {"file_id": 205, "tender_id": 1, "type": "excel1", "user_type": "offeror",
             "link": "https://evil.example.com/a.xlsx"},
            {"file_id": 206, "tender_id": 1, "type": "excel1", "user_type": "offeror",
             "link": "ftp://127.0.0.1/a.xlsx"},
            {"file_id": 207, "tender_id": 1, "type": "excel1", "user_type": "offeror",
             "link": long_link},
            {"file_id": 208, "tender_id": 1, "type": "excel1", "user_type": "offeror",
             "link": "file:///etc/passwd"},
            # file_id already used by another tender → conflict, not "takror"
            {"file_id": 1, "tender_id": 5555, "type": "excel1", "user_type": "offeror",
             "link": L("jam_toldirilgan.xlsx")},
            # file_id of a template sent as a participant → role mismatch
            {"file_id": 9001, "tender_id": 1001, "type": "excel1", "user_type": "offeror",
             "link": L("jam_shablon.xlsx")},
            # exact duplicate of participant 2 → takror
            {"file_id": 2, "tender_id": 1001, "type": "excel1", "user_type": "OFFEROR",
             "link": L("jam_qisman_9nol_1narx.xlsx")},
            # lot_type mismatch is only a warning
            {"file_id": 210, "tender_id": 1004, "type": "loyiha_excel", "user_type": "offeror",
             "lot_type": "pudrat", "link": L("loy_toldirilgan.xlsx")},
            # numeric strings are accepted
            {"file_id": "211", "tender_id": " 1004 ", "type": " loyiha_excel ",
             "user_type": " Offeror ", "link": "  loy_bosh.xlsx  "},
            {"lot_type": "pudrat", "user_type": "offeror", "tender_id": 1003, "files": []},
            {"lot_type": "pudrat", "user_type": "offeror", "tender_id": 1003,
             "files": ["x", {"file_id": 212, "link": L("res_siyrak.xlsx"), "type": "excel2"}]},
            # inner field wins over the group field
            {"user_type": "consulting", "tender_id": 1003,
             "files": [{"file_id": 213, "user_type": "offeror", "tender_id": 1003,
                        "link": L("res_toldirilgan.xlsx"), "type": "excel2"}]},
        ])
        api("shape: single object", {"file_id": 214, "tender_id": 1004, "type": "loyiha_excel",
                                     "user_type": "offeror", "link": L("loy_toldirilgan.xlsx")})
        api("shape: fayllar", {"fayllar": [{"file_id": 215, "tender_id": 1004,
                                            "type": "loyiha_excel", "role": "offeror",
                                            "link": L("loy_ustun_ochirilgan.xlsx")}]})
        api("shape: data", {"data": [{"file_id": 216, "tender_id": 1004,
                                      "type": "loyiha_excel", "user_type": "offeror",
                                      "link": L("loy_toldirilgan.xlsx")}]})
        api("shape: items with group", {"items": [T(1003, [F(217, L("res_bir_varaq.xlsx"),
                                                            "excel2")], "offeror")]})
        api("shape: single group object", T(1003, [F(218, L("res_siyrak.xlsx"), "excel2")],
                                            "offeror", lot="pudrat"))
        api("health (after traffic)", method="GET", path="/health")
    snapshot("db after intake")

    # ── 4. first worker pass ────────────────────────────────────────────────
    run("main --quruq -n 4 (dry run)", X("main") + ["--quruq", "-n", "4"])
    snapshot("db after dry run")
    run("worker --once", X("worker") + ["--once"])
    run("main --bitta", X("main") + ["--bitta", "-y"])
    run("main -n 2", X("main") + ["-n", "2", "-y"])
    run("main -y (drain)", X("main") + ["-y"])
    snapshot("db after worker pass 1")

    # ── 5. rows written straight into the database (discovery, F3) ─────────
    admin("INSERT INTO files (link, file_id, tender_id, type, status, created_at, updated_at) "
          "VALUES (%s, 500, 1001, 'excel9', 0, now(), now())", (L("jam_toldirilgan.xlsx"),))
    admin("INSERT INTO files (link, file_id, tender_id, type, status, created_at, updated_at, "
          "deleted_at) VALUES (%s, 501, 1001, 'excel1', 0, now(), now(), now())",
          (L("jam_toldirilgan.xlsx"),))
    admin("INSERT INTO files (link, file_id, tender_id, type, status, send, created_at, "
          "updated_at) VALUES (%s, 502, 1001, 'excel1', 0, 1, now(), now())",
          (L("jam_toldirilgan.xlsx"),))
    admin("INSERT INTO files (link, file_id, tender_id, type, status, created_at, updated_at) "
          "VALUES (%s, 503, 1002, 'excel3', 0, now(), now())", (L("narx_siyrak.xlsx"),))
    run("worker --health --toliq (backlog)", X("worker") + ["--health", "--toliq"])
    run("main -y (discovery)", X("main") + ["-y"])
    snapshot("db after discovery")

    # ── 6. second API wave: late template, link changes ─────────────────────
    with Api(X("api"), env):
        api("late template for 1007", [T(1007, [F(9007, L("narx_shablon.xlsx"), "excel3")])])
        api("participant link changed", [
            {"file_id": 22, "tender_id": 1002, "type": "excel3", "user_type": "offeror",
             "link": L("narx_toldirilgan.xlsx")},
            {"file_id": 11, "tender_id": 1001, "type": "excel1", "user_type": "offeror",
             "link": L("jam_toldirilgan.xlsx")},
            {"file_id": 3, "tender_id": 1001, "type": "excel1", "user_type": "offeror",
             "link": L("jam_faqat_nollar.xlsx")},
        ])
        api("template link changed (anomaly)", [
            T(1002, [F(9002, L("narx_qayta_saqlangan.xlsx"), "excel3")])])
        api("health (wave 2)", method="GET", path="/health")
    snapshot("db after wave 2")

    run("worker --once (wave 2)", X("worker") + ["--once"])
    run("main -y (wave 2)", X("main") + ["-y"])
    snapshot("db after worker pass 2")

    # ── 7. delivery to the tender platform ──────────────────────────────────
    run("sender --holat (before)", X("sender") + ["--holat"])
    run("sender --health (before)", X("sender") + ["--health"])
    with LOCK:
        TENDER["mode"] = "strict"
        TENDER["always400"] = {4}
        TENDER["fail_once_500"] = {1, 31}
        TENDER["fail_once_401"] = {21}
    for i in range(1, 5):
        run(f"sender cycle {i} (strict)", X("sender"))
    snapshot("db after strict delivery")
    run("sender --health (strict)", X("sender") + ["--health"])
    with LOCK:
        TENDER["mode"] = "normal"
    run("sender --qayta-och 3", X("sender") + ["--qayta-och", "3"])
    run("sender cycle 5", X("sender"))
    run("sender --qayta-och (all)", X("sender") + ["--qayta-och"])
    run("sender cycle 6", X("sender"))
    run("sender cycle 7", X("sender"))
    run("sender --holat (after)", X("sender") + ["--holat"])
    run("sender --health (after)", X("sender") + ["--health"])
    snapshot("db after delivery")

    # ── 8. operator tools (read-only) ───────────────────────────────────────
    run("worker --dead-letters", X("worker") + ["--dead-letters"])
    run("worker --health", X("worker") + ["--health"])
    run("worker --health --toliq", X("worker") + ["--health", "--toliq"])
    run("worker --health (as superuser)", X("worker") + ["--health"],
        env=base_env(DATABASE_URL=ADMIN_URL))
    run("worker --shablonlar", X("worker") + ["--shablonlar"])
    run("worker --etalonlar", X("worker") + ["--etalonlar"])
    run("worker --help", X("worker") + ["--help"])
    run("main --help", X("main") + ["--help"])
    run("sender --help", X("sender") + ["--help"])
    for extra in ([], ["--sxema"], ["--fayllar"], ["--fayllar", "5"], ["--shablonlar"],
                  ["--tenderlar"], ["--natijalar"], ["--kamchilik"], ["--tender", "1001"],
                  ["--sql"]):
        run("korish " + " ".join(extra), X("korish") + extra)
    for extra in ([], ["--hammasi"], ["--fayl", "1"], ["--file-id", "21"], ["--muammo"]):
        run("kuzatuv " + " ".join(extra), X("kuzatuv") + extra)
    with open(os.path.join(TREE, ".env"), "w", encoding="utf-8") as fh:
        for k, v in base_env().items():
            if k not in ("PATH", "HOME"):
                fh.write(f"{k}={v}\n")
    run("ishga_tushir --holat", X("ishga") + ["--holat"])
    run("ishga_tushir --quruq 3", X("ishga") + ["--quruq", "3"])
    os.remove(os.path.join(TREE, ".env"))
    snapshot("db after operator tools")

    # ── 9. maintenance commands (write) ────────────────────────────────────
    run("worker --shablonlarni-tayyorla", X("worker") + ["--shablonlarni-tayyorla"])
    run("worker --requeue 9", X("worker") + ["--requeue", "9"])
    run("worker --requeue unknown", X("worker") + ["--requeue", "424242"])
    snapshot("db after single requeue")
    run("main -y (after requeue)", X("main") + ["-y"])
    run("worker --requeue all", X("worker") + ["--requeue", "all"])
    snapshot("db after requeue all")
    run("main -y (after requeue all)", X("main") + ["-y"])
    run("worker --kesh-tozala", X("worker") + ["--kesh-tozala"])
    run("sender cycle 8", X("sender"))
    snapshot("db final")

    # ── 10. long-running modes stop cleanly on SIGTERM ─────────────────────
    run_signalled("main --davomiy (SIGTERM)", X("main") + ["--davomiy", "-y"])
    run_signalled("worker loop (SIGTERM)", X("worker"))
    run_signalled("sender --davomiy (SIGTERM)", X("sender") + ["--davomiy"])
    snapshot("db after SIGTERM runs")

    with LOCK:
        calls = sorted(TENDER["calls"], key=lambda c: json.dumps(c, sort_keys=True))
    step("tender platform received", kind="tender", calls=normj(calls),
         order_info=[(c["body"]["results"][0]["file_id"], c["body"]["results"][0]["status"],
                 c["answer"]) if isinstance(c["body"], dict) else c["body"]
                for c in TENDER["calls"]])


def yurgiz(admin_url, saqla=False):
    """Ssenariyni yurgizadi. Qaytadi: {"steps": [...], "setup": [...], "seconds": N}."""
    sozla(admin_url)
    OUT["steps"].clear()
    OUT["setup"].clear()
    HITS.clear()
    TENDER.update({"calls": [], "seen": {}, "mode": "normal", "always400": set(),
                   "fail_once_500": set(), "fail_once_401": set()})
    os.makedirs(TREE)
    for nom in ("app", "tender_engine"):
        shutil.copytree(os.path.join(REPO, nom), os.path.join(TREE, nom),
                        ignore=shutil.ignore_patterns("__pycache__"))
    for nom in os.listdir(REPO):
        if nom.endswith(".py"):
            shutil.copy(os.path.join(REPO, nom), os.path.join(TREE, nom))
    os.makedirs(DATA)

    admin(f"DROP DATABASE IF EXISTS {DB} WITH (FORCE)", url=MAINT_URL)
    admin(f"DROP ROLE IF EXISTS {APP_ROLE}", url=MAINT_URL)
    admin(f"CREATE DATABASE {DB}", url=MAINT_URL)
    servers = [serve(FileHandler, "127.0.0.1", P_FILES), serve(FileHandler, "127.0.0.2", P_FILES),
               serve(TenderHandler, "127.0.0.1", P_TENDER)]
    t0 = time.time()
    try:
        scenario(PROFIL)
    finally:
        for srv in servers:
            srv.shutdown()
            srv.server_close()
        if not saqla:
            admin(f"DROP DATABASE IF EXISTS {DB} WITH (FORCE)", url=MAINT_URL)
            admin(f"DROP ROLE IF EXISTS {APP_ROLE}", url=MAINT_URL)
            shutil.rmtree(RUN, ignore_errors=True)
    OUT["seconds"] = round(time.time() - t0)
    return json.loads(json.dumps(OUT, default=str))


#: Xulq kalitlari — solishtiriladi. Matn (`out`, `err`) solishtirilmaydi.
XULQ = ("status", "body", "www", "rc", "tables", "hits", "dump", "grants", "colgrants",
        "seqs", "role", "owners", "dbconnect", "calls", "posts")


def izlar(natija):
    """Har qadam xulqining barmoq izi: [[nomi, sha256], ...]."""
    import hashlib
    out = []
    for st in natija["steps"]:
        xulq = {k: st[k] for k in XULQ if k in st}
        out.append([st["name"], hashlib.sha256(
            json.dumps(xulq, sort_keys=True, ensure_ascii=False).encode()).hexdigest()])
    return out
