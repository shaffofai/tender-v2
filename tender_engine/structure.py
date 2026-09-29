# -*- coding: utf-8 -*-
"""Tuzilma moslashtirish — varaq juftlash va ustun moslashtirish.

(Ilgari `set_validator` ichida; bu modul unga fasad edi. Endi kod shu
yerda — ko'chirilgan, o'zgartirilmagan.)

  _varaq_moslash  — 4 bosqichli varaq juftlash (aniq nom → normallashgan →
                    mazmun imzosi ≥0.6 → yakka-yakka majburiy juftlash)
  _lcs_col_map    — ustun moslashuvi (busiz katak-diff qurib bo'lmaydi)
Kelajak: bipartite ko'p-ko'p juftlash (1→4 varaq qayta tuzilgan hujjatlar,
masalan 582/588/38238) — REVIEW sinfiga chiqarish uchun.
"""

import re

from tender_engine.normalize import normk_asosiy as _norm


def _lcs_col_map(et_names, pt_names):
    """Etalon ustunlarini ishtirokchi ustunlariga NOM bo'yicha moslashtiradi.

    Ishtirokchi o'rtaga ustun qo'shsa (surilish) ham etalon ustunlari o'z
    nomi bo'yicha topiladi. Nomi bo'sh/o'zgartirilgan ustunlar uchun pozitsion
    yarashtirish qo'llanadi. Returns: {etalon_idx: ishtirokchi_idx}.
    """
    a = [_norm(x) for x in et_names]
    b = [_norm(x) for x in pt_names]
    n, m = len(a), len(b)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            if a[i] and a[i] == b[j]:
                dp[i][j] = dp[i + 1][j + 1] + 1
            else:
                dp[i][j] = dp[i + 1][j] if dp[i + 1][j] >= dp[i][j + 1] else dp[i][j + 1]
    mapping = {}
    i = j = 0
    while i < n and j < m:
        if a[i] and a[i] == b[j]:
            mapping[i] = j
            i += 1
            j += 1
        elif dp[i + 1][j] >= dp[i][j + 1]:
            i += 1
        else:
            j += 1
    # pozitsion yarashtirish: moslanmagan etalon ustuni ENG YAQIN moslangan
    # qo'shnisiga nisbatan siljish bilan topiladi — ishtirokchi o'rtaga ustun
    # qo'shib, ustiga nomini ham o'zgartirgan bo'lsa ham joyi to'g'ri chiqadi.
    used_pt = set(mapping.values())
    for c in range(n):
        if c in mapping:
            continue
        cand = None
        for cl in range(c - 1, -1, -1):        # chapdagi eng yaqin moslangan
            if cl in mapping:
                cand = mapping[cl] + (c - cl)
                break
        if cand is None:
            for cr in range(c + 1, n):         # o'ngdagi eng yaqin moslangan
                if cr in mapping:
                    cand = mapping[cr] - (cr - c)
                    break
        if cand is None:
            cand = c
        if 0 <= cand < m and cand not in used_pt:
            mapping[c] = cand
            used_pt.add(cand)
    return mapping


_VARAQ_STANDART = re.compile(r"^(лист|list|sheet|варақ|варак|varaq|сахифа|sahifa)\s*")
_VARAQ_NUSXA = re.compile(r"\s*\(\d+\)\s*$")


def _varaq_kaliti(nom):
    """Varaq nomini tasodifiy farqlardan tozalab, taqqoslash kalitiga aylantiradi.

    Ikki farq ishtirokchining aybi EMAS:
      - Excel tili: ruscha Excel varaqni «Лист1», inglizchasi «Sheet1» deb
        nomlaydi — fayl boshqa kompyuterda ochilsa nom o'zgarib qoladi.
      - Nusxa qo'shimchasi: varaqdan nusxa olinsa Excel « (2)» qo'shadi.
    """
    n = _norm(nom)
    n = _VARAQ_NUSXA.sub("", n)
    n = _VARAQ_STANDART.sub("sheet", n)
    return n.strip()


def _varaq_imzosi(rows, nechta=300):
    """Varaqning matn imzosi — nomi o'zgargan varaqni mazmunidan tanish uchun."""
    imzo = set()
    for r in rows[:80]:
        for c in r[:15]:
            s = _norm(c)
            if s and not s.replace(".", "").replace(",", "").replace(" ", "").isdigit():
                imzo.add(s[:40])
                if len(imzo) >= nechta:
                    return imzo
    return imzo


def _varaq_moslash(etalon_sheets, part_sheets, ostona=0.6):
    """Etalon varaqlarini ishtirokchi varaqlariga bog'laydi.

    Uch bosqich, har biri oldingisidan qolganini oladi:
      1) aynan bir xil nom
      2) tasodifiy nom farqi (Лист1 <-> Sheet1, «F5 (2)» -> «F5»)
      3) mazmun o'xshashligi — nom butunlay boshqacha bo'lsa ham
         («_ЛРВ» -> «ЛОКАЛКА»)

    Returns: {_norm(etalon nomi): ishtirokchidagi ASL nom}
    """
    moslik = {}
    band = set()

    pt_norm = {}
    for k in part_sheets:
        pt_norm.setdefault(_norm(k), k)
    for ek in etalon_sheets:
        p = pt_norm.get(_norm(ek))
        if p is not None and p not in band:
            moslik[_norm(ek)] = p
            band.add(p)

    pt_kalit = {}
    for k in part_sheets:
        if k not in band:
            pt_kalit.setdefault(_varaq_kaliti(k), k)
    for ek in etalon_sheets:
        if _norm(ek) in moslik:
            continue
        p = pt_kalit.get(_varaq_kaliti(ek))
        if p is not None and p not in band:
            moslik[_norm(ek)] = p
            band.add(p)

    qolgan = [k for k in part_sheets if k not in band]
    for ek in etalon_sheets:
        if _norm(ek) in moslik or not qolgan:
            continue
        imzo = _varaq_imzosi(etalon_sheets[ek])
        if not imzo:
            continue
        eng, engi = 0.0, None
        for pn in qolgan:
            u = len(imzo & _varaq_imzosi(part_sheets[pn])) / len(imzo)
            if u > eng:
                eng, engi = u, pn
        if engi is not None and eng >= ostona:
            moslik[_norm(ek)] = engi
            band.add(engi)
            qolgan.remove(engi)

    # 4) YAKKA-YAKKA majburiy juftlash (2026-08-05): etalonda ham, faylda ham
    #    bittadan juftlanmagan varaq qolgan bo'lsa — ular BIR-BIRINIKI, boshqa
    #    nomzod yo'q. Ishtirokchi varaqni «Оферта» kabi butunlay boshqacha
    #    nomlashi mumkin; «jadval o'chirilgan» deb rad etish o'rniga MAZMUNI
    #    tekshiriladi (noo'rin rad noo'rin qabuldan yomonroq). Mazmun boshqa
    #    hujjat bo'lsa, baribir keyingi tekshiruvlar (majburiy narx, Q3)
    #    ushlaydi.
    juftlanmagan_et = [ek for ek in etalon_sheets if _norm(ek) not in moslik]
    if len(juftlanmagan_et) == 1 and len(qolgan) == 1:
        moslik[_norm(juftlanmagan_et[0])] = qolgan[0]

    return moslik


# Ochiq nomlar (tashqi chaqiruvchilar uchun)
varaq_moslash = _varaq_moslash
lcs_col_map = _lcs_col_map
