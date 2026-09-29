# -*- coding: utf-8 -*-
"""Rol qoidalarining UMUMIY yordamchilari — sarlavha topish, ustun
statistikasi, «muqobil narx ustuni», Q3 («jiddiy to'ldirish») va h.k.

(Ilgari `set_validator.py` ning yuqori qismi — ko'chirilgan, o'zgartirilmagan.
Har rolning o'z qoidasi alohida modulda: jamlanma, narxlar, resurs, loyiha.)
"""

import math
import re

from tender_engine.normalize import (
    _kw,
    is_numeric_nonzero as _is_numeric_nonzero,
    normk_asosiy as _norm,
)
from tender_engine.roles import (
    FILL_THRESHOLD,
    RESURS_HEADER_ANCHORS,
    has_error_value,
    is_price_keyword,
)
from tender_engine.structure import _varaq_moslash


# O'lchov birligi ustuni — ikki tilda. Narxlanadigan qatorlarni ajratish
# uchun ishlatiladi (bo'lim sarlavhalarida birlik bo'lmaydi).
_BIRLIK_KEYWORDS = _kw([
    "ўлчов бирлиги", "улчов бирлиги", "единица измерения",
    "ed.изм", "ед.изм", "o'lchov birligi", "olchov birligi",
])

# Sarlavha qatorining birinchi ustuni shu bilan boshlansa — sarlavha topildi
_ANCHOR_PREFIX = _kw(["n п.п", "№п", "т/р", "t/r"])

# Raqamlash ustuni langar bermasa — USTUN nomlari bo'yicha tanish.
# Sabab: ishtirokchi «№ п/п» ni oddiy «№» deb qisqartirsa (juda keng
# tarqalgan), yuqoridagi langarlarning bironi ham ishlamaydi va butun
# jadval «sarlavhasi topilmadi» deb rad etilardi — narxlar to'la bo'lsa ham.
#
# DIQQAT: bu ro'yxatga hujjat SARLAVHASIDA uchraydigan so'zlar kirmaydi
# («ЛОКАЛЬНАЯ РЕСУРСНАЯ ВЕДОМОСТЬ» kabi), aks holda sarlavha qatori
# yuqoriga siljib ketadi.
_USTUN_ANCHORS = _kw([
    "наименование работ и затрат",
    "наименование работ и ресурсов",
    "наименование ресурса",
    "наименование затрат",
    "шифр номера нормативов",
    "единица измерения",
    "ед.изм",
])

# Ustun nomi bo'yicha tanishda qator ROSTDAN sarlavha ekaniga ishonch:
# smetalarda «(наименование работ и затрат)» kabi izohlar merge qilingan
# bo'ladi va butun qatorda AYNAN BIR XIL matn takrorlanadi. Haqiqiy
# sarlavhada esa turli ustun nomlari turadi.
_MIN_SARLAVHA_USTUN = 3

_BOSHLIQ_RE = re.compile(r"\s+")

# Narxlar jadvalida ishtirokchi narxni etalondagi ustunga emas, BOSHQA nomdagi
# narx ustuniga yozishi mumkin (amalda ko'p uchraydi: «на единицу измерения»
# bo'sh qoldirilib, «сумма» to'ldiriladi). Buyurtmachining asosiy talabi —
# narx kiritilganmi, ustun NOMI emas. Shuning uchun asosiy ustun bo'sh bo'lsa
# shu ro'yxatdagi nomlar bo'yicha ham qaraladi.
#
# DIQQAT: bu ro'yxatga narx BO'LMAGAN son ustunlari («объем», «количество»)
# kiritilmaydi — aks holda hajm kiritgan ishtirokchi narx bergan hisoblanadi.
_NARX_NOM_MUQOBIL = _kw([
    "сумма", "summa", "суммаси", "summasi",
    "қиймат", "киймат", "qiymat", "қиймати", "киймати", "qiymati",
    "нарх", "narx", "narxi", "цена", "baho", "баҳо",
    "стоимость", "стоимости",
])


