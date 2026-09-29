# -*- coding: utf-8 -*-
"""Audit izi — har verdiktning «nega»si `validation_evidence` ga yoziladi.

Ikki yozuv: eski dvigatel natijasi (engine-0.1) va yakuniy hukm
(engine-0.2-shadow); texnik holatlar ham TECHNICAL_ERROR bilan iz qoldiradi.
Jadval append-only (trigger) — yozuvni o'zgartirib ham, o'chirib ham bo'lmaydi.

(Ilgari `jobs_worker.py` da — ko'chirilgan.)
"""

import os

from app import download as _w
from app.log import log
from tender_engine import decision as _decision
from tender_engine import evidence as _evidence


def _etalon_kop_note(et, ish):
    """F2: bitta tender+type uchun BIR NECHTA shablon — audit belgisi.

    Tanlov o'zgarmaydi (`templates_db.qatorni_top`: eng yangi id). Belgi
    `severity=note` — statusga ham, ishtirokchi izohiga ham ta'sir qilmaydi
    (`validate_one`/`_render_comment`/`decision.hukm` note'larni chetlaydi);
    evidence `notelar` va `jobs_validation_log.findings` ga tushadi — qaysi
    shablon nega olingani keyin bitta SQL bilan ko'rinadi.
    """
    try:
        n = int(et.get("nomzodlar") or 1)
    except (TypeError, ValueError):
        return None
    if n <= 1:
        return None
    boshqa = ", ".join(str(x) for x in (et.get("boshqa_idlar") or [])[:4])
    log(f"  [etalon] tender={ish.get('tender_id')} {ish.get('type')}: {n} ta shablon — "
        f"templates.id={et.get('id')} (eng yangisi) tanlandi; boshqalari: {boshqa}",
        "warning")
    return {"code": "ETALON_KOP", "sheet": None, "severity": "note",
            "detail_uz": (f"Buyurtmachi shu tender uchun {n} ta shablon yuklagan — "
                          f"eng yangisi (templates.id={et.get('id')}) bilan "
                          f"solishtirildi; boshqalari: {boshqa}.")}


def _siyosat_versiya():
    """Joriy siyosat versiyasi — evidence'ga yoziladi (qaysi chegara ishlagan)."""
    return getattr(_decision, "SIYOSAT_VERSIYA", None)


