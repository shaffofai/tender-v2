# -*- coding: utf-8 -*-
"""P0 — audit izi kafolatlari (ROADMAP K1, M1, M3, M5, M6).

Qoidalar:
  • engine-0.1 yozuvi HAR DOIM eski dvigatelning haqiqiy natijasi;
  • REVIEW→1 qabul FAQAT sinf='review' izi bilan (iz yozilmasa — bekor);
  • siyosat chegaralari o'zgarsa SIYOSAT_VERSIYA ham o'zgarishi SHART;
  • audit jadvali sxema (migratsiya) darajasida append-only.
"""
import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.worker import audit, gates
from tender_engine import decision, evidence as ev


class _QoqConn:
    """`conn` faqat evidence.yoz ichida ishlatiladi — testda o'sha yamalanadi."""
    def rollback(self):
        pass


def _fayl(tmp_path, nom="f.xlsx", mazmun=b"PK\x03\x04 test"):
    yol = tmp_path / nom
    yol.write_bytes(mazmun)
    return str(yol)


def _ish():
    return {"uuid": "42", "link": "1/excel1/2/f.xlsx",
            "tender_id": 1, "type": "excel1"}


def _yig(monkeypatch):
    """evidence.yoz ni yozuvlarni ro'yxatga yig'adigan qilib almashtiradi."""
    yozuvlar = []
    monkeypatch.setattr(ev, "yoz", lambda conn, yozuv: yozuvlar.append(yozuv))
    return yozuvlar


def test_engine01_har_doim_eski_natija(tmp_path, monkeypatch):
    """Shakl-erkin yo'lida engine-0.1 yozuvi ESKI (texnik) natijani ko'rsatsin,
    yangi hukmni emas — M1 regressiyasi."""
    yozuvlar = _yig(monkeypatch)
    res_eski = {"status": 0, "role": "narxlar", "comment_uz": "",
                "findings": [{"code": "ETALON_UNPARSED",
                              "severity": "technical", "detail_uz": "x"}]}
    res_final = {"status": 1, "role": "narxlar",
                 "comment_uz": "Hujjat to'g'ri to'ldirilgan.",
                 "findings": [], "_shakl_erkin": True,
                 "_ichki": ev.REVIEW_AMBIGUOUS,
                 "_review_sabab": "shablon tanilmadi; 40/50 band narxlangan",
                 "_band": 50, "_narxli_band": 40, "_yangi_raqamlar": 120}
    ok = audit._soya_evidence(_QoqConn(), _ish(), res_eski,
                           _fayl(tmp_path), None, None,
                           res_final=res_final)
    assert ok is True
    assert len(yozuvlar) == 2
    eski, soya = yozuvlar
    assert eski["validator_version"] == ev.ENGINE_VERSION
    assert eski["ichki_status"] == ev.TECHNICAL_ERROR       # haqiqiy eski natija
    assert soya["validator_version"] == "engine-0.2-shadow"
    assert soya["ichki_status"] == ev.REVIEW_AMBIGUOUS
    assert soya["sinf"] == "review"                          # K1 — iz bor
    assert soya["varaq_dalil"]["band"] == 50
    assert soya["varaq_dalil"]["narxli_band"] == 40
    assert soya["varaq_dalil"]["siyosat"] == decision.SIYOSAT_VERSIYA


def test_majburiy_rejim_xatoda_false(tmp_path, monkeypatch):
    """REVIEW→1 kafolati shu qiymatga tayanadi: yozuv yiqilsa False."""
    def yiqil(conn, yozuv):
        raise RuntimeError("jadval yo'q")
    monkeypatch.setattr(ev, "yoz", yiqil)
    res = {"status": 1, "role": "narxlar", "comment_uz": "", "findings": [],
           "_ichki": ev.REVIEW_AMBIGUOUS}
    ok = audit._soya_evidence(_QoqConn(), _ish(), res, _fayl(tmp_path),
                           None, None, res_final=res, majburiy=True)
    assert ok is False


def test_majburiy_emas_xato_yutiladi(tmp_path, monkeypatch):
    """Oddiy rejimda xato asosiy oqimni yiqitmaydi (False, exception yo'q)."""
    def yiqil(conn, yozuv):
        raise RuntimeError("ulanish uzildi")
    monkeypatch.setattr(ev, "yoz", yiqil)
    res = {"status": 2, "role": "resurs", "comment_uz": "rad", "findings": []}
    ok = audit._soya_evidence(_QoqConn(), _ish(), res, _fayl(tmp_path), None, None)
    assert ok is False


def test_texnik_iz_yoziladi(tmp_path, monkeypatch):
    """M7: texnik holat ham evidence'da TECHNICAL_ERROR izini qoldiradi."""
    yozuvlar = _yig(monkeypatch)
    audit._texnik_iz(_QoqConn(), _ish(), "yuklab olishda xato",
                  yuklangan=_fayl(tmp_path))
    assert len(yozuvlar) == 1
    assert yozuvlar[0]["ichki_status"] == ev.TECHNICAL_ERROR
    assert yozuvlar[0]["tashqi_status"] == 0
    # fayl yuklanmagan holatda ham yiqilmaydi (fayl_hash="" NOT NULL uchun)
    audit._texnik_iz(_QoqConn(), _ish(), "404")
    assert yozuvlar[1]["fayl_hash"] == ""