def _narx_nomimi(nom):
    """Ustun nomi narx ma'nosini bildiradimi (nomi ixtiyoriy tilda)."""
    n = _norm(nom)
    return bool(n) and any(k in n for k in _NARX_NOM_MUQOBIL)


# HAJM/MIQDOR ustunlari — narx EMAS. Ishtirokchi ustunni xohlagancha nomlashi
# mumkin (Q2), LEKIN aniq «hajm» deb nomlangan ustundagi sonlar narx sanalmaydi:
# aks holda faqat ish hajmini ko'chirgan (narx bermagan) hujjat qabul bo'lardi.
_HAJM_KEYWORDS = _kw([
    "объем", "объём", "количество", "кол-во", "кол.во", "кол во",
    "hajm", "hajmi", "miqdor", "миқдор", "микдор",
])


def _hajm_nomimi(nom):
    n = _norm(nom)
    return bool(n) and any(k in n for k in _HAJM_KEYWORDS)


def _muqobil_narx_ustuni(pt_rows, data_start, col_names, band_idx, talab,
                         tashqari=(), et_rows=None, et_ds=0, et_band_idx=None):
    """Ishtirokchi narxni O'ZI tanlagan/qo'shgan ustunga kiritgan bo'lishi
    mumkin — nomi IXTIYORIY, hatto YORLIQSIZ ham (buyurtmachi qoidasi Q2:
    «ustunni o'zi xohlagandek nomlab to'ldirib ketishlari shart»).

    NOO'RIN RAD NOO'RIN QABULDAN YOMONROQ (sud xavfi, 2026-08-05 ko'rsatmasi)
    — shuning uchun bu zaxira keng: band qatorlarida jiddiy (>= talab) YANGI
    son to'plagan HAR QANDAY ustun narx deb olinadi. Ikkita istisno:
      - aniq HAJM/MIQDOR/BIRLIK deb nomlangan ustun (hajm narx emas)
      - etalonda ham xuddi shu joyda turgan sonlar (Q1 himoyasi: aynan
        nusxadagi «%», shifr kabi ustunlar YANGI hisoblanmaydi)

    Returns: (ustun_idx, yangi_toldirilgan_bandlar) yoki (None, 0).
    """
    def _sanash(rows, ds, b_idx, c):
        f = 0
        for row in rows[ds:]:
            if _is_numbering_row(row) or _jami_qatormi(row):
                continue
            if not _matnli(row[b_idx] if b_idx < len(row) else None):
                continue
            if _is_numeric_nonzero(row[c] if c < len(row) else None):
                f += 1
        return f

    # band ustuni ishtirokchi faylida BOSHQA joyda bo'lishi mumkin (masalan
    # jamlanma etaloni, lekin resurs-shakl fayl) — matnga eng boy ustunga
    # tushamiz, aks holda bandlar 0 chiqib zaxira ishlamay qolardi
    if band_idx is None or _sanash(pt_rows, data_start, band_idx, band_idx) == 0:
        bor = sum(1 for row in pt_rows[data_start:]
                  if band_idx is not None and band_idx < len(row)
                  and _matnli(row[band_idx]))
        if band_idx is None or bor == 0:
            band_idx = _eng_matnli_ustun(pt_rows, data_start)
    if band_idx is None:
        return None, 0

    kenglik = max(len(col_names), _distinct_width(pt_rows))
    eng, engi = 0, None
    for c in range(kenglik):
        if c in tashqari or c == band_idx:
            continue
        nm = col_names[c] if c < len(col_names) else ""
        if nm and (_hajm_nomimi(nm)
                   or any(k in nm for k in _BIRLIK_KEYWORDS)):
            continue
        f_pt = _sanash(pt_rows, data_start, band_idx, c)
        f_et = _sanash(et_rows, et_ds,
                       et_band_idx if et_band_idx is not None else band_idx,
                       c) if et_rows else 0
        f = max(0, f_pt - f_et)
        if f > eng:
            eng, engi = f, c
    if engi is not None and eng >= talab:
        return engi, eng
    return None, 0


