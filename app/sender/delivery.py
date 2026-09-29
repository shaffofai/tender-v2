# -*- coding: utf-8 -*-
"""Bitta xabarni tender tizimiga yuborish — HTTP mijoz va yuk.

(Ilgari `yuboruvchi.py` da — ko'chirilgan.)
"""

import re

import httpx

from app import config
from app.sender.statuses import (
    IZOH_ZAXIRA,
    QABUL_STATUSLAR,
    status_qabulmi,
    tashqi_status,
)

_S = config.yuboruvchi()
TENDER_API_URL = _S.tender_api_url
TENDER_API_LOGIN = _S.tender_api_login
TENDER_API_PAROL = _S.tender_api_parol
TENDER_TIMEOUT = _S.tender_timeout


# ---------------------------------------------------------------------------
# 2) HTTP yuborish
# ---------------------------------------------------------------------------
class _Mijoz:
    """Bitta `httpx.Client` — ulanish qayta ishlatiladi. Testda almashtiriladi."""
    _c = None

    @classmethod
    def ol(cls):
        if cls._c is None:
            cls._c = httpx.Client(timeout=TENDER_TIMEOUT,
                                  auth=(TENDER_API_LOGIN, TENDER_API_PAROL),
                                  headers={"User-Agent": "tender-ai-yuboruvchi/1.0"})
        return cls._c


def javob_muvaffaqiyatlimi(resp):
    """Muvaffaqiyat mezoni — FAQAT HTTP 200 (S1). Javob tanasi o'qilmaydi.

    Kelajakda tender tanada `{"success": false}` kabi belgi qaytaradigan
    bo'lsa, `TENDER_JAVOB_KALITI` env bilan SHU YERGA qo'shiladi — boshqa
    joyga tegilmaydi.
    """
    return resp.status_code == 200


def _xato_matni(exc_yoki_resp):
    """`xato` ustuniga yoziladigan qisqa matn. URL/parol bosilmaydi (§8.2)."""
    if isinstance(exc_yoki_resp, httpx.Response):
        tana = (exc_yoki_resp.text or "")[:200].replace("\n", " ")
        return f"HTTP {exc_yoki_resp.status_code}: {tana}"
    m = f"{type(exc_yoki_resp).__name__}: {exc_yoki_resp}"
    # Basic Auth kaliti ba'zan URL/xato matnida chiqib qoladi
    m = re.sub(r"://[^@\s]*@", "://***@", m)
    return m[:500]


#: `yubor` ning maxsus kodi: tender bu statusni QABUL QILMAYDI — so'rov
#: yuborilmaydi, xabar `holat=3` (tashlandi) bo'ladi, sababi `xato` da.
KOD_STATUS_RUXSATSIZ = -1


def payload(file_id, status, comment):
    """Tender validatoriga AYNAN mos yuk: `{"results": [{file_id, status, comment}]}`.

    Bitta xabar = bitta element. Massivda bir nechta yuborish mumkin edi, lekin
    bittasi `exists:files,id` da yiqilsa butun to'plam 422 bo'ladi — xabarma-xabar
    holat kuzatish uchun har xabar alohida so'rov.
    """
    st = tashqi_status(status)
    matn = (comment or "").strip() or IZOH_ZAXIRA.get(int(status), IZOH_ZAXIRA[2])
    return {"results": [{"file_id": int(file_id), "status": st, "comment": matn}]}


def yubor(xabar):
    """Bitta xabarni yuboradi. Qaytadi: (kod, xato_matni).

    kod: HTTP kodi; 0 — tarmoq/timeout (HTTP javobsiz);
    KOD_STATUS_RUXSATSIZ — status tender tomonidan qabul qilinmaydi (yuborilmadi).
    """
    _id, file_id, status, comment = xabar
    yuk = payload(file_id, status, comment)
    st = yuk["results"][0]["status"]
    if not status_qabulmi(st):
        return KOD_STATUS_RUXSATSIZ, (
            f"status {st} tender tomonidan qabul qilinmaydi "
            f"(TENDER_QABUL_STATUSLAR={','.join(map(str, sorted(QABUL_STATUSLAR)))}) — "
            f"sherik validatorini kengaytirsin yoki TENDER_STATUS_XARITA sozlansin")
    try:
        resp = _Mijoz.ol().post(TENDER_API_URL, json=yuk)
    except httpx.HTTPError as exc:
        return 0, _xato_matni(exc)
    if javob_muvaffaqiyatlimi(resp):
        return 200, ""
    return resp.status_code, _xato_matni(resp)
