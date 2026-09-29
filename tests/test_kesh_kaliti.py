# -*- coding: utf-8 -*-
"""B1 (2026-09-15) — etalon keshi kaliti `id` EMAS, HAVOLA xeshidan.

NUQSON (2026-08-17 da jonli sodir bo'lgan). Kesh fayli `tpl_<id>.xlsx` deb
nomlanardi va shu nom topilsa darhol ishlatilardi. Sherik jamoa jadvalni
almashtirganda `id` lar 1 dan qayta boshlangan va 2 499 shablondan
**2 497 tasida** keshda BOSHQA TENDERNING fayli turgan — tizim buni sezmay,
noto'g'ri etalon bilan solishtirib verdikt yozgan.

Bu testlar shu holatni AYNAN takrorlaydi va endi takrorlanmasligini
tekshiradi. Bazaga ULANMAYDI.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import templates as td    # noqa: E402


def test_id_bir_xil_havola_boshqa_bolsa_kesh_yoli_HAR_XIL():
    """ENG MUHIM TEST — 2026-08-17 nosozligining aynan o'zi.

    Bir xil `templates.id`, lekin boshqa tenderning havolasi: kesh yo'li
    ALBATTA boshqa bo'lishi kerak, aks holda eski shablon ishlatiladi.
    """
    a = td._kesh_yoli(5620, "269390/excel1/9468/385db905-86c4-445a-ac24-3e99ee69384a.xlsx")
    b = td._kesh_yoli(5620, "274183/excel2/12878/6e3f821e-bfe4-4818-a8e9-9fce079f2a6c.xlsx")
    assert a != b, "id qayta ishlatilganda kesh TO'QNASHADI — 08-17 nosozligi"


def test_bir_xil_havola_bir_xil_yol():
    """Kesh ishlashi uchun kalit barqaror bo'lishi shart."""
    h = "269390/excel1/9468/385db905-86c4-445a-ac24-3e99ee69384a.xlsx"
    assert td._kesh_yoli(5620, h) == td._kesh_yoli(5620, h)
    # `id` o'zgarsa ham (sherik qayta yuklasa) bir xil fayl — bir xil kesh
    assert td._kesh_yoli(5620, h) == td._kesh_yoli(99999, h)


def test_kengaytma_havoladan_olinadi():
    x = td._kesh_yoli(1, "a/b/c/d.xls")
    assert x.endswith(".xls")
    assert td._kesh_yoli(1, "a/b/c/d.xlsx").endswith(".xlsx")


def test_yangi_prefiks_eskisidan_ajraladi():
    """Eski/yangi formatni aralashtirib yuborish mumkin bo'lmasin."""
    nom = os.path.basename(td._kesh_yoli(7, "x/y/z/q.xlsx"))
    assert nom.startswith("tplh_")
    assert not td._ESKI_KESH_RE.match(nom), "yangi nom eski naqshga tushib qoldi"
    # Eski nomlar esa naqshga tushsin
    assert td._ESKI_KESH_RE.match("tpl_5620.xlsx")
    assert td._ESKI_KESH_RE.match("tpl_5620.1234.xls")


def test_kalit_16_hex():
    k = td._kesh_kaliti("a/b/c.xlsx")
    assert len(k) == 16 and all(c in "0123456789abcdef" for c in k)
    assert td._kesh_kaliti(None) == td._kesh_kaliti("")


def test_eski_keshni_tozala_faqat_eskisini_ochiradi(tmp_path, monkeypatch):
    """Tozalash yangi formatdagi va begona fayllarga TEGMASIN."""
    monkeypatch.setattr(td, "ETALON_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(td, "_eski_tozalandi", False)
    monkeypatch.delenv("ETALON_ESKI_KESHNI_SAQLA", raising=False)

    eski = ["tpl_1.xlsx", "tpl_5620.xls", "tpl_77.1234.xlsx"]
    yangi = ["tplh_0123456789abcdef.xlsx", "tplh_ffffffffffffffff.xls"]
    begona = ["boshqa.txt", "tpl_.xlsx"]        # `tpl_` dan keyin raqam yo'q
    for nom in eski + yangi + begona:
        (tmp_path / nom).write_bytes(b"x")

    n = td.eski_keshni_tozala()
    qolgan = sorted(os.listdir(tmp_path))
    assert n == len(eski), f"o'chirilgan: {n}, kutilgan: {len(eski)}"
    assert qolgan == sorted(yangi + begona), qolgan


def test_tozalash_bir_marta_ishlaydi(tmp_path, monkeypatch):
    monkeypatch.setattr(td, "ETALON_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(td, "_eski_tozalandi", False)
    monkeypatch.delenv("ETALON_ESKI_KESHNI_SAQLA", raising=False)
    (tmp_path / "tpl_1.xlsx").write_bytes(b"x")
    assert td.eski_keshni_tozala() == 1
    (tmp_path / "tpl_2.xlsx").write_bytes(b"x")
    assert td.eski_keshni_tozala() == 0        # ikkinchi marta ishlamaydi
    assert (tmp_path / "tpl_2.xlsx").exists()


def test_saqlash_kaliti_tozalashni_otkazib_yuboradi(tmp_path, monkeypatch):
    monkeypatch.setattr(td, "ETALON_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(td, "_eski_tozalandi", False)
    monkeypatch.setenv("ETALON_ESKI_KESHNI_SAQLA", "1")
    (tmp_path / "tpl_1.xlsx").write_bytes(b"x")
    assert td.eski_keshni_tozala() == 0
    assert (tmp_path / "tpl_1.xlsx").exists()
