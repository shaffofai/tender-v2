# -*- coding: utf-8 -*-
"""api_server — tender tizimidan fayl ma'lumotini qabul qiluvchi API.

2026-09-23 dan boshlab tender tizimining bazaga to'g'ridan-to'g'ri kirishi
to'xtatildi. Fayl haqidagi ma'lumot SHU API orqali keladi:

    POST /api-v2/tender-v2/check
    Authorization: Basic <base64(login:parol)>

    {"file_id": 1, "tender_id": 2, "link": "...", "type": "excel1",
     "role": "offeror"}

`role` bo'yicha marshrutlanadi:
    consulting -> `templates` (buyurtmachi shabloni)
    offeror    -> `files` + `jobs_state` (tekshiruv navbati)

API bazaga yozgach DARHOL javob qaytaradi — tekshirilishini kutmaydi.

Holat: GET /api-v2/tender-v2/health. Ikkala yo'l prefikssiz ham ishlaydi
(`/check`, `/health`): kompaniya edge'i prefiksni kesib yuboradi — `health`
oldidagi izohga qarang.

So'rov jurnali: har so'rov `sorov_jurnali` ga yoziladi (`JurnalOraliq` —
ishlovchilarga tegmaydi); o'qish — GET /api-v2/tender-v2/jurnal, O'Z kalitlari
bilan (app/api/jurnal.py).

Ishga tushirish:
    uvicorn api_server:app --host 0.0.0.0 --port 8000

Sozlamalar (.env): KIRUVCHI_LOGIN, KIRUVCHI_PAROL, MAX_TOPLAM, DOCS_ENABLED,
RUXSAT_HOSTLAR, FILE_BASE_URL, JURNAL_LOGIN, JURNAL_PAROL, JURNAL_YOQILGAN
+ baza ulanishi (DATABASE_URL yoki DB_*).
"""

import threading
from contextlib import asynccontextmanager

import psycopg
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app import config, jurnal
from app.api import models
from app.api.auth import KIRUVCHI_LOGIN, KIRUVCHI_PAROL, _auth_ok
from app.api.intake import elementlarni_ajrat, elementni_tekshir
from app.api.jurnal import JurnalOraliq, royxatni_ol, yozuvni_ol
from app.api.store import ULANISH, _shablon_uygot, elementni_yoz
from app.db import safe_dsn
from app.log import log

MAX_TOPLAM = config.api().max_toplam


# ---------------------------------------------------------------------------
# Hisoblagichlar (C3) — tender bizning javobimizni o'qimasligi mumkin
# ---------------------------------------------------------------------------
# Agar ular `natijalar` massivini parse qilmasa, biz rad etgan element JIMGINA
# yo'qoladi: fayl hech qachon tekshirilmaydi va hech kim bilmaydi. Shuning
# uchun har rad etilgan element ERROR bilan log'ga yoziladi va shu yerda
# sanaladi — `/health` da ko'rinadi.
HISOB = {"qabul": 0, "yangilandi": 0, "takror": 0, "xato": 0, "sorov": 0}
_HISOB_QULF = threading.Lock()


def _sana(kalit, n=1):
    with _HISOB_QULF:
        HISOB[kalit] = HISOB.get(kalit, 0) + n


# ---------------------------------------------------------------------------
# FastAPI
# ---------------------------------------------------------------------------
@asynccontextmanager
async def _hayot(_app):
    """Ishga tushish/to'xtash (FastAPI lifespan)."""
    # Birinchi bo'lib: pastdagi ishga tushish satrlari ham jurnalga tushsin.
    # Baza yotgan bo'lsa ham kutmaydi — yozuvchi oqim o'zi qayta ulanadi.
    jurnal.boshla("api")
    if not KIRUVCHI_LOGIN or not KIRUVCHI_PAROL:
        log("KIRUVCHI_LOGIN/KIRUVCHI_PAROL sozlanmagan — hamma so'rov 401 bo'ladi",
            "error")
    try:
        with ULANISH.qulf:
            ULANISH.ol()
        log("[api] baza ulanishi tayyor: %s" % safe_dsn())
    except Exception as exc:                      # pragma: no cover
        # FATAL emas: baza vaqtincha yotgan bo'lishi mumkin, so'rovda qayta
        # urinamiz va 503 qaytaramiz. Konteyner cheksiz restart bo'lmasin.
        log("[api] baza hali tayyor emas: %s" % exc, "warning")
    yield
    ULANISH.yop()
    jurnal.toxtat()


