# -*- coding: utf-8 -*-
"""Bitta faylning butun yo'li — `ishni_bajar`.

Qadamlar TARTIBI tekshiruv mantig'ining o'zi (har qadam izohida sababi bor):

    1)  yuklab olish                                   `_yukla`
    2)  etalon: `templates` dan; yo'q bo'lsa kutish (F1), noma'lum tur (F3)
        yoki texnik holat                              `_etalonni_top`
    Q1) bayt-ba-bayt aynan nusxa → status 3           (hamma qoidadan OLDIN)
    3)  o'qish: ochilmadi → QABUL (#2); mazmun-nusxa (S1b) → 2;
        `validate_one`; bo'sh hujjat (#7) → 2
    3a) shakl-erkin hukm — etalon notanish yoki matn tiklanmagan
    3b) «narx bor — qabul» — faqat struktura bo'yicha rad bo'lsa
    3c) REVIEW→1 faqat audit izi OLDIN yozilgach
    4)  verdikt + chiquvchi xabar — bitta tranzaksiyada

OLTIN QOIDA: texnik xato (tarmoq, etalon, o'qish) HECH QACHON status 2 bermaydi.

(Ilgari `jobs_worker.py` da. 1–2 qadamlar alohida funksiyaga ajratildi,
qolgani ko'chirilgan; diskdagi etalon zaxirasi olib tashlandi — etalon faqat
`templates` jadvalidan olinadi.)
"""

import os

from app import download as _w
from app import templates as templates_db
from app.download import DOWNLOAD_DIR, PermanentDownloadError, download
from app.log import log
from app.worker.audit import _etalon_kop_note, _soya_evidence, _texnik_iz
from app.worker.gates import (
    STATUS_IDENTICAL,
    _aynan_nusxa_res,
    _bosh_hujjat_res,
    _mazmun_nusxa_res,
    _ochilmadi_res,
)
from app.worker.queue import KUTISH_BELGI, shablon_kutilsin, texnik_qoyib_yubor
from app.worker.verdict import natijani_yoz
from tender_engine import decision as _decision
from tender_engine import evidence as _evidence
from tender_engine.reader import ExcelTooLargeError, read_file
from tender_engine.validate import (
    STATUS_DEFECT,
    STATUS_FILLED,
    _render_comment,
    load_etalon_sheets,
    validate_one,
)


def _yukla(conn, ish, token, ish_papka):
    """1) Ishtirokchi faylini yuklab oladi.

    Qaytadi: lokal yo'l, yoki None — xato texnik holat sifatida allaqachon
    yozilgan (chaqiruvchi to'xtaydi).
    """
    uid = ish["uuid"]
    try:
        return download(ish["link"], ish_papka)
    except PermanentDownloadError as exc:
        log(f"  [doimiy xato] {str(ish['link'])[:70]}: {exc}")
        _texnik_iz(conn, ish, str(exc))
        texnik_qoyib_yubor(conn, uid, str(exc), doimiy=True, token=token)
        return None
    except Exception as exc:
        # httpx.ReadTimeout kabi xatolarda str(exc) BO'SH bo'ladi —
        # iz/last_error da hech bo'lmasa xato TURI ko'rinsin
        # (2026-08-27 yurishida ~240 ta izsiz-matnli yozuv shundan edi).
        xato = str(exc).strip() or type(exc).__name__
        log(f"  [texnik xato] {str(ish['link'])[:70]}: {xato}")
        _texnik_iz(conn, ish, xato)
        texnik_qoyib_yubor(conn, uid, xato, token=token)
        return None