def _muqobil_talab(pt_rows, data_start, band_idx):
    """Muqobil ustun uchun «jiddiylik» talabi — band soniga bog'liq."""
    def _band_soni(b_idx):
        n = 0
        for row in pt_rows[data_start:]:
            if _is_numbering_row(row) or _jami_qatormi(row):
                continue
            if _matnli(row[b_idx] if b_idx < len(row) else None):
                n += 1
        return n

    n = _band_soni(band_idx) if band_idx is not None else 0
    if n == 0:
        zaxira = _eng_matnli_ustun(pt_rows, data_start)
        if zaxira is not None:
            n = _band_soni(zaxira)
    n = max(n, 1)
    # MUTLAQ 3 lik chegara: muqobil zaxira faqat jiddiy to'ldirishni qabul
    # qiladi. Juda kichik (2-3 bandli) haqiqiy shakllar ASOSIY narx ustuni
    # orqali o'tadi — muqobilga tushmaydi.
    return max(3, min(30, math.ceil(FILL_THRESHOLD * n)))


def _korinish(matn, maks=60):
    """Ustun/jadval nomini izohda ko'rsatish uchun tayyorlaydi.

    Excel sarlavhalari ko'pincha satr uzilishi va ortiqcha bo'shliq bilan
    yoziladi («Стоимость\\n  в текущих ценах, сум») — izohda ular bitta
    probelga aylantiriladi, uzuni qisqartiriladi.
    """
    if matn is None:
        return ""
    s = _BOSHLIQ_RE.sub(" ", str(matn)).strip().strip(",;:").strip()
    if len(s) > maks:
        s = s[:maks].rstrip() + "…"
    return s


def _etalon_ustun_nomi(xom_hdr, norm_hdr, idx, zaxira):
    """Etalondagi ustunning izohda ko'rsatiladigan nomi.

    Avval XOM (asl registrdagi) matn olinadi — `_norm` harflarni kichraytirib
    yuboradi, ishtirokchiga esa ustun aynan hujjatdagidek ko'rinishi kerak.
    """
    if idx is None:
        return zaxira
    if xom_hdr is not None and idx < len(xom_hdr):
        nom = _korinish(xom_hdr[idx])
        if nom:
            return nom
    if norm_hdr and idx < len(norm_hdr) and norm_hdr[idx]:
        return _korinish(norm_hdr[idx])
    return zaxira


# ---------------------------------------------------------------------------
# Umumiy past-darajali yordamchilar
# ---------------------------------------------------------------------------

def _find_row_with(rows, keyword, limit=25):
    """Birinchi `limit` qatordan `keyword` (substring) bo'lgan qator indeksini
    qaytaradi, aks holda None."""
    return _find_row_with_any(rows, [keyword], limit)


def _find_row_with_any(rows, keywords, limit=25):
    """Kalit so'zlardan BIRORTASI uchragan birinchi qator indeksi.

    Bir xil shakl ikki tilda kelgani uchun kerak: `Харажатлар номи` va
    `Наименование расходов` — ikkalasi ham jamlanma sarlavhasi.
    """
    kws = _kw(keywords)      # xom kirill kalit so'z berilsa ham ishlasin
    for i, row in enumerate(rows[:limit]):
        for cell in row:
            n = _norm(cell)
            if n and any(k in n for k in kws):
                return i
    return None


def _first_col_matching(col_names, keywords):
    """Kalit so'zlar TARTIBI bo'yicha birinchi mos ustun indeksi.

    Tartib muhim: eng aniq nom (`жами қиймати`) umumiy nomdan
    (`стоимость`) oldin sinaladi, aks holda noto'g'ri ustun tanlanishi mumkin.
    """
    for kw in _kw(keywords):
        for c, name in enumerate(col_names):
            if kw in name:
                return c
    return None


def _norm_header_row(row):
    return [_norm(c) for c in row]


def _trim_trailing_empty(names):
    out = list(names)
    while out and not out[-1]:
        out.pop()
    return out