def test_siyosat_barmoq_izi():
    """M5: chegara qiymatlari o'zgarsa SIYOSAT_VERSIYA ham o'zgarishi SHART.

    Bu test yiqilsa: (1) decision.SIYOSAT_VERSIYA ni yangi sanaga ko'taring,
    (2) quyidagi QAYD juftligini yangi qiymatlarga yangilang. IKKALASINI ham —
    aks holda evidence'dagi `siyosat` maydoni yolg'on bo'lib qoladi.
    """
    from tender_engine.rules import common as sv
    chegaralar = (
        decision.NARX_NISBATI, decision.NARX_MIN_BAND,
        decision.BOSH_USTUN_MIN, decision.SHAKL_ERKIN_MIN_SON,
        decision.NARX_MIN_QIYMAT, decision.NARX_MIN_KASR,
        decision.REVIEW_YANGI_RAQAM,
        decision.SHAKL_ERKIN_KOP_SON,          # #14-son (2026-09-10)
        decision.HUKM_KOP_SON,                 # #14-h  (2026-09-10b)
        tuple(sorted(sv._Q3_SOZLAMA.items())),
    )
    iz = hashlib.sha256(repr(chegaralar).encode()).hexdigest()[:16]

    # s2026-09-08: chegaralar o'zgarmagan (iz 50270b39f2a0e755), qoidalar
    # to'plami kengaydi (#6, #7, #9, #9b, #10, #11-b, #12-V3).
    # s2026-09-10: #14-son — SHAKL_ERKIN_KOP_SON=500 barmoq-iziga qo'shildi.
    # s2026-09-10b: #14-h — HUKM_KOP_SON=300 qo'shildi (o'sha qoida `hukm`
    # yo'lida; SHEET_UNFILLED istisnosi bilan, A+B da regressiya 0).
    # s2026-09-11: P1 — chegaralar o'zgarmagan (iz bir xil), qoida PRICE_SPARSE
    # va shakl-erkin yo'llariga kengaytirildi.
    # s2026-09-15: NOL = QIYMAT EMAS hamma o'lchovda (bosh_ustun_holati,
    # narx_ustuni_toldirilganmi) — chegaralar o'zgarmagan, qoida o'zgardi.
    # s2026-09-17: BEKOR — buyurtmachi: «0 bilan to'ldirsa ham qabul»;
    # ikkala o'lchov 09-14 holatiga qaytdi, chegaralar o'zgarmagan (iz bir xil).
    QAYD = ("s2026-09-17", "f52fda84dcae9582")
    assert (decision.SIYOSAT_VERSIYA, iz) == QAYD, (
        f"Siyosat chegaralari o'zgargan (iz={iz}, versiya="
        f"{decision.SIYOSAT_VERSIYA}) — SIYOSAT_VERSIYA ni ko'tarib, "
        f"QAYD ni ({decision.SIYOSAT_VERSIYA!r}, {iz!r}) ga yangilang.")


def _migratsiya_sql():
    ildiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    yol = os.path.join(ildiz, "app", "db", "migrations", "0001_boshlangich_sxema.sql")
    with open(yol, encoding="utf-8") as fh:
        return fh.read()


def test_evidence_ddl_append_only():
    """M6: sxemada (migratsiyada) UPDATE/DELETE taqiqlovchi trigger bo'lishi shart."""
    ddl = _migratsiya_sql()
    assert "ve_append_only" in ddl
    assert "BEFORE UPDATE OR DELETE ON validation_evidence" in ddl
    assert "RAISE EXCEPTION" in ddl


def test_gate_res_evidence_bilan_mos(tmp_path, monkeypatch):
    """Q1 darvoza natijasi ham evidence'ga yozila oladi (regressiya)."""
    yozuvlar = _yig(monkeypatch)
    et = _fayl(tmp_path, "etalon.xlsx", b"PK\x03\x04 shablon " * 50)
    import shutil
    pt = str(tmp_path / "pt.xlsx")
    shutil.copyfile(et, pt)
    res = gates._aynan_nusxa_res("pt.xlsx", "resurs", pt, et)
    assert res is not None
    ok = audit._soya_evidence(_QoqConn(), _ish(), res, pt, et, None)
    assert ok is True
    # 2026-09-18: darvoza status 3 → audit izida IDENTICAL_COPY / tashqi 3
    # (ilgari REJECT_NO_PRICE / 2 edi)
    assert yozuvlar[0]["ichki_status"] == ev.IDENTICAL_COPY
    assert yozuvlar[0]["tashqi_status"] == 3
    assert "AYNAN_NUSXA" in yozuvlar[0]["kodlar"]
    assert yozuvlar[0]["fayl_hash"] == yozuvlar[0]["etalon_hash"]
