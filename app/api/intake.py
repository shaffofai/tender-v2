# -*- coding: utf-8 -*-
"""Kiruvchi yukni normallashtirish va tekshirish — bazaga tegmaydi.

Qabul qilinadigan shakllar (hammasi o'zgarishsiz qoldirildi — sherik
integratsiyasi va qo'lda sinovlar ularga tayanadi):
    * sherikning guruhlangan massivi: [{lot_type, user_type, tender_id, files: [...]}]
    * bitta obyekt, tekis massiv, {"fayllar"|"files"|"data"|"items": [...]}

(Ilgari `api_server.py` da — ko'chirilgan.)
"""

from app import download as _w
from app import templates as templates_db
from app.log import log


#: `templates.link` / `files.link` — varchar(255). To'liq (normallashtirilgan)
#: qiymat shu chegaradan oshsa INSERT baza xatosi bilan yiqilardi.
MAX_LINK = 255

ROLLAR = ("consulting", "offeror")
TURLAR = tuple(templates_db.TYPE_ROL.keys()) or (
    "excel1", "excel2", "excel3", "loyiha_excel")


#: Sherik guruh darajasida yuboradigan maydonlar — ichki fayl elementiga
#: ko'chiriladi (ichkarida o'zi bo'lsa, ichkisi ustun).
_GURUH_MAYDONLARI = ("tender_id", "user_type", "role", "lot_type")


def _guruhmi(el):
    """Sherik formati: `{"lot_type","user_type","tender_id","files":[...]}`."""
    return isinstance(el, dict) and isinstance(el.get("files"), list) and (
        "tender_id" in el or "user_type" in el or "role" in el or "lot_type" in el)


def elementlarni_ajrat(tana):
    """Kelgan yukni TEKIS fayl ro'yxatiga keltiradi. None — shakl noto'g'ri.

    Sherikning HAQIQIY formati (2026-09-23 da yubordi) — tender bo'yicha
    GURUHLANGAN massiv, `tender_id`/`user_type`/`lot_type` guruhda, fayllar
    `files` ichida:

        [{"lot_type": "pudrat", "user_type": "offeror", "tender_id": 12345,
          "files": [{"file_id": 123, "link": "...", "type": "excel1"}, ...]},
         {"lot_type": "loyiha", ... "files": [{"file_id": 223, ...}]}]

    Har guruh ichki fayllarga YOYILADI: guruh maydonlari har faylga
    ko'chiriladi. Shu bilan birga eski tekis shakl (bitta obyekt, massiv,
    `{"fayllar": [...]}`) ham ishlayveradi — testlar va qo'lda sinov uchun.

    `MAX_TOPLAM` YOYILGAN fayllar soniga qaraydi, guruhlar soniga emas.
    """
    if isinstance(tana, dict):
        if _guruhmi(tana):
            royxat = [tana]
        else:
            royxat = None
            for kalit in ("fayllar", "files", "data", "items"):
                if isinstance(tana.get(kalit), list):
                    royxat = tana[kalit]
                    break
            if royxat is None:
                royxat = [tana]
    elif isinstance(tana, list):
        royxat = tana
    else:
        return None

    tekis = []
    for el in royxat:
        if not _guruhmi(el):
            tekis.append(el)
            continue
        guruh = {k: el[k] for k in _GURUH_MAYDONLARI if k in el}
        fayllar = el["files"]
        if not fayllar:
            # Bo'sh guruh — javobda ko'rinsin, jim yo'qolmasin
            tekis.append({"_guruh_xato": "files: bo'sh ro'yxat", **guruh})
            continue
        for f in fayllar:
            if not isinstance(f, dict):
                tekis.append({"_guruh_xato": "files elementi obyekt emas", **guruh})
                continue
            birlashgan = dict(guruh)
            birlashgan.update(f)             # ichki maydon ustun
            tekis.append(birlashgan)
    return tekis


def _butun(qiymat):
    """Butun son > 0 bo'lsa qaytaradi, aks holda None. Satr ham qabul qilinadi."""
    if isinstance(qiymat, bool):          # `True` — 1 emas
        return None
    if isinstance(qiymat, int):
        return qiymat if qiymat > 0 else None
    if isinstance(qiymat, str) and qiymat.strip().isdigit():
        n = int(qiymat.strip())
        return n if n > 0 else None
    return None


#: `lot_type` → kutilgan `type` lar (buyurtmachi: loyiha 1 fayl, pudrat 3 fayl).
#: FAQAT ogohlantirish uchun — rad etilmaydi: har fayl mustaqil tekshiriladi
#: (3-BOSQICH qarori), sherikning slot xatosi hujjat aybi emas.
_LOT_TURLARI = {"pudrat": ("excel1", "excel2", "excel3"),
                "loyiha": ("loyiha_excel",)}


def elementni_tekshir(el):
    """(tozalangan_dict, xato_matni) — biri None bo'ladi."""
    if not isinstance(el, dict):
        return None, "element JSON obyekt bo'lishi kerak"
    if el.get("_guruh_xato"):
        return None, el["_guruh_xato"]

    file_id = _butun(el.get("file_id"))
    if file_id is None:
        return None, "file_id: butun son > 0 bo'lishi kerak"
    tender_id = _butun(el.get("tender_id"))
    if tender_id is None:
        return None, "tender_id: butun son > 0 bo'lishi kerak"

    typ = str(el.get("type") or "").strip()
    if typ not in TURLAR:
        return None, "type: kutilgan qiymatlar — %s" % ", ".join(TURLAR)

    # Sherik `user_type` deb yuboradi; `role` ham qabul qilinadi.
    role = str(el.get("user_type") or el.get("role") or "").strip().lower()
    if role not in ROLLAR:
        return None, "user_type: kutilgan qiymatlar — %s" % ", ".join(ROLLAR)

    lot = str(el.get("lot_type") or "").strip().lower()
    if lot and lot in _LOT_TURLARI and typ not in _LOT_TURLARI[lot]:
        log("[ogohlantirish] lot_type=%s, lekin type=%s (file_id=%s) — rad etilmadi, "
            "har fayl mustaqil tekshiriladi" % (lot, typ, file_id), "warning")

    xom_link = str(el.get("link") or "").strip()
    if not xom_link:
        return None, "link: bo'sh"

    # Havola BAZAGA HAR DOIM TO'LIQ ko'rinishda yoziladi.
    #
    # Nega: `common.toliq_havola` yuklab olish paytida nisbiy qiymatga
    # `FILE_BASE_URL` ni QAYTADAN qo'shadi. Xom saqlansa, API tekshirgan manzil
    # bilan haqiqatda yuklanadigan manzil bir xil bo'lmasligi mumkin
    # (`FILE_BASE_URL` o'zgarsa oq ro'yxat butunlay chetlab o'tiladi), va
    # `toliq_havola` dagi `os.path.isfile` sinovi tasodifan lokal faylga
    # to'g'ri kelib diskni o'qishi mumkin.
    link = _w.toliq_havola(xom_link)
    ok, sabab = _w.host_ruxsatmi(link)
    if not ok:
        return None, "link: %s" % sabab
    if len(link) > MAX_LINK:
        return None, "link: %d belgi — chegara %d" % (len(link), MAX_LINK)

    return {"file_id": file_id, "tender_id": tender_id, "type": typ,
            "role": role, "link": link}, None