def _is_numbering_row(row):
    """Qator '1 2 3 4' yoki '1.0 2.0 ...' kabi ustun-raqamlash qatorimi?"""
    vals = [v for v in row if v is not None and str(v).strip() != ""]
    if not vals:
        return False
    for v in vals:
        try:
            f = float(str(v).replace(",", "."))
        except (ValueError, TypeError):
            return False
        if not (0 < f <= 40):
            return False
    return True


def _content_width(rows, scan=300):
    """Varaqdagi haqiqiy (bo'sh bo'lmagan) ustunlar sonini aniqlaydi."""
    w = 0
    for row in rows[:scan]:
        for idx in range(len(row) - 1, -1, -1):
            v = row[idx]
            if v is not None and str(v).strip() != "":
                if idx + 1 > w:
                    w = idx + 1
                break
    return w


def _col_stats(rows, data_start, col_idx, skip_numbering=False):
    """(filled, errors) — ustundagi bo'sh bo'lmagan raqamli kataklar soni va
    Excel xato matnlari (#VALUE! ...) soni.

    skip_numbering=True: '1 2 3 4 ...' ko'rinishidagi ustun-raqamlash qatorlari
    sanalmaydi — print-shakl smetalarda ular har sahifa boshida takrorlanadi va
    narx ustuniga «soxta raqam» bo'lib tushadi.
    """
    filled = 0
    errors = 0
    for row in rows[data_start:]:
        if skip_numbering and _is_numbering_row(row):
            continue
        v = row[col_idx] if col_idx < len(row) else None
        if has_error_value(v):
            errors += 1
        elif _is_numeric_nonzero(v):
            filled += 1
    return filled, errors


def _distinct_width(rows, scan=300):
    """Varaqning haqiqiy ustunlar soni — chap qo'shnisidan FARQ qiluvchi
    qiymatlar bo'yicha. Butun satr bo'ylab merge-kengaytirilgan sarlavha/futer
    matnlari («ИТОГО ...») kenglikni sun'iy oshirmaydi."""
    w = 0
    for row in rows[:scan]:
        prev = None
        for idx, v in enumerate(row):
            if v is not None and str(v).strip() != "":
                if prev is None or str(v).strip() != str(prev).strip():
                    if idx + 1 > w:
                        w = idx + 1
                prev = v
            else:
                prev = None
    return w


def _col_nonempty(rows, data_start, col_idx):
    """Ustundagi HAQIQIY mazmunga ega kataklar soni.

    Sanalmaydi:
      • bo'sh kataklar
      • Excel xato qiymatlari (#VALUE! va b.) — ular mazmun emas, buzuq formula
      • chap qo'shnisi bilan bir xil qiymat: read_file birlashtirilgan (merge)
        kataklarni butun satr bo'ylab nusxalaydi («ИТОГО ...» kabi footerlar)
    """
    cnt = 0
    for row in rows[data_start:]:
        v = row[col_idx] if col_idx < len(row) else None
        if v is None or str(v).strip() == "":
            continue
        if has_error_value(v):
            continue
        left = row[col_idx - 1] if col_idx > 0 else None
        if left is not None and str(left).strip() == str(v).strip():
            continue  # merge-kengaytirilgan takror
        cnt += 1
    return cnt


def _jadval_tegilmagan(et_rows, pt_rows, data_start, width):
    """Ishtirokchi bu jadvalga HECH NARSA qo'shmaganmi?

    Ma'lumot sohasidagi (data_start dan keyingi) barcha kataklar etalon bilan
    aynan bir xil bo'lsa True — ya'ni shablon qanday berilgan bo'lsa, shundayligicha
    qaytarilgan. Bunday holatda ayrim ustunlarni sanab o'tirish o'rniga
    «jadval umuman to'ldirilmagan» deyish aniqroq.
    """
    for i in range(data_start, max(len(et_rows), len(pt_rows))):
        er = et_rows[i] if i < len(et_rows) else []
        pr = pt_rows[i] if i < len(pt_rows) else []
        for c in range(width):
            ev = er[c] if c < len(er) else None
            pv = pr[c] if c < len(pr) else None
            es = "" if ev is None else str(ev).strip()
            ps = "" if pv is None else str(pv).strip()
            if es != ps:
                return False
    return True


