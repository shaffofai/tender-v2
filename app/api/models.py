# -*- coding: utf-8 -*-
"""Javob shakllari — FAQAT hujjat (/docs, DOCS_ENABLED=1) uchun.

Marshrutlar javobni avvalgidek dict/JSONResponse bilan qaytaradi; bu modellar
`responses=` orqali OpenAPI ga tushadi, ish vaqtida hech narsani tekshirmaydi
va o'zgartirmaydi (javob tarkibi o'zgarmasin — sherik unga tayanadi).
"""

from typing import Literal, Optional

from pydantic import BaseModel


class ElementNatija(BaseModel):
    file_id: Optional[int] = None
    holat: Literal["qabul_qilindi", "yangilandi", "takror", "xato"]
    id: Optional[int] = None          # yangi/yangilangan qator (files.id yoki templates.id)
    sabab: Optional[str] = None       # faqat holat="xato" bo'lganda


class CheckJavob(BaseModel):
    jami: int
    qabul: int
    yangilandi: int
    takror: int
    xato: int
    natijalar: list[ElementNatija]


class XatoJavob(BaseModel):
    xato: str


class HealthJavob(BaseModel):
    xizmat: str
    hisob: dict[str, int]
    baza: str
    ogohlantirish: Optional[str] = None


#: So'rov tanasi — FastAPI tekshirmaydi (yuk qo'lda tahlil qilinadi, to'rt
#: shakl qabul qilinadi), shuning uchun hujjatda faqat namunalar.
SOROV_NAMUNALARI = {
    "sherik_formati": {
        "summary": "Sherikning guruhlangan formati (asosiy)",
        "value": [{"lot_type": "pudrat", "user_type": "offeror", "tender_id": 12345,
                   "files": [{"file_id": 123, "type": "excel1",
                              "link": "12345/excel1/9518/fayl.xlsx"}]}],
    },
    "bitta_obyekt": {
        "summary": "Bitta obyekt (qo'lda sinov uchun)",
        "value": {"file_id": 1, "tender_id": 2, "type": "excel1", "role": "offeror",
                  "link": "https://apisitender.mc.uz/storage/2/excel1/1/fayl.xlsx"},
    },
}
