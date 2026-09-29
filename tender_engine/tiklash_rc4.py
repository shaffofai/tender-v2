# -*- coding: utf-8 -*-
"""Excel ning STANDART «faqat o'qish» parolini ochish (RC4 standard, BIFF8).

MUAMMO. Platformadagi 4 ta `.xls` fayl xlrd da `XLRDError('Workbook is
encrypted')` bilan yiqiladi va hujjat «Faylni ochib bo'lmadi» deb RAD
etiladi. Aslida bu MAXFIY parol EMAS: Excel «faqat o'qish uchun tavsiya
etiladi» himoyasini qo'yganda faylni O'ZINING o'zgarmas ichki paroli —
`VelvetSweatshop` — bilan shifrlaydi. Excel bunday faylni foydalanuvchidan
hech narsa so'ramasdan ochadi, ya'ni ishtirokchi hech qanday xato
qilmagan; rad BIZNING o'quvchimizning cheklovi edi.

USUL (MS-OFFCRYPTO 2.3.6, «RC4 standard encryption»):
  1. OLE2 (CFB) dan `Workbook` oqimi olinadi;
  2. FILEPASS yozuvi (rc=0x002F) shifr turini aytadi — wType=1 va
     vMajor/vMinor=1/1 bo'lsagina RC4 standard, boshqa hollarda
     (CryptoAPI, AES) TOZA rad etamiz;
  3. faqat MA'LUM standart parollar (`VelvetSweatshop` va bo'sh parol)
     verifier bilan tekshiriladi — PAROL TANLASH (brute-force) YO'Q,
     u maxfiy hujjatni buzish bo'lardi;
  4. mos kelsa butun oqim 1024-baytli bloklar bilan deshifrlanadi
     (har blok uchun alohida RC4 kaliti — MS-OFFCRYPTO 2.3.5.2);
  5. FILEPASS yozuvi olib tashlanib, deshifrlangan oqim YANGI OLE2
     ichiga yoziladi va vaqtinchalik `.xls` sifatida qaytariladi —
     uni odatdagi `reader._read_xls` hech qanday o'zgarishsiz o'qiydi.

XAVFSIZLIK QOIDALARI (loyihaning umumiy talablari):
  • Bu modul FAQAT o'qish xatosidan KEYIN chaqirilishi kerak — sog'lom
    fayl avvalgi yo'ldan aynan o'tadi, bu yerga umuman kirmaydi.
  • ASL FAYLGA TEGILMAYDI: natija har doim vaqtinchalik papkadagi yangi
    nusxa (`tozala()` yoki `tiklangan()` konteksti uni o'chiradi).
  • `TIKLASH_YOQ=0` (yoki `TIKLASH_RC4_YOQ=0`) modulni butunlay
    o'chiradi — standart holat YOQIQ.
  • Qidiruv/ta'mir siklarining hammasida QAT'IY tugun va vaqt budjeti
    bor (2026-09-02 dagi O(qator²) livelock takrorlanmasin): budjet
    tugasa `TiklashBudjeti` — toza istisno.
  • `tikla()` HECH QACHON istisno ko'tarmaydi. Tiklab bo'lmasa `None`
    qaytaradi va chaqiruvchi ASL xatoni qayta ko'taradi — ishtirokchiga
    ko'rinadigan izoh o'zgarmaydi.
  • Fayl/varaq chegaralari (`MAX_SHEET_ROWS`, `MAX_SHEET_COLS`) tiklangan
    oqimga HAM tatbiq etiladi, ya'ni ta'mir orqali «bomba» kirib kelmaydi.
"""

import contextlib
import hashlib
import os
import shutil
import struct
import tempfile
import time
import warnings


# ── Sozlamalar (hammasi muhit o'zgaruvchisi bilan) ─────────────────────────
def _yoqilganmi() -> bool:
    """`TIKLASH_YOQ=0` yoki `TIKLASH_RC4_YOQ=0` — o'chirish kaliti."""
    for nom in ("TIKLASH_RC4_YOQ", "TIKLASH_YOQ"):
        if os.environ.get(nom, "1").strip() == "0":
            return False
    return True