def _etalonni_top(conn, ish, token, yuklangan):
    """2) Etalon — `templates` jadvalidan, tender_id + type bo'yicha.

    Qaytadi: (rol, etalon_yoli, tfid, etalon_note), yoki None — fayl kutish
    yoki texnik holatga o'tkazilgan (chaqiruvchi to'xtaydi).

    Diskdagi etalon/ papkasi (fayl nomidagi ID orqali) ENDI YO'Q: u fayl
    nomi bo'yicha TASODIFIY mos kelib, butunlay boshqa tenderning shabloni
    bilan solishtirib yolg'on verdikt berishi mumkin edi (va `tender_id`
    bo'lgan qatorlar uchun allaqachon yopiq edi).
    """
    uid = ish["uuid"]
    rol = etalon_yoli = tfid = None
    shablon_yoq = False
    etalon_note = None            # F2: bir nechta shablon bo'lsa audit belgisi
    try:
        et = templates_db.etalon_ol(conn, ish["tender_id"], ish.get("type"))
        rol, etalon_yoli, tfid = et["role"], et["path"], str(et["file_id"])
        etalon_note = _etalon_kop_note(et, ish)
        # Etalon muvaffaqiyatli yuklab o'qildi → status 0 dan 1 ga
        if templates_db.tekshirilgan_deb_belgila(conn, et["id"]):
            conn.commit()
    except templates_db.EtalonYoq as exc:
        # F1: qator umuman yo'q — shablon hali kelmagan (kutish holati),
        # `EtalonOqilmadi` (bor, lekin o'qilmadi) dan FARQLANADI.
        shablon_yoq = True
        log(f"  [templates] {exc}", "warning")
    except templates_db.EtalonOqilmadi as exc:
        log(f"  [templates] {exc}", "warning")

    if not etalon_yoli or rol is None:
        if shablon_yoq:
            tur = str(ish.get("type") or "").strip().lower()
            if tur not in templates_db.TYPE_ROL:
                # F3 (2026-09-10): `type` platformada TANILMAYDI va shu
                # turda shablon ham yo'q — DETERMINIK holat (kutish
                # foydasiz: shablon shu tur bilan kelmaydi). Darhol o'lik
                # ro'yxatiga, sabab aniq; `jobs.status` 0 qoladi (OLTIN
                # QOIDA). Shu turda shablon BOR bo'lsa bu yerga kelmaydi —
                # aynan tenglik bo'yicha topilib, rol mazmundan aniqlanadi.
                sabab = (f"TYPE_NOMALUM: type={ish.get('type')!r} platformada "
                         f"tanilmaydi ({', '.join(sorted(templates_db.TYPE_ROL))}) "
                         f"va tender_id={ish['tender_id']} uchun shu turda shablon yo'q")
                log(f"  [texnik/o_lik] {sabab}")
                _texnik_iz(conn, ish, sabab, yuklangan)
                texnik_qoyib_yubor(conn, uid, sabab, doimiy=True, token=token)
                return None
            # F1 (2026-09-08): shablon HALI YO'Q — bu xato emas, KUTISH.
            # Fayl o'lmaydi, urinish sarflanmaydi; shablon `templates` ga
            # tushishi bilan `shablon_kelganini_tekshir` uyg'otadi.
            sabab = (f"«{templates_db.TEMPLATES_TABLE}» da tender_id="
                     f"{ish['tender_id']} type={ish.get('type')} uchun "
                     f"shablon hali yo'q")
            _texnik_iz(conn, ish, f"{KUTISH_BELGI}: {sabab}", yuklangan)
            shablon_kutilsin(conn, uid, sabab, token=token)
            return None
        sabab = (f"«{templates_db.TEMPLATES_TABLE}» da tender_id="
                 f"{ish['tender_id']} type={ish.get('type')} uchun "
                 f"yaroqli etalon yo'q")
        log(f"  [texnik] {sabab}")
        _texnik_iz(conn, ish, sabab, yuklangan)
        texnik_qoyib_yubor(conn, uid, sabab, token=token)
        return None
    return rol, etalon_yoli, tfid, etalon_note