# ---------------------------------------------------------------------------
# Q3 — «jiddiy to'ldirish» (2026-08-04, buyurtmachi talabi)
#
#   «Qo'lda to'ldirish deganda shunchaki bir-ikkita qiymat yozib qo'yish,
#    yoki umumiy narxni yozib qo'yish holatlari kuzatilsa rad etilishi lozim.»
#
# Har jadval uchun:
#   N — etalonda to'ldirilishi KUTILGAN bandlar (tavsifi bor, narxi etalonda
#       BO'SH qatorlar; JAMI/ИТОГО futerlari va raqamlash qatorlari sanalmaydi)
#   F — ishtirokchi narxlagan bandlar (kamida bitta narx ustunida haqiqiy son)
#   T — ishtirokchi FAQAT jami qatorlariga kiritgan qo'shimcha sonlar
#
#   RAD (PRICE_ONLY_TOTAL):  F == 0  va  T > 0
#   RAD (PRICE_SPARSE):      F < min(N, max(3, ceil(nisbat × N)))
#
# MUHIM: maxraj (N) ETALONDAN olinadi — ishtirokchi qatorlarni o'chirib
# «hammasini to'ldirdim» deb chetlab o'tolmasin. Q3 QO'SHIMCHA (AND) shart:
# mavjud tekshiruvlar topgan kamchiliklarni almashtirmaydi, faqat ular hech
# narsa topmaganda ishlaydi.
# ---------------------------------------------------------------------------

# (nisbat, minimal band soni) — rolga qarab. `resurs` da past: smetada narx
# bir necha ustunga taqsimlanadi va faqat quyi resurs qatorlarida turadi,
# to'liq to'ldirilgan hujjatning nisbati ham 0.2 atrofida bo'lishi mumkin.
_Q3_SOZLAMA = {
    "jamlanma": (0.30, 1),
    "narxlar": (0.30, 1),
    "loyiha": (0.30, 1),
    "resurs": (0.05, 8),
}

_JAMI_KEYWORDS = _kw(["итого", "всего", "жами", "jami", "umumiy", "hammasi"])


def _matnli(cell):
    """Katakda HAQIQIY matn bormi (raqam/belgigina emas)?"""
    n = _norm(cell)
    if not n:
        return False
    return sum(1 for ch in n if ch.isalpha()) >= 2


def _jami_qatormi(row, limit=12):
    """Qator JAMI/ИТОГО/ВСЕГО (yakun) qatorimi?

    Kalit so'z katak BOSHIDA tursa, yoki qisqa (≤40 belgi) katakda uchrasa —
    yakun qatori. Uzun ish tavsifi ichida uchragan «жами» hisobga olinmaydi.
    """
    for c in row[:limit]:
        n = _norm(c)
        if not n:
            continue
        for kw in _JAMI_KEYWORDS:
            if n.startswith(kw) or (kw in n and len(n) <= 40):
                return True
    return False


def _eng_matnli_ustun(rows, data_start, width=30):
    """Ma'lumot sohasida eng ko'p HAQIQIY matnga ega ustun (tavsif ustuni)."""
    hisob = {}
    for row in rows[data_start:]:
        for c in range(min(len(row), width)):
            if _matnli(row[c]):
                hisob[c] = hisob.get(c, 0) + 1
    if not hisob:
        return None
    return max(hisob, key=lambda c: hisob[c])