# Vaqt budjeti butun tiklash uchun (OLE2 + deshifrlash + yozish).
MAX_SONIYA = float(os.environ.get("TIKLASH_RC4_MAX_SONIYA", "120"))
# Fayl va deshifrlangan oqim uchun hajm shifti — «ta'mir orqali bomba» ga qarshi.
MAX_MB = int(os.environ.get("TIKLASH_RC4_MAX_MB", "64"))
# BIFF yozuvlarini aylanishda tugun chegarasi (buzuq `ln` cheksiz sikl bermasin).
MAX_YOZUV = int(os.environ.get("TIKLASH_RC4_MAX_YOZUV", "4000000"))
# Tiklangan hujjatga ham reader bilan BIR XIL varaq chegaralari.
MAX_SHEET_ROWS = int(os.environ.get("MAX_SHEET_ROWS", "200000"))
MAX_SHEET_COLS = int(os.environ.get("MAX_SHEET_COLS", "2000"))

# FAQAT ma'lum standart parollar. Bu ro'yxat KENGAYTIRILMASIN — «keng
# tarqalgan parollar» qo'shish parol tanlashga aylanadi.
PAROLLAR = ("VelvetSweatshop", "")

_OLE_IMZO = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

# BIFF yozuv kodlari
_FILEPASS = 0x002F
_BOUNDSHEET = 0x0085
_DIMENSIONS = 0x0200
_EOF = 0x000A

# Shifrlanmaydigan yozuvlar (MS-XLS 2.2.10) — keystream pozitsiyasi baribir siljiydi.
_OCHIQ_YOZUVLAR = frozenset((0x0809, 0x002F, 0x00E1, 0x0194, 0x0195,
                             0x0161, 0x0060, 0x0138, 0x0139, 0x009C))
_BLOK = 1024


class TiklashRad(Exception):
    """Tiklash bu faylga TATBIQ ETILMAYDI (shifr turi boshqa, parol mos emas ...)."""


class TiklashBudjeti(TiklashRad):
    """Vaqt yoki tugun budjeti tugadi — qidiruv to'xtatildi."""


class _Budjet:
    """Vaqt chegarasi. Har og'ir siklda `tekshir()` chaqiriladi."""

    def __init__(self, soniya: float = None):
        self.chek = time.monotonic() + (MAX_SONIYA if soniya is None else soniya)

    def tekshir(self, qayerda: str) -> None:
        if time.monotonic() > self.chek:
            raise TiklashBudjeti(f"vaqt budjeti tugadi ({qayerda})")


# ══ 1-qism: minimal OLE2 (CFB) o'quvchi ════════════════════════════════════
# `olefile` kutubxonasi loyihada yo'q va YANGI KUTUBXONA O'RNATILMAYDI,
# shuning uchun MS-CFB ning bizga kerak qismi qo'lda o'qiladi: faqat
# `Workbook`/`Book` oqimini ajratib olish uchun.

_ENDOFCHAIN = 0xFFFFFFFE
_FREESECT = 0xFFFFFFFF
_FATSECT = 0xFFFFFFFD
_DIFSECT = 0xFFFFFFFC


