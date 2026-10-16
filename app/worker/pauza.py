# -*- coding: utf-8 -*-
"""Ommaviy yuklab olish nosozligida PAUZA (2026-10-16).

MUAMMO. Yuklab olish xatosi har faylni ALOHIDA aybdor deb biladi: 404 va
«Excel emas» faylni BITTA urinishda o'ldiradi, vaqtinchalik xatolar urinishlarni
sarflaydi. Saqlash tizimi yotsa yoki `FILE_BASE_URL` / `RUXSAT_HOSTLAR`
noto'g'ri bo'lsa butun navbat bir necha daqiqada «o'lik» bo'lardi va uni faqat
operatorning `--requeue all` buyrug'i qaytarardi.

YECHIM. Ketma-ket `PAUZA_KETMA_KET` ta yuklab olish xatosi (orada bironta
muvaffaqiyatli yuklab olish yo'q), kamida `PAUZA_TENDERLAR` ta TURLI tenderdan —
bu fayllar emas, MANBA nosoz degani. Bitta tenderning fayllari (masalan sherik
o'sha tenderni o'chirgan) pauzani ochmaydi. Pauza ochilganda:

  • seriyadagi (pauzadan oldin xato bergan) fayllar qaytariladi —
    `queue.pauza_tirilt`: o'lim bekor, urinish qaytariladi, status 4 → 0;
  • pauza davomida xato bergan fayl JAZOSIZ navbatga qaytadi —
    `queue.pauzada_qaytar` (o'lmaydi, urinish sarflanmaydi);
  • worker har `PAUZA_SINOV_INTERVAL` soniyada BITTA fayl oladi — o'sha
    faylning yuklab olinishi manbaning sinovi;
  • birinchi muvaffaqiyatli yuklab olish pauzani yopadi va qaytarilgan
    fayllar darhol navbatga tushadi (`queue.pauza_uygot`).

Pauza ochilishi `error` darajasidagi log satri — audit bot uni Telegram'ga
yuboradi. Holat JARAYON xotirasida: worker qayta ishga tushsa seriya noldan
boshlanadi (nosozlik davom etsa — yana shuncha xatodan keyin pauza).
Yuklab olishga TEGISHLI bo'lmagan xatolar (o'qish, etalon, hukm) bu yerga
kelmaydi — ular faylning o'ziga tegishli.
"""

import time

from app import config
from app.log import log

_S = config.worker()
KETMA_KET = _S.pauza_ketma_ket
TENDERLAR = _S.pauza_tenderlar
SINOV_INTERVAL = _S.pauza_sinov_interval
#: Pauzada qaytarilgan fayl shuncha soniyadan keyin yana sinov nomzodi bo'ladi.
#: Sinov intervalidan ancha katta — keyingi sinov BOSHQA faylni olsin.
QAYTISH_SONIYA = max(SINOV_INTERVAL, 1) * 10
#: Seriya xotirasi chegarasi (uzoq nosozlikda ro'yxat cheksiz o'smasin).
_SERIYA_MAKS = 5000


class _Holat:
    def __init__(self):
        self.seriya = []          # [(uuid, tender_id)] — oxirgi muvaffaqiyatdan beri
        self.faol = False
        self.boshlandi = None     # time.monotonic()


_H = _Holat()


def faolmi():
    return _H.faol


def seriya_uuidlari():
    return [u for u, _t in _H.seriya]


def xato(uuid, tender_id):
    """Yuklab olish xatosi qayd etiladi.

    Qaytaradi:
      None     — pauza yo'q: xato odatdagidek (fayl darajasida) ishlansin;
      "yangi"  — shu xato pauzani OCHDI (chaqiruvchi seriyani qaytaradi);
      "faol"   — pauza allaqachon ochiq.
    """
    if KETMA_KET <= 0:
        return None
    if _H.faol:
        return "faol"
    _H.seriya.append((str(uuid), tender_id))
    if len(_H.seriya) > _SERIYA_MAKS:
        del _H.seriya[:-_SERIYA_MAKS]
    tenderlar = {t for _u, t in _H.seriya}
    if len(_H.seriya) >= KETMA_KET and len(tenderlar) >= max(TENDERLAR, 1):
        _H.faol = True
        _H.boshlandi = time.monotonic()
        return "yangi"
    return None


def muvaffaqiyat():
    """Yuklab olish muvaffaqiyatli — seriya uziladi. Qaytaradi: pauza YOPILDIMI."""
    yopildi = _H.faol
    _H.seriya.clear()
    _H.faol = False
    davom = time.monotonic() - _H.boshlandi if (yopildi and _H.boshlandi) else 0
    _H.boshlandi = None
    if yopildi:
        log(f"[PAUZA tugadi] yuklab olish tiklandi ({int(davom // 60)} daqiqa "
            f"pauzada) — navbat odatdagidek davom etadi")
    return yopildi


def tenderlar_soni():
    return len({t for _u, t in _H.seriya})


def kut(toxtash):
    """Pauzada: keyingi sinovgacha `SINOV_INTERVAL` soniya kutadi.

    `toxtash()` True bo'lsa darhol qaytadi (SIGTERM — `docker stop`).
    """
    for _ in range(max(SINOV_INTERVAL, 1)):
        if toxtash() or not _H.faol:
            return
        time.sleep(1)


def _tozala():
    """Testlar uchun: holatni boshlang'ich ko'rinishga qaytaradi."""
    _H.seriya.clear()
    _H.faol = False
    _H.boshlandi = None