def _q3_tekshir(rol, sheet, narx_nomi,
                et_rows, et_ds, et_name_idx, et_price_cols,
                pt_rows, pt_ds, pt_name_idx, pt_price_cols):
    """Q3 tekshiruvi: kamchilik topilsa finding dict, aks holda None.

    Chaqiruvchi buni FAQAT boshqa kamchilik topilmaganda chaqiradi.
    """
    if not et_rows or not pt_rows or not pt_price_cols:
        return None
    nisbat, min_band = _Q3_SOZLAMA.get(rol, (0.30, 1))
    if et_name_idx is None:
        et_name_idx = _eng_matnli_ustun(et_rows, et_ds)
    if pt_name_idx is None:
        pt_name_idx = _eng_matnli_ustun(pt_rows, pt_ds)
    elif not any(_matnli(row[pt_name_idx] if pt_name_idx < len(row) else None)
                 for row in pt_rows[pt_ds:]):
        # berilgan ustun bu faylda matnsiz (boshqa shakl) — matnga boyiga tushamiz
        pt_name_idx = _eng_matnli_ustun(pt_rows, pt_ds)
    if et_name_idx is None or pt_name_idx is None:
        return None

    def _sarlavha_qoldigi(row, cols):
        # narx katagida narx KALIT SO'ZI turgan qator — ikkinchi sarlavha
        for c in cols:
            t = _norm(row[c] if c < len(row) else None)
            if t and is_price_keyword(t, rol):
                return True
        return False

    def _hisobla(rows, ds, name_idx, cols):
        band = priced = jami = 0
        for row in rows[ds:]:
            if _is_numbering_row(row) or _sarlavha_qoldigi(row, cols):
                continue
            son_bor = any(_is_numeric_nonzero(row[c] if c < len(row) else None)
                          for c in cols)
            if _jami_qatormi(row):
                if son_bor:
                    jami += 1
                continue
            v = row[name_idx] if name_idx < len(row) else None
            if not _matnli(v):
                continue
            band += 1
            if son_bor:
                priced += 1
        return band, priced, jami

    et_band, et_priced, et_jami = _hisobla(et_rows, et_ds, et_name_idx,
                                           et_price_cols or [])
    N = et_band - et_priced
    pt_band, pt_priced, pt_jami = _hisobla(pt_rows, pt_ds, pt_name_idx,
                                           pt_price_cols)
    F = max(0, pt_priced - et_priced)
    T = max(0, pt_jami - et_jami)

    if N < min_band:
        # Etalonda band yo'q (SKELET shablon — ishtirokchi qatorlarni o'zi
        # qo'shadi) yoki juda kam. Nisbiy talab qo'yib bo'lmaydi, LEKIN
        # «faqat jami» teshigi baribir yopiq turishi shart: ishtirokchi
        # bironta ham band narxlamasdan yakun qatoriga son yozib qo'ygan
        # bo'lsa — RAD.
        if pt_priced == 0 and T > 0:
            joy0 = f"«{sheet}» jadvalida" if sheet else "Jadvalda"
            return {"code": "PRICE_ONLY_TOTAL", "sheet": sheet,
                    "detail_uz": f"{joy0} faqat umumiy (jami) qiymat "
                                 f"kiritilgan — ish/xarajat bandlariga "
                                 f"alohida narx kiritilmagan."}
        return None
    # Nisbiy talab 30 ta qiymatda TO'XTAYDI: 17 000 qatorli smetada ham
    # 30 ta haqiqiy narx «jiddiy to'ldirish» hisoblanadi — Q3 ning maqsadi
    # bir-ikkita qiymat va faqat-jami holatlarini tutish, foizni emas.
    # (2026-08-05) Ishtirokchi hujjatni O'Z shaklida yuborgan bo'lsa (band
    # soni etalondan kam), NISBIY talab uning bandlariga nisbatan olinadi —
    # noo'rin rad noo'rin qabuldan yomonroq. LEKIN 3 lik MUTLAQ chegara
    # etalon N >= 3 bo'lganda saqlanadi: ishtirokchi qatorlarni 2 taga
    # qisqartirib 2 ta qiymat bilan o'tib ketolmasin («bir-ikkita yetarli
    # emas» — Q3).
    N_eff = min(N, pt_band) if pt_band > 0 else N
    qavat = 3 if N >= 3 else N
    talab = min(N, max(qavat, min(30, math.ceil(nisbat * N_eff))))
    if F >= talab:
        return None

    joy = f"«{sheet}» jadvalida" if sheet else "Jadvalda"
    if F == 0 and T > 0:
        return {"code": "PRICE_ONLY_TOTAL", "sheet": sheet,
                "detail_uz": f"{joy} faqat umumiy (jami) qiymat kiritilgan — "
                             f"{N} ta ish/xarajat bandiga alohida narx "
                             f"kiritilmagan."}
    return {"code": "PRICE_SPARSE", "sheet": sheet,
            "detail_uz": f"{joy} {N} ta banddan atigi {F} tasiga qiymat "
                         f"kiritilgan — hujjat jiddiy to'ldirilmagan."}