def ishni_bajar(conn, ish, token):
    """Bitta faylni tekshiradi (qadamlar — modul izohida).

    Qaytadi: verdikt yozilsa {"id", "file_id", "status", "comment"}, aks
    holda None (texnik holat, kutish, claim boshqada).
    """
    uid = ish["uuid"]
    papka_kalit = str(ish.get("bidder_id") or ish["uuid"])
    ish_papka = os.path.join(DOWNLOAD_DIR, papka_kalit, token[:12])
    yuklangan = None
    try:
        # 1) yuklab olish
        yuklangan = _yukla(conn, ish, token, ish_papka)
        if yuklangan is None:
            return

        fayl_nomi = _w._link_filename(ish["link"])

        # 2) etalonni topish — `templates` jadvali, tender_id + type bo'yicha
        etalon = _etalonni_top(conn, ish, token, yuklangan)
        if etalon is None:
            return
        rol, etalon_yoli, tfid, etalon_note = etalon

        # ── Q1 DARVOZASI: bayt-ba-bayt AYNAN NUSXA (2026-08-20) ──────────
        # HAMMA qoidadan oldin. Ataylab `load_sheets`/`validate_one` dan
        # OLDIN turadi: etalon shakli bizga notanish bo'lsa ham
        # (ETALON_UNPARSED — ilgari bunday nusxalar verdiktsiz muallaq
        # qolardi) bayt-tenglik fakti o'zgarmaydi va verdikt chiqadi.
        res = _aynan_nusxa_res(fayl_nomi, rol, yuklangan, etalon_yoli)
        if res is not None:
            res["_template_file_id"] = tfid
            if etalon_note:
                res["findings"].append(dict(etalon_note))      # F2 audit belgisi
            if not natijani_yoz(conn, ish, token, res):
                log(f"  [o'tkazildi] {fayl_nomi[:45]} — claim boshqada "
                    f"yoki send=true")
                return
            _soya_evidence(conn, ish, res, yuklangan, etalon_yoli, tfid)
            log(f"  ≡ [nusxa] {fayl_nomi[:45]} → status={STATUS_IDENTICAL} "
                f"(sha256 etalon bilan teng)")
            return

        # Etalon parse natijasi LRU da saqlanadi — bitta shablon o'nlab
        # faylga ishlatiladi, yirik smeta esa har safar qayta parse bo'lardi.
        et_varaqlar, et_xato = load_etalon_sheets(etalon_yoli)
        if not et_varaqlar:
            log(f"  [texnik] etalon o'qilmadi: {etalon_yoli}")
            _texnik_iz(conn, ish, f"etalon o'qilmadi: {et_xato}",
                       yuklangan, etalon_yoli, rol)
            texnik_qoyib_yubor(conn, uid, f"etalon o'qilmadi: {et_xato}", token=token)
            return

        # 3) tekshirish
        #
        # `oqish_hisoboti` — o'qish qatlamining ISHONCHLILIK belgisi. Fayl
        # zaxira zanjiri orqali tiklangan bo'lsa `usul`, matn lug'ati zipda
        # umuman bo'lmagan bo'lsa `matn_tiklanmadi` yoziladi. Sog'lom faylda
        # lug'at BO'SH qoladi — qo'shimcha ish yo'q.
        oqish_hisoboti = {}
        try:
            pt_varaqlar, xato = read_file(yuklangan, oqish_hisoboti), None
        except ExcelTooLargeError as exc:
            # Zararli/nosog'lom fayl (merge bomba, ulkan varaq) — bu
            # ishtirokchining odatiy xatosi emas, ODAM ko'rib chiqsin.
            log(f"  [XAVFLI FAYL] {fayl_nomi[:45]}: {exc}", "error")
            _texnik_iz(conn, ish, f"xavfsizlik chegarasi: {exc}",
                       yuklangan, etalon_yoli, rol)
            texnik_qoyib_yubor(conn, uid, f"xavfsizlik chegarasi: {exc}",
                               doimiy=True, token=token)
            return
        except Exception as exc:
            pt_varaqlar, xato = None, str(exc)

        if pt_varaqlar is None:
            if not os.path.isfile(yuklangan):
                _texnik_iz(conn, ish, f"yuklangan fayl yo'qoldi: {xato}",
                           etalon_yoli=etalon_yoli, rol=rol)
                texnik_qoyib_yubor(conn, uid, f"yuklangan fayl yo'qoldi: {xato}", token=token)
                return
            # #2 (2026-09-07): ochilmagan fayl RAD emas — QABUL + aniq izoh.
            res = _ochilmadi_res(fayl_nomi, rol, xato)
        else:
            # S1b (#6, 2026-09-07) — mazmun-tenglik: sha256 darvozasi
            # o'tkazib yuborgan «qayta saqlangan» nusxa. validate_one dan OLDIN.
            res = _mazmun_nusxa_res(fayl_nomi, rol, et_varaqlar, pt_varaqlar)
            if res is None:
                res = validate_one(rol, et_varaqlar, pt_varaqlar, fayl_nomi)
            # #7 (2026-09-07) — ACCEPT yo'lida bo'sh-hujjat himoyasi: hech
            # qanday yangi qiymat yo'q bo'lsa qabul emas (Q1/Q3 bu yo'lda
            # ilgari umuman ishlamasdi). Uch istisno `bosh_hujjat_qabulmi` ichida.
            if res["status"] == STATUS_FILLED:
                try:
                    if _decision.bosh_hujjat_qabulmi(et_varaqlar, pt_varaqlar):
                        res = _bosh_hujjat_res(fayl_nomi, rol)
                except Exception as exc:          # asosiy oqim yiqilmasin
                    log(f"  [bosh-hujjat] hisoblanmadi ({type(exc).__name__})",
                        "warning")
        res["_template_file_id"] = tfid
        if etalon_note:
            # F2: note — statusga/izohga ta'sir yo'q, faqat audit izi
            res.setdefault("findings", []).append(dict(etalon_note))

        # AUDIT IZI: eski dvigatelning HAQIQIY natijasi shakl-erkin/flip
        # almashtirishlaridan OLDIN muzlatiladi — engine-0.1 yozuvi shu.
        # (Ilgari bu 3a dan keyin turardi va shakl-erkin fayllarda
        # engine-0.1 ga yangi hukm yozilib, ikki dvigatel solishtiruvi
        # buzilardi.)
        res_eski = dict(res)

        # 3a) SHAKL-ERKIN HUKM — buyurtmachi shabloni tanilmaganda ham verdikt
        #
        # Buyurtmachi ko'rsatmasi (2026-08-19): «tender uchun notanish
        # ko'rinishdagi buyurtmachi fayli kelsa ham HAR DOIM qabul qilib,
        # uni ishtirokchi fayllari bilan solishtirish kerak». Ilgari bunday
        # hujjat verdiktsiz qolardi (13 855 lik yurishda 679 fayl).
        #
        # Bu yo'l sarlavha/rol/ustun aniqlashga TAYANMAYDI — faqat xom katak
        # qiymatlarini solishtiradi: aynan nusxa → rad, narx bor → qabul,
        # siyrak → rad. Ya'ni istalgan shakldagi hujjatga javob bera oladi.
        # MATN TIKLANMAGAN HUJJAT (2026-09-07). Zaxira zanjiri faylni ochgan,
        # lekin uning MATN lug'ati zipda umuman yo'q edi — sonlar to'liq,
        # matn esa o'rinbosar. Bunday hujjatda `validate_one` MATNGA tayangan
        # kodlar beradi (HEADER_NOT_FOUND va h.k.) va ular YOLG'ON bo'ladi:
        # ishtirokchi «sarlavhani o'chirgani» uchun emas, fayl YO'LDA
        # buzilgani uchun. Shuning uchun eski dvigatel verdikti QANDAY
        # bo'lishidan qat'i nazar sonlar bo'yicha hukmga o'tamiz.
        matn_ishonchsiz = bool(oqish_hisoboti.get("matn_tiklanmadi"))
        if ((res["status"] == 0 or matn_ishonchsiz)
                and pt_varaqlar):
            try:
                h = _decision.hukm_shakl_erkin(et_varaqlar, pt_varaqlar,
                                               matn_ishonchsiz=matn_ishonchsiz)
            except Exception as exc:        # asosiy oqim yiqilmasin
                log(f"  [shakl-erkin] hisoblanmadi ({type(exc).__name__}: "
                    f"{exc}) — texnik holat saqlanadi", "warning")
            else:
                # `tashqi=0` — hukm «ayta olmayman» dedi; uni pastdagi texnik
                # blok tanishi uchun findingga severity qo'yiladi (aks holda
                # sabab matni yo'qolib, «tekshirib bo'lmadi» yozilardi).
                jiddiy = "technical" if h["tashqi"] == 0 else None
                findings = [{"code": k, "sheet": None, "severity": jiddiy,
                             "detail_uz": h["comment_uz"]} for k in h["kodlar"]]
                res = {"file": fayl_nomi, "role": rol, "status": h["tashqi"],
                       "findings": findings, "comment_uz": h["comment_uz"],
                       "_template_file_id": tfid, "_shakl_erkin": True,
                       # audit: ichki holat va o'lchovlar evidence'ga o'tadi
                       # (REVIEW_AMBIGUOUS → sinf='review' izi yo'qolmasin)
                       "_ichki": h["ichki"],
                       "_review_sabab": h.get("review_sabab"),
                       "_band": h.get("band"),
                       "_narxli_band": h.get("narxli_band"),
                       "_yangi_raqamlar": h.get("yangi_raqamlar")}
                if etalon_note:
                    res["findings"].append(dict(etalon_note))   # F2 belgisi saqlansin
                if h.get("matn_ishonchsiz"):
                    # Band o'lchovi bunday hujjatda yo'q — sonlar aytiladi.
                    log(f"  [matnsiz] {fayl_nomi[:40]}: "
                        f"{h.get('yangi_raqamlar')} ta yangi son "
                        f"→ status={h['tashqi']}")
                else:
                    log(f"  [shakl-erkin] {fayl_nomi[:40]}: "
                        f"{h.get('narxli_band')}/{h.get('band')} band narxlangan "
                        f"→ status={h['tashqi']}")

        # Matn tiklanmagan, lekin sonlar bo'yicha hukm ham chiqmadi
        # (`hukm_shakl_erkin` yiqildi) — bu holda
        # `res` da MATNGA tayangan verdikt turibdi va uni yozib bo'lmaydi.
        # Texnik holatga o'tkazamiz: OLTIN QOIDA bo'yicha status 0 qoladi.
        if matn_ishonchsiz and not res.get("_shakl_erkin"):
            res = {"file": fayl_nomi, "role": rol, "status": 0,
                   "findings": [{"code": "TEXT_UNRECOVERED", "sheet": None,
                                 "severity": "technical",
                                 "detail_uz": "hujjatning matn qismi tiklanmadi "
                                              "va sonlar bo'yicha hukm "
                                              "chiqarilmadi"}],
                   "comment_uz": "", "_template_file_id": tfid}

        # Tekshiruv «tushunmadim» desa — bu TEXNIK holat, rad javob emas.
        # Masalan buyurtmachi shabloni bizga notanish ko'rinishda bo'lsa,
        # ishtirokchini ayblab bo'lmaydi (OLTIN QOIDA).
        if res["status"] == 0:
            texnik = next((f for f in res.get("findings", [])
                           if f.get("severity") == "technical"), {})
            sabab = texnik.get("detail_uz") or "tekshirib bo'lmadi"
            # ETALON_UNPARSED — DETERMINIK sabab: etalon bayti o'zgarmasa
            # natija ham o'zgarmaydi, qayta urinish faqat vaqt sarflaydi.
            # 2026-08-17 da 13 855 lik yurishda shu sabab 692 faylni
            # 5 martadan qayta yuklattirdi (~2 000 ortiqcha so'rov).
            # Darhol «o'lik» qilamiz: `status` BARIBIR 0 qoladi (OLTIN QOIDA
            # buzilmaydi), qator `--dead-letters` da ko'rinadi va sarlavha
            # qoidalari kengaytirilgach `--requeue` bilan qaytariladi.
            # TEXT_UNRECOVERED ham DETERMINIK: fayl bayti o'zgarmasa matn
            # lug'ati baribir qaytmaydi. Qayta urinish faqat vaqt sarflaydi,
            # qator esa `--dead-letters` da ODAM ko'rigiga chiqadi.
            doimiy = texnik.get("code") in ("ETALON_UNPARSED", "TEXT_UNRECOVERED")
            log(f"  [texnik{'/o_lik' if doimiy else ''}] "
                f"{fayl_nomi[:45]}: {sabab[:90]}")
            _texnik_iz(conn, ish, sabab, yuklangan, etalon_yoli, rol)
            texnik_qoyib_yubor(conn, uid, sabab, doimiy=doimiy, token=token)
            return

        # 3b) YAKUNIY HUKM — «narx bor — qabul» (buyurtmachi qarori 2026-08-12)
        #
        # Buyurtmachi shablonni NOTO'G'RI slotga yuklagan bo'lishi mumkin
        # (korpusda 58% shunday edi) — u holda tuzilma farqi ishtirokchining
        # aybi emas. Shuning uchun faqat-struktura kodlari bilan rad etilgan
        # hujjat, agar ISHTIROKCHINING O'Z faylida narxlar jiddiy hajmda
        # bo'lsa, qabul qilinadi. Q1 (aynan nusxa) va Q3 (jiddiy to'ldirish)
        # `decision.hukm` ichida saqlanadi — bo'sh hujjat baribir rad etiladi.
        #
        # `res_eski` yuqorida (validate_one dan keyin darhol) muzlatilgan —
        # evidence'da eski dvigatel verdikti (engine-0.1) va yakuniy hukm
        # (engine-0.2) alohida yozuvlarda saqlanadi.
        if (res["status"] == STATUS_DEFECT
                and not res.get("_shakl_erkin")      # shakl-erkin hukm allaqachon chiqarilgan
                and et_varaqlar and pt_varaqlar):
            try:
                h = _decision.hukm(rol, et_varaqlar, pt_varaqlar, fayl_nomi,
                                   eski_res=res)
            except Exception as exc:
                # Hukm hisoblanmasa eski verdikt qoladi — yangi qatlam
                # asosiy oqimni yiqitmasligi kerak.
                log(f"  [hukm] hisoblanmadi ({type(exc).__name__}: {exc}) — "
                    f"eski verdikt saqlanadi", "warning")
            else:
                if h["tashqi"] == STATUS_FILLED:
                    log(f"  [QAYTA KO'RIB] {fayl_nomi[:45]}: "
                        f"{h['review_sabab']}")
                    res["status"] = STATUS_FILLED
                    res["comment_uz"] = _render_comment(fayl_nomi, [])
                    res["_ichki"] = h["ichki"]
                    res["_review_sabab"] = h.get("review_sabab")
                    res["_band"] = h.get("band")
                    res["_narxli_band"] = h.get("narxli_band")
                    res["_yangi_raqamlar"] = h.get("yangi_raqamlar")

        # 3c) REVIEW→1 KAFOLATI (2026-08-11 qarorining sharti): «ishonchsizlik
        # bilan qabul» FAQAT `sinf='review'` izi bilan yoziladi. Verdikt
        # `send` muzlatgach QAYTMAS, iz esa keyin tiklab bo'lmaydi — shuning
        # uchun iz OLDIN yoziladi; yozilmasa qabul BEKOR bo'lib, konservativ
        # natija qoladi (rad yoki texnik).
        review_qabul = (res["status"] == STATUS_FILLED
                        and res.get("_ichki") == _evidence.REVIEW_AMBIGUOUS)
        evidence_yozildi = False
        if review_qabul:
            evidence_yozildi = _soya_evidence(
                conn, ish, res_eski, yuklangan, etalon_yoli, tfid,
                et_varaqlar=et_varaqlar, pt_varaqlar=pt_varaqlar,
                res_final=res, majburiy=True)
            if not evidence_yozildi:
                # Audit infratuzilmasi ishlamasligi — BIZNING texnik
                # muammomiz, hujjat kamchiligi emas (OLTIN QOIDA). Shuning
                # uchun bu yerda HECH QANDAY verdikt yozilmaydi: rad ham
                # (u noo'rin bo'lishi mumkin edi — flip aynan shuning uchun
                # bor), qabul ham (izsiz qabul 2026-08-11 shartini buzadi).
                # Qator status 0 da qoladi va evidence tiklangach qayta
                # tekshiriladi.
                log(f"  [REVIEW BEKOR] {fayl_nomi[:45]} — audit izi "
                    f"yozilmadi, verdikt kechiktirildi", "error")
                texnik_qoyib_yubor(
                    conn, uid,
                    "audit izi yozilmadi (validation_evidence) — "
                    "REVIEW verdikt kechiktirildi", token=token)
                return

        # 4) natijani yozish
        yozildi = natijani_yoz(conn, ish, token, res)
        if not yozildi:
            log(f"  [o'tkazildi] {fayl_nomi[:45]} — claim boshqada yoki send=true")
            return
        if not evidence_yozildi:
            _soya_evidence(conn, ish, res_eski, yuklangan, etalon_yoli, tfid,
                           et_varaqlar=et_varaqlar,
                           pt_varaqlar=pt_varaqlar, res_final=res)
        belgi = "✓" if res["status"] == STATUS_FILLED else "✗"
        log(f"  {belgi} [{res['role']}] {fayl_nomi[:45]} → status={res['status']}")
        if res["status"] == STATUS_DEFECT:
            log(f"      {res['comment_uz'][:150]}")
        # Verdikt YOZILGAN yagona yo'l — chaqiruvchi (sikl, test, regressiya
        # yurgizgichi) buni bilsin. Qolgan hamma yo'lda None (texnik holat,
        # claim boshqada, send=true). Outbox qatori `natijani_yoz` ichida,
        # verdikt bilan bir tranzaksiyada yozilgan.
        return {"id": ish["uuid"], "file_id": ish.get("bidder_id"),
                "status": res["status"], "comment": res["comment_uz"]}
    finally:
        if yuklangan:
            _w._cleanup_downloads([yuklangan], ish_papka)