class _Ole2:
    """CFB konteynerdan nomlangan oqimni o'qiydi (faqat o'qish, ta'mirsiz)."""

    def __init__(self, data: bytes, budjet: _Budjet):
        if data[:8] != _OLE_IMZO:
            raise TiklashRad("OLE2 imzosi yo'q — bu BIFF8 `.xls` emas")
        # CFB sarlavhasi HAR DOIM 512 bayt (MS-CFB 2.2). Kaltaroq fayl kesilgan:
        # tekshirmasak quyidagi `unpack_from` lar xom `struct.error` berardi.
        if len(data) < 512:
            raise TiklashRad("CFB sarlavhasi kesilgan (%d bayt)" % len(data))
        self.d = data
        self.b = budjet
        self.sec_shift = struct.unpack_from("<H", data, 30)[0]
        self.mini_shift = struct.unpack_from("<H", data, 32)[0]
        # Sektor o'lchami faqat 512 yoki 4096 bo'lishi mumkin (MS-CFB 2.2);
        # boshqa qiymat — buzuq sarlavha, hisob-kitobga ishonib bo'lmaydi.
        if self.sec_shift not in (9, 12) or not (4 <= self.mini_shift <= 12):
            raise TiklashRad("CFB sarlavhasidagi sektor o'lchami noto'g'ri")
        self.ssz = 1 << self.sec_shift
        self.msz = 1 << self.mini_shift
        self.n_fat = struct.unpack_from("<I", data, 44)[0]
        self.dir_start = struct.unpack_from("<I", data, 48)[0]
        self.mini_cutoff = struct.unpack_from("<I", data, 56)[0]
        self.minifat_start = struct.unpack_from("<I", data, 60)[0]
        self.difat_start = struct.unpack_from("<I", data, 68)[0]
        self.n_difat = struct.unpack_from("<I", data, 72)[0]
        # Sektorlarning MUMKIN BO'LGAN eng katta soni — fayl hajmidan kelib
        # chiqadi. Sarlavhadagi sonlarga ishonmaymiz (buzuq fayl «4 milliard
        # sektor» deb yozishi mumkin).
        self.max_sec = len(data) // self.ssz + 2
        self._difat_oq()
        self._fat_oq()
        self._dir_oq()
        self._minifat_oq()

    def _sek(self, n: int) -> bytes:
        o = (n + 1) << self.sec_shift
        return self.d[o:o + self.ssz]

    def _difat_oq(self) -> None:
        self.difat = list(struct.unpack_from("<109I", self.d, 76))
        nxt = self.difat_start
        per = self.ssz // 4 - 1
        korilgan = set()
        while nxt not in (_ENDOFCHAIN, _FREESECT) and nxt < self.max_sec:
            if nxt in korilgan:
                break                      # halqa — buzuq DIFAT zanjiri
            korilgan.add(nxt)
            self.b.tekshir("DIFAT")
            s = self._sek(nxt)
            if len(s) < self.ssz:
                break
            vals = struct.unpack_from("<%dI" % (self.ssz // 4), s, 0)
            self.difat.extend(vals[:per])
            nxt = vals[per]
        self.difat = [x for x in self.difat if x != _FREESECT and x < self.max_sec]

    def _fat_oq(self) -> None:
        fat = []
        for s in self.difat:
            self.b.tekshir("FAT")
            blk = self._sek(s)
            if len(blk) < self.ssz:
                break
            fat.extend(struct.unpack_from("<%dI" % (self.ssz // 4), blk, 0))
        self.fat = fat

    def _zanjir(self, start: int):
        out = []
        cur = start
        korilgan = set()
        while cur not in (_ENDOFCHAIN, _FREESECT, _FATSECT, _DIFSECT):
            if cur in korilgan or cur >= len(self.fat):
                break                      # halqa yoki chegaradan tashqari
            korilgan.add(cur)
            out.append(cur)
            cur = self.fat[cur]
            if len(out) > self.max_sec:
                break
        return out

    def _zanjirni_oq(self, start: int, size: int = None) -> bytes:
        buf = bytearray()
        for s in self._zanjir(start):
            buf += self._sek(s)
        return bytes(buf[:size]) if size is not None else bytes(buf)

    def _dir_oq(self) -> None:
        raw = self._zanjirni_oq(self.dir_start)
        self.entries = []
        for i in range(0, len(raw) - 127, 128):
            e = raw[i:i + 128]
            nlen = struct.unpack_from("<H", e, 64)[0]
            typ = e[66]
            if typ == 0:                   # bo'sh yozuv
                continue
            nom = e[:max(0, min(nlen - 2, 64))].decode("utf-16-le", "replace")
            self.entries.append({
                "name": nom, "type": typ,
                "start": struct.unpack_from("<I", e, 116)[0],
                "size": struct.unpack_from("<Q", e, 120)[0],
            })
        self.root = next((x for x in self.entries if x["type"] == 5), None)

    def _minifat_oq(self) -> None:
        raw = self._zanjirni_oq(self.minifat_start)
        self.minifat = (list(struct.unpack_from("<%dI" % (len(raw) // 4), raw, 0))
                        if raw else [])
        self.ministream = self._zanjirni_oq(self.root["start"]) if self.root else b""

    def _mini_zanjir(self, start: int):
        out = []
        cur = start
        korilgan = set()
        while cur not in (_ENDOFCHAIN, _FREESECT) and cur < len(self.minifat):
            if cur in korilgan:
                break
            korilgan.add(cur)
            out.append(cur)
            cur = self.minifat[cur]
        return out

    def oqim(self, nom: str):
        """Nomlangan oqim baytlari yoki None."""
        for e in self.entries:
            if e["name"] != nom or e["type"] != 2:
                continue
            if e["size"] > MAX_MB * 1024 * 1024:
                raise TiklashRad(
                    "«%s» oqimi juda katta (%d MB; chegara %d MB)"
                    % (nom, e["size"] // 1048576, MAX_MB))
            if e["size"] < self.mini_cutoff:
                buf = bytearray()
                for m in self._mini_zanjir(e["start"]):
                    o = m * self.msz
                    buf += self.ministream[o:o + self.msz]
                return bytes(buf[:e["size"]])
            return self._zanjirni_oq(e["start"], e["size"])
        return None


# ══ 2-qism: RC4 va MS-OFFCRYPTO kalit hosil qilish ═════════════════════════

def _rc4(kalit: bytes, data: bytes) -> bytes:
    """Sof-Python RC4. Faqat tiklash yo'lida ishlaydi (sog'lom faylda emas)."""
    S = list(range(256))
    j = 0
    kl = len(kalit)
    for i in range(256):
        j = (j + S[i] + kalit[i % kl]) & 0xFF
        S[i], S[j] = S[j], S[i]
    out = bytearray(len(data))
    i = j = 0
    for n, c in enumerate(data):
        i = (i + 1) & 0xFF
        j = (j + S[i]) & 0xFF
        S[i], S[j] = S[j], S[i]
        out[n] = c ^ S[(S[i] + S[j]) & 0xFF]
    return bytes(out)


def _hfinal(parol: str, salt: bytes) -> bytes:
    """MS-OFFCRYPTO 2.3.6.2: parol + salt -> oraliq xesh (MD5)."""
    h0 = hashlib.md5(parol.encode("utf-16-le")).digest()
    return hashlib.md5((h0[:5] + salt) * 16).digest()


def _blok_kaliti(hf: bytes, blok: int) -> bytes:
    """MS-OFFCRYPTO 2.3.5.2: har 1024-baytli blok uchun alohida RC4 kaliti."""
    return hashlib.md5(hf[:5] + struct.pack("<I", blok)).digest()


def _parol_mos(parol: str, salt: bytes, shifr_verifier: bytes,
               shifr_hash: bytes):
    """Parol to'g'rimi — verifier tekshiruvi (uzluksiz RC4 oqimi).

    Verifier 16 bayt tasodifiy ma'lumot, undan keyin uning MD5 xeshi.
    Deshifrlangach xesh mos kelsa — parol to'g'ri. Mos kelmasa oddiy
    «bu parol emas», hech narsa taxmin qilinmaydi.
    """
    hf = _hfinal(parol, salt)
    dec = _rc4(_blok_kaliti(hf, 0), shifr_verifier + shifr_hash)
    return hashlib.md5(dec[:16]).digest() == dec[16:32], hf


def _xor(a: bytes, b: bytes) -> bytes:
    """Baytma-bayt XOR — butun bo'lak bilan (Python siklidan ~20 barobar tez)."""
    n = len(a)
    return (int.from_bytes(a, "big") ^ int.from_bytes(b, "big")).to_bytes(n, "big")


# ══ 3-qism: BIFF oqimi bilan ishlash ═══════════════════════════════════════

def _yozuvlar(st: bytes, boshi: int = 0):
    """BIFF yozuvlarini (kod, tana_boshi, tana_oxiri) ko'rinishida beradi.

    Buzuq oqimda ham to'xtaydi: uzunlik oqimdan tashqariga chiqsa yoki
    yozuvlar soni `MAX_YOZUV` dan oshsa sikl tugaydi.
    """
    p = boshi
    n = len(st)
    sanoq = 0
    while p + 4 <= n:
        rc, ln = struct.unpack_from("<HH", st, p)
        a = p + 4
        b = min(a + ln, n)
        yield rc, a, b
        p = b
        sanoq += 1
        if sanoq > MAX_YOZUV:
            return
        if rc == 0 and ln == 0:
            return                         # to'ldiruvchi nollar — oxiri


def _filepass_top(st: bytes):
    """FILEPASS yozuvi: (tana, boshlanish_ofseti, tugash_ofseti) yoki None."""
    for rc, a, b in _yozuvlar(st):
        if rc == _FILEPASS:
            return st[a:b], a - 4, b
    return None


def _rc4_standardmi(fp: bytes) -> bool:
    """FILEPASS tanasi RC4 standard (Office 97/2000) ni bildiradimi.

    wType=1 (RC4), vMajor=1, vMinor=1 — MS-OFFCRYPTO 2.3.6.1. Boshqa
    kombinatsiya CryptoAPI (vMajor 2..4) yoki XOR obfuskatsiyasi (wType=0):
    ularni ochishga URINMAYMIZ.
    """
    if len(fp) < 54:
        return False
    w_type, v_major, v_minor = struct.unpack_from("<HHH", fp, 0)
    return w_type == 1 and v_major == 1 and v_minor == 1


def _deshifrla(st: bytes, hf: bytes, filepass_end: int, budjet: _Budjet) -> bytes:
    """Butun `Workbook` oqimini deshifrlaydi.

    Ikkita nozik joy:
      • Yozuv SARLAVHALARI (4 bayt) va ayrim yozuvlar (`_OCHIQ_YOZUVLAR`)
        shifrlanmaydi, LEKIN keystream pozitsiyasi baribir siljiydi —
        shuning uchun kalit oqimi oqimdagi ABSOLYUT ofsetdan hisoblanadi.
      • BOUNDSHEET8 da dastlabki 4 bayt (lbPlyPos) ochiq qoladi.
    """
    out = bytearray(st)
    kesh = {}

    def keystream(blok: int) -> bytes:
        # Bloklar deyarli har doim o'sish tartibida so'raladi, shuning uchun
        # bittalik kesh yetarli — RC4 ni qayta-qayta hosil qilishning oldini oladi.
        if blok not in kesh:
            kesh.clear()
            kesh[blok] = _rc4(_blok_kaliti(hf, blok), b"\x00" * _BLOK)
        return kesh[blok]

    def oraliq(a: int, b: int) -> None:
        p = a
        while p < b:
            blk, off = divmod(p, _BLOK)
            olam = min(_BLOK - off, b - p)
            out[p:p + olam] = _xor(st[p:p + olam], keystream(blk)[off:off + olam])
            p += olam

    for sanoq, (rc, a, b) in enumerate(_yozuvlar(st, filepass_end)):
        if not (sanoq & 0x3FF):
            budjet.tekshir("deshifrlash")
        if rc in _OCHIQ_YOZUVLAR:
            continue
        oraliq(a + 4 if rc == _BOUNDSHEET else a, b)
    return bytes(out)


def _chegaralarni_tekshir(st: bytes, budjet: _Budjet) -> None:
    """Tiklangan oqimga reader bilan BIR XIL varaq chegaralarini qo'llaydi.

    DIMENSIONS yozuvi (0x0200) har varaqning oxirgi qator/ustunini beradi.
    Shu bilan ta'mir yo'li orqali «juda katta varaq» kirib kelmasligiga
    kafolat bo'ladi — asl fayl uchun qanday bo'lsa, tiklangani uchun ham.
    """
    for sanoq, (rc, a, b) in enumerate(_yozuvlar(st)):
        if not (sanoq & 0xFFF):
            budjet.tekshir("chegara tekshiruvi")
        if rc != _DIMENSIONS or b - a < 12:
            continue
        rw_mac, col_mac = struct.unpack_from("<I", st, a + 4)[0], \
            struct.unpack_from("<H", st, a + 10)[0]
        if rw_mac > MAX_SHEET_ROWS:
            raise TiklashRad("tiklangan varaqda juda ko'p qator (%d)" % rw_mac)
        if col_mac > MAX_SHEET_COLS:
            raise TiklashRad("tiklangan varaqda juda ko'p ustun (%d)" % col_mac)


def _filepassni_olib_tashla(dec: bytes, fp_start: int, fp_end: int,
                            budjet: _Budjet) -> bytes:
    """FILEPASS yozuvini o'chiradi va BOUNDSHEET8 ofsetlarini tuzatadi.

    BOUNDSHEET8 (0x0085) dagi `lbPlyPos` — varaq BOF ining oqimdagi ABSOLYUT
    ofseti. FILEPASS olib tashlangach undan keyingi hamma narsa `fp_len`
    baytga suriladi; ofsetlarni tuzatmasak xlrd «Expected BOF record» beradi.
    """
    toza = bytearray(dec)
    fp_len = fp_end - fp_start
    del toza[fp_start:fp_end]
    for rc, a, b in _yozuvlar(toza):
        budjet.tekshir("BOUNDSHEET tuzatish")
        if rc == _BOUNDSHEET and b - a >= 4:
            pos = struct.unpack_from("<I", toza, a)[0]
            if pos > fp_start:
                struct.pack_into("<I", toza, a, pos - fp_len)
        elif rc == _EOF:
            break                          # globals substream tugadi
    return bytes(toza)


# ══ 4-qism: yangi CFB yasash ═══════════════════════════════════════════════

_SEK = 512
_SEK_SHIFT = 9
_FAT_YOZUV = _SEK // 4              # bitta FAT sektoriga sig'adigan yozuv soni
_DIFAT_YOZUV = _SEK // 4 - 1        # DIFAT sektorining oxirgi yozuvi — keyingisiga havola
_DIFAT_HDR = 109                    # sarlavhadagi DIFAT uyalari


def _cfb_yasa(oqimlar) -> bytes:
    """`[(nom, baytlar)]` dan CFB konteyner yasaydi.

    Sektor 512 bayt: bu MS-CFB ning eng keng tarqalgan varianti va xlrd
    boshqa o'lchamda stdout ga `@@@@ sec_size=...` ogohlantirishi chiqaradi
    (ish natijasiga ta'sir qilmaydi, lekin jurnalni ifloslaydi). 512 bayt
    bilan 10 MB li oqimga ~160 ta FAT sektori kerak, ya'ni sarlavhadagi
    109 ta uyaga sig'maydi — shuning uchun DIFAT sektorlari ham yoziladi.

    Soddalashtirish: `mini_cutoff = 0` — hamma oqim katta FAT sektorlarida
    bo'ladi, ya'ni MiniFAT umuman yozilmaydi.
    """
    def sektorlar(n):
        return (n + _SEK - 1) // _SEK

    n_yozuv = 1 + len(oqimlar)             # Root + oqimlar
    dir_bayt = ((n_yozuv + 3) // 4) * 4 * 128
    dir_sek = sektorlar(dir_bayt)
    oqim_sek = [sektorlar(len(d)) if d else 0 for _, d in oqimlar]

    # FAT o'lchami o'ziga o'zi bog'liq (FAT va DIFAT sektorlari ham FAT da
    # sanaladi), shuning uchun barqarorlashguncha iteratsiya qilamiz. Qiymatlar
    # faqat o'sadi, demak sikl albatta tugaydi; `range` esa kafolat sifatida.
    baza = sum(oqim_sek) + dir_sek
    n_fat, n_difat = 1, 0
    for _ in range(64):
        jami = baza + n_fat + n_difat
        kerak_fat = max(1, (jami + _FAT_YOZUV - 1) // _FAT_YOZUV)
        kerak_difat = max(0, (kerak_fat - _DIFAT_HDR + _DIFAT_YOZUV - 1) // _DIFAT_YOZUV)
        if kerak_fat <= n_fat and kerak_difat <= n_difat:
            break
        n_fat, n_difat = max(n_fat, kerak_fat), max(n_difat, kerak_difat)

    idx = 0
    boshlar = []
    for ss in oqim_sek:
        boshlar.append(_ENDOFCHAIN if ss == 0 else idx)
        idx += ss
    dir_start, idx = idx, idx + dir_sek
    fat_start, idx = idx, idx + n_fat
    difat_start = idx
    jami_sek = idx + n_difat

    fat = [_FREESECT] * (n_fat * _FAT_YOZUV)

    def zanjir(start, count):
        for k in range(count):
            fat[start + k] = (start + k + 1) if k < count - 1 else _ENDOFCHAIN

    off = 0
    for ss in oqim_sek:
        if ss:
            zanjir(off, ss)
            off += ss
    zanjir(dir_start, dir_sek)
    for k in range(n_fat):
        fat[fat_start + k] = _FATSECT
    for k in range(n_difat):
        fat[difat_start + k] = _DIFSECT

    YOQ = 0xFFFFFFFF

    def yozuv(nom, typ, chap, ong, bola, start, size):
        e = bytearray(128)
        nm = nom.encode("utf-16-le")
        e[0:len(nm)] = nm
        struct.pack_into("<H", e, 64, len(nm) + 2)
        e[66] = typ
        e[67] = 1                          # rang: qora
        struct.pack_into("<III", e, 68, chap, ong, bola)
        struct.pack_into("<I", e, 116, start)
        struct.pack_into("<Q", e, 120, size)
        return bytes(e)

    # Oqimlarni o'ng-qo'shni zanjiri bilan bog'laymiz: muvozanatsiz daraxt,
    # lekin MS-CFB buni taqiqlamaydi va xlrd/olefile bemalol o'qiydi.
    de = bytearray(yozuv("Root Entry", 5, YOQ, YOQ, 1, _ENDOFCHAIN, 0))
    for i, (nm, d) in enumerate(oqimlar):
        ong = (i + 2) if i < len(oqimlar) - 1 else YOQ
        de += yozuv(nm, 2, YOQ, ong, YOQ, boshlar[i], len(d))
    while len(de) < dir_sek * _SEK:
        bosh = bytearray(128)
        struct.pack_into("<III", bosh, 68, YOQ, YOQ, YOQ)
        de += bosh

    hdr = bytearray(512)
    hdr[0:8] = _OLE_IMZO
    struct.pack_into("<H", hdr, 24, 0x003E)      # minor versiya
    struct.pack_into("<H", hdr, 26, 0x0003)      # major versiya (512 baytli sektor)
    struct.pack_into("<H", hdr, 28, 0xFFFE)      # bayt tartibi
    struct.pack_into("<H", hdr, 30, _SEK_SHIFT)
    struct.pack_into("<H", hdr, 32, 6)           # mini sektor 2^6 = 64
    struct.pack_into("<I", hdr, 44, n_fat)
    struct.pack_into("<I", hdr, 48, dir_start)
    struct.pack_into("<I", hdr, 56, 0)           # mini cutoff = 0 -> MiniFAT yo'q
    struct.pack_into("<I", hdr, 60, _ENDOFCHAIN)
    struct.pack_into("<I", hdr, 64, 0)
    struct.pack_into("<I", hdr, 68, difat_start if n_difat else _ENDOFCHAIN)
    struct.pack_into("<I", hdr, 72, n_difat)
    for i in range(_DIFAT_HDR):
        struct.pack_into("<I", hdr, 76 + i * 4,
                         (fat_start + i) if i < n_fat else _FREESECT)

    tana = bytearray(jami_sek * _SEK)

    def joyla(sek, data):
        o = sek * _SEK
        tana[o:o + len(data)] = data

    off = 0
    for i, (_, d) in enumerate(oqimlar):
        if oqim_sek[i]:
            joyla(off, d)
            off += oqim_sek[i]
    joyla(dir_start, de)
    joyla(fat_start, struct.pack("<%dI" % len(fat), *fat))

    # Sarlavhaga sig'magan FAT sektor raqamlari — DIFAT sektorlarida.
    # Har sektorning OXIRGI yozuvi keyingi DIFAT sektoriga havola.
    for k in range(n_difat):
        bosh = _DIFAT_HDR + k * _DIFAT_YOZUV
        uyalar = [(fat_start + bosh + i) if bosh + i < n_fat else _FREESECT
                  for i in range(_DIFAT_YOZUV)]
        uyalar.append(difat_start + k + 1 if k < n_difat - 1 else _ENDOFCHAIN)
        joyla(difat_start + k, struct.pack("<%dI" % len(uyalar), *uyalar))
    return bytes(hdr) + bytes(tana)


# ══ 5-qism: ommaviy API ════════════════════════════════════════════════════

def shifrlangan_xatomi(exc: BaseException) -> bool:
    """Xato AYNAN «shifrlangan hujjat» haqidami.

    Chaqiruvchi shu bilan tiklashni FAQAT kerakli xatoda ishga tushiradi —
    boshqa xatolarda (buzuq zip, yo'q fayl) modul umuman chaqirilmaydi.
    """
    return "encrypted" in str(exc).lower()


def _tikla_ichki(filepath: str) -> str:
    """`tikla()` ning istisno ko'taradigan ichki varianti.

    TiklashRad / TiklashBudjeti — toza rad; boshqa istisno bo'lmasligi kerak.
    """
    budjet = _Budjet()
    hajm = os.path.getsize(filepath)
    if hajm > MAX_MB * 1024 * 1024:
        raise TiklashRad("fayl juda katta (%d MB; chegara %d MB)"
                         % (hajm // 1048576, MAX_MB))

    with open(filepath, "rb") as fh:
        data = fh.read()
    ole = _Ole2(data, budjet)
    st = ole.oqim("Workbook") or ole.oqim("Book")
    if not st:
        raise TiklashRad("OLE2 ichida `Workbook` oqimi topilmadi")

    topildi = _filepass_top(st)
    if topildi is None:
        raise TiklashRad("FILEPASS yozuvi yo'q — fayl shifrlanmagan")
    fp, fp_start, fp_end = topildi
    if not _rc4_standardmi(fp):
        # CryptoAPI/AES yoki XOR obfuskatsiyasi: bular haqiqiy parol bilan
        # himoyalangan bo'lishi mumkin — tegmaymiz.
        raise TiklashRad("shifr turi RC4 standard emas — ochilmaydi")

    salt, shifr_verifier, shifr_hash = fp[6:22], fp[22:38], fp[38:54]
    hf = None
    for parol in PAROLLAR:
        budjet.tekshir("parol tekshiruvi")
        mos, xesh = _parol_mos(parol, salt, shifr_verifier, shifr_hash)
        if mos:
            hf = xesh
            break
    if hf is None:
        # HAQIQIY parol bilan yopilgan hujjat. Parol TANLAMAYMIZ — bu maxfiy
        # hujjatni buzish bo'lardi; asl xato o'z holicha qaytadi.
        raise TiklashRad("standart parol mos kelmadi — haqiqiy parol qo'yilgan")

    dec = _deshifrla(st, hf, fp_end, budjet)
    toza = _filepassni_olib_tashla(dec, fp_start, fp_end, budjet)
    _chegaralarni_tekshir(toza, budjet)

    cfb = _cfb_yasa([("Workbook", toza)])
    if len(cfb) > MAX_MB * 1024 * 1024:
        raise TiklashRad("tiklangan fayl chegaradan katta")

    # ASL FAYLGA TEGILMAYDI — natija har doim alohida vaqtinchalik papkada.
    papka = tempfile.mkdtemp(prefix="xls_rc4_")
    nusxa = os.path.join(papka, os.path.basename(filepath) or "tiklangan.xls")
    if not nusxa.lower().endswith(".xls"):
        nusxa += ".xls"                    # `read_file` kengaytmaga qarab tarqatadi
    try:
        with open(nusxa, "wb") as fh:
            fh.write(cfb)
    except OSError:
        shutil.rmtree(papka, ignore_errors=True)
        raise
    return nusxa


def tikla(filepath: str):
    """Shifrlangan `.xls` ni ochib, VAQTINCHALIK nusxa yo'lini qaytaradi.

    Qaytadi: yangi `.xls` yo'li, yoki tiklab bo'lmasa `None`. HECH QACHON
    istisno ko'tarmaydi — chaqiruvchi `None` da ASL xatoni qayta ko'taradi,
    ya'ni ishtirokchiga ko'rinadigan izoh o'zgarmaydi.

    Natija ustida ish tugagach `tozala()` chaqirilishi kerak (yoki
    `tiklangan()` konteksti ishlatilsin).
    """
    if not _yoqilganmi():
        return None
    try:
        return _tikla_ichki(filepath)
    except TiklashRad:
        return None                        # kutilgan rad — jim o'tamiz
    except Exception as exc:               # kutilmagan: asosiy oqim yiqilmasin
        warnings.warn("RC4 tiklash ishlamadi (%s: %s): %s"
                      % (type(exc).__name__, exc, os.path.basename(filepath)))
        return None


def tozala(yol) -> None:
    """`tikla()` yasagan vaqtinchalik papkani o'chiradi (xavfsiz, jim)."""
    if yol:
        shutil.rmtree(os.path.dirname(yol), ignore_errors=True)


@contextlib.contextmanager
def tiklangan(filepath: str):
    """`with tiklangan(p) as nusxa:` — chiqishda vaqtinchalik fayl o'chadi."""
    yol = tikla(filepath)
    try:
        yield yol
    finally:
        tozala(yol)