def _q3_data_boshi(rows, hdr):
    """Etalon uchun ma'lumot boshlanishi: sarlavhadan keyin, raqamlash
    qatorlarini o'tkazib."""
    ds = (hdr + 1) if hdr is not None else 0
    while ds < len(rows) and _is_numbering_row(rows[ds]):
        ds += 1
    return ds


def _defekt_bormi(findings):
    return any(f.get("severity") not in ("note",) for f in findings)


def _mos_ustunlar(col_names, keywords):
    """Kalit so'zlarga mos keluvchi BARCHA ustun indekslari."""
    kws = _kw(keywords)
    return [c for c, name in enumerate(col_names)
            if name and any(kw in name for kw in kws)]


def _eng_toldirilgan_ustun(nomzodlar, rows, data_start):
    """Nomzod ustunlar ichidan haqiqiy raqamlarga ENG BOYI (teng: o'ngdagisi).

    Bir xil nomli ustun IKKITA bo'lishi mumkin: «Qiymat (ming so'm)» ning
    birinchisi matnli izoh ustuni, haqiqiy narx keyingisida. Birinchisini
    olish to'la to'ldirilgan hujjatni «bo'sh» deb rad etardi.
    """
    if not nomzodlar:
        return None
    if len(nomzodlar) == 1:
        return nomzodlar[0]
    eng, engi = -1, nomzodlar[0]
    for c in nomzodlar:
        f, _ = _col_stats(rows, data_start, c, skip_numbering=True)
        if f >= eng:
            eng, engi = f, c
    return engi


# ---------------------------------------------------------------------------
# Resurs (ko'p varaqli smeta) sarlavhasini tahlil qilish
# ---------------------------------------------------------------------------

def _parse_resource_sheet(rows):
    """Resurs varag'ining sarlavha tuzilmasini qaytaradi yoki None.

    {header_top, num_row, data_start, col_names, ncols}
    """
    header_top = None
    for i, row in enumerate(rows[:25]):
        # merge qilingan izoh qatorlarini sarlavha deb olmaslik uchun:
        # qatorda nechta HAR XIL matn borligini oldindan sanaymiz
        xilma = len({_norm(c) for c in row if _norm(c)})
        for cell in row:
            n = _norm(cell)
            if not n:
                continue
            if n in RESURS_HEADER_ANCHORS or any(n.startswith(p) for p in _ANCHOR_PREFIX):
                header_top = i
                break
            if xilma >= _MIN_SARLAVHA_USTUN and any(k in n for k in _USTUN_ANCHORS):
                header_top = i
                break
        if header_top is not None:
            break
    if header_top is None:
        return None

    # raqamlash qatorini topish (keyingi 4 qator ichida)
    num_row = None
    for j in range(header_top + 1, min(header_top + 5, len(rows))):
        if _is_numbering_row(rows[j]):
            num_row = j
            break

    hdr_end = num_row if num_row is not None else header_top + 3
    data_start = (num_row + 1) if num_row is not None else header_top + 3

    header_rows = rows[header_top:hdr_end]
    ncols = max((len(r) for r in header_rows), default=0)

    # `col_names` — solishtirish uchun (kichik harf, normallashtirilgan).
    # `col_names_xom` — IZOHDA ko'rsatish uchun asl registrdagi matn.
    #
    # Eski `.xls` sarlavhalari aralash klaviaturada yozilgan bo'ladi
    # («CTOИMOCTЬ HA EДИHИЦY ИЗMEPEHИЯ» — C, T, O, H, E, Y lotin). Bosh
    # harflarda odam buni «СТОИМОСТЬ НА ЕДИНИЦУ ИЗМЕРЕНИЯ» deb o'qiydi,
    # `_norm` kichraytirgach esa «ctoиmoctь ha eдиhицy изmepehия» bo'lib
    # buzuq matnga aylanadi — ishtirokchiga aynan shu ko'rinardi.
    col_names = []
    col_names_xom = []
    for c in range(ncols):
        parts, xom = [], []
        for r in header_rows:
            if c < len(r):
                n = _norm(r[c])
                if n and n not in parts:
                    parts.append(n)
                    asl = _korinish(r[c], 200)
                    if asl:
                        xom.append(asl)
        col_names.append(" ".join(parts))
        col_names_xom.append(" ".join(xom))

    return {
        "header_top": header_top,
        "num_row": num_row,
        "data_start": data_start,
        "col_names": col_names,
        "col_names_xom": col_names_xom,
        "ncols": ncols,
    }