# /docs, /redoc, /openapi.json — standart YOPIQ (DOCS_ENABLED=1 ochadi).
_DOCS = config.api().docs_enabled
app = FastAPI(title="Tender fayl qabul API", version="1.0", lifespan=_hayot,
              docs_url="/docs" if _DOCS else None,
              redoc_url="/redoc" if _DOCS else None,
              openapi_url="/openapi.json" if _DOCS else None)

# So'rov jurnali — sof ASGI qatlam: javobga hech narsa qo'shmaydi, ishlovchilar
# o'zgarmagan. Yagona oraliq qatlam, ya'ni ishlovchi istisnosini ham ko'radi.
app.add_middleware(JurnalOraliq)


# Har tashqi yo'l ikki shaklda keladi, ikkalasi ham bitta ishlovchiga:
#   `/api-v2/tender-v2/<yo'l>` — to'liq yo'l (gateway router shunday uzatadi);
#   `/<yo'l>`                  — kompaniya edge'i `/api-v2/tender-v2` prefiksini
#                                KESIB yuboradi (2026-09-29 da tekshirildi:
#                                `.../api-v2/tender-v2/api-v2/tender-v2/check` → 401).
# Edge tuzatilsa ham hech narsa buzilmaydi. `/health` — konteyner healthcheck'i ham.
@app.get("/health", responses={200: {"model": models.HealthJavob},
                                 503: {"model": models.HealthJavob}})
@app.get("/api-v2/tender-v2/health", responses={200: {"model": models.HealthJavob},
                                                 503: {"model": models.HealthJavob}})
def health():
    holat = {"xizmat": "api_server", "hisob": dict(HISOB)}
    try:
        with ULANISH.qulf:
            conn = ULANISH.ol()
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
            conn.rollback()
        holat["baza"] = "ok"
    except Exception as exc:
        holat["baza"] = "xato: %s" % exc
        return JSONResponse(holat, status_code=503)
    if not KIRUVCHI_LOGIN or not KIRUVCHI_PAROL:
        holat["ogohlantirish"] = "KIRUVCHI_LOGIN/KIRUVCHI_PAROL sozlanmagan"
    return holat


@app.post("/api-v2/tender-v2/check",
          responses={200: {"model": models.CheckJavob},
                     400: {"model": models.XatoJavob},
                     401: {"model": models.XatoJavob},
                     413: {"model": models.XatoJavob},
                     503: {"model": models.XatoJavob}},
          openapi_extra={"requestBody": {"required": True, "content": {
              "application/json": {"examples": models.SOROV_NAMUNALARI}}}})