def _soya_evidence(conn, ish, res_eski, yuklangan, etalon_yoli, tfid,
                   et_varaqlar=None, pt_varaqlar=None, res_final=None,
                   majburiy=False):
    """Audit: verdikt dalilini validation_evidence ga yozadi.

    Ikki yozuv: (1) eski dvigatelning HAQIQIY natijasi (engine-0.1) —
    `res_eski` shakl-erkin/flip almashtirishlaridan OLDIN muzlatilgan
    bo'lishi SHART; (2) yakuniy hukm (engine-0.2-shadow) — `res_final`
    da `_ichki` bo'lsa (shakl-erkin yoki flip yo'li) o'shandan, aks holda
    `decision.hukm` qayta hisobidan. REVIEW_AMBIGUOUS avtomatik
    `sinf='review'` oladi (evidence.yozuv_tayyorla).

    Qaytaradi: True — yozildi (yoki yozadigan narsa yo'q), False — xato.
    `majburiy=False` da xato yutiladi (asosiy oqimga ta'sir yo'q);
    `majburiy=True` da chaqiruvchi False ni ko'rib QAROR qiladi —
    REVIEW→1 kafolati shunga tayanadi.
    """
    if res_final is None:
        res_final = res_eski
    try:
        kodlar = [f["code"] for f in res_eski.get("findings", [])
                  if f.get("severity") not in ("note", "technical")]
        umumiy = dict(
            fayl_id=ish["uuid"], link=str(ish["link"]),
            tender_id=ish.get("tender_id"), typ=ish.get("type"),
            fayl_hash=_evidence.fayl_sha256(yuklangan),
            etalon_tpl_id=int(tfid) if tfid and str(tfid).isdigit() else None,
            etalon_hash=(_evidence.fayl_sha256(etalon_yoli)
                         if etalon_yoli else None),
            rol=res_final.get("role") or res_eski.get("role"))
        yozuv = _evidence.yozuv_tayyorla(
            ichki_status=_evidence.eski_natijadan_ichki(
                res_eski["status"], kodlar),
            comment_uz=res_eski.get("comment_uz", ""),
            findings=res_eski.get("findings"), **umumiy)
        _evidence.yoz(conn, yozuv)

        if res_final.get("_ichki"):
            # Yakuniy hukm shakl-erkin/flip yo'lidan chiqqan — QAYTA
            # HISOBLAMAYMIZ, o'sha yo'lning o'z ichki holati va o'lchovlari
            # yoziladi (confidence surrogati: band/narxli_band).
            soya = _evidence.yozuv_tayyorla(
                ichki_status=res_final["_ichki"],
                comment_uz=(res_final.get("_review_sabab")
                            or res_final.get("comment_uz", "")),
                findings=res_final.get("findings"),
                varaq_dalil={
                    "yangi_raqamlar": res_final.get("_yangi_raqamlar"),
                    "band": res_final.get("_band"),
                    "narxli_band": res_final.get("_narxli_band"),
                    "siyosat": _siyosat_versiya(),
                }, **umumiy)
            soya["validator_version"] = "engine-0.2-shadow"
            _evidence.yoz(conn, soya)
        elif et_varaqlar and pt_varaqlar:
            h = _decision.hukm(res_eski.get("role"), et_varaqlar, pt_varaqlar,
                               _w._link_filename(ish["link"]),
                               eski_res=res_eski)
            soya = _evidence.yozuv_tayyorla(
                ichki_status=h["ichki"],
                comment_uz=h["review_sabab"] or res_eski.get("comment_uz", ""),
                findings=res_eski.get("findings"),
                varaq_dalil={
                    "yangi_raqamlar": h.get("yangi_raqamlar"),
                    "band": h.get("band"),
                    "narxli_band": h.get("narxli_band"),
                    "siyosat": _siyosat_versiya(),
                }, **umumiy)
            soya["validator_version"] = "engine-0.2-shadow"
            _evidence.yoz(conn, soya)
        return True
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        log(f"  [evidence] yozilmadi ({type(exc).__name__}: "
            f"{str(exc)[:80]})" + ("" if majburiy else " — verdiktga ta'sir yo'q"),
            "error" if majburiy else "warning")
        return False


def _texnik_iz(conn, ish, sabab, yuklangan=None, etalon_yoli=None, rol=None):
    """TEXNIK holat (status 0) uchun ham audit-iz: TECHNICAL_ERROR yozuvi.

    Ilgari texnik yo'llar evidence'ga umuman yetmasdi — «nega javob yo'q»
    savoliga faqat jobs_state.last_error (bitta, ustidan yoziladigan maydon)
    javob berardi. Xatosi yutiladi — asosiy oqimga ta'sir qilmaydi.
    Fayl yuklanmagan bo'lsa fayl_hash bo'sh satr (sxemada NOT NULL).
    """
    try:
        fh = ""
        if yuklangan and os.path.isfile(yuklangan):
            fh = _evidence.fayl_sha256(yuklangan)
        eh = None
        if etalon_yoli and os.path.isfile(etalon_yoli):
            eh = _evidence.fayl_sha256(etalon_yoli)
        yozuv = _evidence.yozuv_tayyorla(
            ichki_status=_evidence.TECHNICAL_ERROR,
            comment_uz=str(sabab)[:500],
            fayl_id=ish["uuid"], link=str(ish["link"]),
            tender_id=ish.get("tender_id"), typ=ish.get("type"),
            fayl_hash=fh, etalon_tpl_id=None, etalon_hash=eh,
            rol=rol, findings=[])
        _evidence.yoz(conn, yozuv)
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        log(f"  [evidence/texnik] yozilmadi ({type(exc).__name__})", "warning")