def _price_col_indices(col_names):
    return [c for c, name in enumerate(col_names) if is_price_keyword(name, "resurs")]


# ---------------------------------------------------------------------------
# Jamlanma va narxlar uchun tekshiriladigan varaqni tanlash
# ---------------------------------------------------------------------------

def _tekshiriladigan_varaq(etalon_sheets, part_sheets, kalitlar):
    """#10 (2026-09-08): jamlanma/narxlar uchun TEKSHIRILADIGAN varaq.

    Ilgari doim BIRINCHI varaq olinardi — ishtirokchi oldiga muqova yoki
    «ЛОКАЛЬНЫЙ» varag'ini qo'ysa, haqiqiy jadval ko'rilmay «sarlavha
    topilmadi» bilan rad etilardi (korpus: 17633, 29149). Tanlov tartibi
    (`validate_loyiha` dagi `loyiha_varagi` naqshi):
      1) etalonning birinchi varag'iga `_varaq_moslash` bilan juftlangan
         varaq (nom / nom-kaliti / mazmun imzosi / yakka-yakka);
      2) bo'lmasa — sarlavha kalitlari topilgan BIRINCHI varaq;
      3) bo'lmasa — birinchi varaq (eski xulq, aynan).
    Bitta varaqli faylda hech narsa o'zgarmaydi. Korpusda o'lchangan
    (`korpus/regressiya/xom/q10_olchov.log`): 2 095 jamlanma/narxlar
    qatorida tanlov 71 faylda o'zgaradi, verdikt 3 tasida — B zonasi 0.
    """
    nomlar = list(part_sheets.keys())
    if len(nomlar) <= 1:
        return nomlar[0]
    if etalon_sheets:
        mos = _varaq_moslash(etalon_sheets, part_sheets)
        p = mos.get(_norm(next(iter(etalon_sheets))))
        if p is not None and p in part_sheets:
            return p
    for nom in nomlar:
        if _find_row_with_any(part_sheets[nom], kalitlar) is not None:
            return nom
    # DIQQAT (#10-b, 2026-09-08 — RAD ETILGAN variant): «juftlangan varaq
    # sarlavhasiz bo'lsa sarlavhali/birinchi varaqni ol» tartibi sinaldi.
    # 20k sinovidagi 142790 (etalon nomli «Лист1» bo'sh, «Sheet0» 23 030 qatorli
    # TOVAR KATALOGI) eski kodda pozitsion zaxira bilan NOO'RIN qabul bo'lgan
    # edi; #10-b uni yana qabul qilar va A dagi tasdiqlangan noo'rin qabul
    # 73823 (7 varaq, birortasida sarlavha yo'q, etalon nomli varaq bo'sh) ni
    # ham qaytarardi (A 338→337). Juftlangan varaq bo'sh bo'lsa — bu
    # ishtirokchining o'zi shablon varag'ini bo'sh qoldirgani; pozitsion
    # zaxira bilan boshqa varaqni «shakl» deb o'qish xato.
    return nomlar[0]