@app.post("/check", include_in_schema=False)          # edge prefiksni kesgan yo'l
async def fayllarni_qabul_qil(request: Request):
    ok, sabab = _auth_ok(request)
    if not ok:
        return JSONResponse(
            {"xato": sabab}, status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="tender"'})

    try:
        tana = await request.json()
    except Exception:
        return JSONResponse({"xato": "JSON tahlil qilinmadi"}, status_code=400)

    elementlar = elementlarni_ajrat(tana)
    if elementlar is None:
        return JSONResponse(
            {"xato": "kutilgan shakl: obyekt, massiv yoki {\"fayllar\": [...]}"},
            status_code=400)
    if not elementlar:
        return JSONResponse({"xato": "bo'sh ro'yxat"}, status_code=400)
    if len(elementlar) > MAX_TOPLAM:
        return JSONResponse(
            {"xato": "elementlar soni %d — chegara %d" % (len(elementlar), MAX_TOPLAM)},
            status_code=413)

    _sana("sorov")
    natijalar = []
    hisob = {"qabul": 0, "yangilandi": 0, "takror": 0, "xato": 0}

    try:
        with ULANISH.qulf:
            conn = ULANISH.ol()
            shablon_keldi = False
            for el in elementlar:
                toza, xato = elementni_tekshir(el)
                if xato:
                    fid = el.get("file_id") if isinstance(el, dict) else None
                    natijalar.append({"file_id": fid, "holat": "xato", "sabab": xato})
                    hisob["xato"] += 1
                    # ERROR bilan: tender javobimizni o'qimasa ham BIZ bilamiz.
                    log("[qabul qilinmadi] %s | element=%r" % (xato, el), "error")
                    continue
                try:
                    holat, yangi_id, yoz_xato = elementni_yoz(conn, toza)
                except psycopg.Error as exc:
                    conn.rollback()
                    natijalar.append({"file_id": toza["file_id"], "holat": "xato",
                                      "sabab": "bazaga yozilmadi"})
                    hisob["xato"] += 1
                    log("[qabul qilinmadi] baza xatosi: %s | file_id=%s"
                        % (exc, toza["file_id"]), "error")
                    continue
                if yoz_xato:
                    natijalar.append({"file_id": toza["file_id"], "holat": "xato",
                                      "sabab": yoz_xato})
                    hisob["xato"] += 1
                    log("[qabul qilinmadi] %s" % yoz_xato, "error")
                    continue
                javob = {"file_id": toza["file_id"], "holat": holat}
                if yangi_id is not None:
                    javob["id"] = yangi_id
                natijalar.append(javob)
                hisob["qabul" if holat == "qabul_qilindi" else
                      ("yangilandi" if holat == "yangilandi" else "takror")] += 1
                if toza["role"] == "consulting" and holat != "takror":
                    shablon_keldi = True
            conn.commit()
    except (psycopg.OperationalError, psycopg.InterfaceError) as exc:
        log("[api] baza ulanmadi: %s" % exc, "error")
        return JSONResponse({"xato": "baza mavjud emas"}, status_code=503)

    for k, v in hisob.items():
        if v:
            _sana(k, v)

    # COMMIT dan KEYIN — shablon `templates` da ko'rinib turgan bo'lsin.
    if shablon_keldi:
        _shablon_uygot()

    return {"jami": len(elementlar), "qabul": hisob["qabul"],
            "yangilandi": hisob["yangilandi"], "takror": hisob["takror"],
            "xato": hisob["xato"], "natijalar": natijalar}


# So'rov jurnalini o'qish — O'Z kalitlari bilan (JURNAL_LOGIN / JURNAL_PAROL):
# sherikning KIRUVCHI_* kalitlari bu yerda ISHLAMAYDI. Sinxron `def` —
# threadpool'da: o'qish hodisa siklini (`/check`) ham, `ULANISH.qulf` ni ham
# band qilmaydi (app/api/jurnal.py). Filtrlar qo'lda tahlil qilinadi — xato
# `/check` dagidek `{"xato": ...}` bilan qaytadi.
@app.get("/api-v2/tender-v2/jurnal",
         responses={400: {"model": models.XatoJavob},
                    401: {"model": models.XatoJavob},
                    503: {"model": models.XatoJavob}})
@app.get("/jurnal", include_in_schema=False)          # edge prefiksni kesgan yo'l
def jurnal_royxati(request: Request):
    return royxatni_ol(request)


@app.get("/api-v2/tender-v2/jurnal/{jid}",
         responses={401: {"model": models.XatoJavob},
                    404: {"model": models.XatoJavob},
                    503: {"model": models.XatoJavob}})
@app.get("/jurnal/{jid}", include_in_schema=False)
def jurnal_yozuvi(request: Request, jid: str):
    return yozuvni_ol(request, jid)
