# -*- coding: utf-8 -*-
"""Ichki status → tender statusi. Texnik holat (0/4) HECH QACHON 2 ga
o'girilmaydi — OLTIN QOIDA (xarita shunday bo'lsa xizmat ishga tushmaydi).

(Ilgari `yuboruvchi.py` da — ko'chirilgan.)
"""

from app import config
from tender_engine import evidence as _evidence

_S = config.yuboruvchi()


# ── Tender qabul qiladigan statuslar — ULARNING VALIDATORIDAN (2026-09-23) ────
# Sherik `set-result` ning Laravel validatorini ko'rsatdi:
#     'results'          => 'required|array',
#     'results.*.file_id'=> 'required|integer|exists:files,id',
#     'results.*.status' => 'required|integer|in:1,2',      ← 0 va 3 RAD ETILADI
#     'results.*.comment'=> 'required|string',              ← bo'sh bo'lmasin
# Ya'ni buyurtmachi «0 va 3 ni tushunadi» degan bo'lsa ham, KOD hozircha
# faqat 1,2 ni oladi. Standart qiymatlar shu DALILGA mos; sherik validatorni
# `in:0,1,2,3` qilgach bitta env o'zgaradi: TENDER_QABUL_STATUSLAR=0,1,2,3
# va TENDER_STATUS_XARITA= (bo'sh).
def _statuslar(qiymat):
    return frozenset(int(x) for x in qiymat.replace(";", ",").split(",") if x.strip())


QABUL_STATUSLAR = _statuslar(_S.tender_qabul_statuslar)


#: `ichki:tashqi[,ichki:tashqi]` — yuborishdan oldin status o'giriladi.
#: Standart `3:2`: aynan nusxa (3) 2026-09-18 gacha aynan 2 (rad) edi va
#: `comment` («Buyurtmachi fayli bilan aynan bir xil») sababni aytadi — ma'no
#: yo'qolmaydi. 0 (texnik) HECH QACHON 2 ga o'girilmaydi — OLTIN QOIDA:
#: texnik xato rad emas; tender 0 ni olmasa, texnik xabar shunchaki yuborilmaydi.
def _xarita(qiymat):
    x = {}
    for juft in qiymat.replace(";", ",").split(","):
        if ":" in juft:
            a, b = juft.split(":", 1)
            x[int(a)] = int(b)
    for texnik in (0, 4):
        if x.get(texnik) == 2:
            raise SystemExit(f"TENDER_STATUS_XARITA: {texnik} ni 2 ga o'girish TAQIQLANGAN — "
                             "texnik holat rad javob emas (OLTIN QOIDA)")
    return x


STATUS_XARITA = _xarita(_S.tender_status_xarita)


#: Texnik, taslim — `files.status` va tenderga boradigan kod (buyurtmachi, 2026-09-23).
STATUS_TEXNIK = _evidence.STATUS_TEXNIK


#: `comment` bo'sh kelsa (`required|string` yiqiladi) — status bo'yicha zaxira matn.
IZOH_ZAXIRA = {
    1: "Hujjat to'g'ri to'ldirilgan.",
    2: "Hujjatda kamchilik aniqlandi.",
    3: "Buyurtmachi fayli bilan aynan bir xil.",
    0: "Hujjatni avtomatik tekshirish texnik sabab bilan yakunlanmadi.",
    STATUS_TEXNIK: "Hujjatni avtomatik tekshirish texnik sabab bilan yakunlanmadi.",
}


def tashqi_status(status):
    """Ichki status → tenderga yuboriladigan status (xarita bo'yicha)."""
    return STATUS_XARITA.get(int(status), int(status))


def status_qabulmi(tashqi):
    return int(tashqi) in QABUL_STATUSLAR
