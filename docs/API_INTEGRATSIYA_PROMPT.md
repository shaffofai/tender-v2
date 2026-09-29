> **Tarixiy hujjat (2026-09-23).** API integratsiyasining spetsifikatsiyasi;
> undagi `fayl.py:qator` havolalari 2026-09-29 refaktoridan OLDINGI tuzilmaga
> ishora qiladi. Yangi joylari — `README.md` dagi modullar xaritasida.

# VAZIFA: tender tizimi bilan API integratsiyasi

**Kiruvchi API → navbat → tekshiruv → chiquvchi callback → qayta yuborish cron'i**

*Versiya 4 (2026-09-23). Buyurtmachining barcha javoblari kiritilgan.
7 mustaqil ko'rikdan o'tkazilgan (4 tasi prompt, 3 tasi `status=0` yo'li) —
30 kamchilik tuzatildi. Koddagi har bir `fayl:qator` havolasi tekshirilgan.*

> **ISHNI HOZIR BOSHLASH MUMKIN.** Har bir qaror qabul qilingan, har bir
> sozlamaga standart qiymat qo'yilgan. §13 dagi savollar **kod yozishni
> to'xtatmaydi** — ular sinov (A), deploy (B) va siyosat (C) bosqichlarida
> kerak bo'ladi. Javob kelguncha standart qiymat bilan ishlanadi.

---

## 1. MAQSAD

Tender tizimining bazaga to'g'ridan-to'g'ri kirishi **to'xtatiladi**. Baza bilan
faqat biz ishlaymiz.

```
Tender tizimi ──POST──►  Kiruvchi API  ──►  baza (files/templates)
                                              │  + jobs_state (navbat)
                                              ▼
                                      TEKSHIRUV (ishni_bajar)
                                              │
                                              ▼
                   baza: files.status + comment
                             +
                   yuborish_navbati (outbox) ← BIR TRANZAKSIYADA
                             │
                             ▼
                   CRON (3 daq) ──POST──► tender tizimi
                             │
                      200 OK │ xato → qayta urinish (max 3)
```

Texnik holat (`status=0`) ham yuboriladi, lekin **alohida yo'l bilan** —
kechikish va toshqin to'xtatgichi bilan (§7.5).

---

## 2. QABUL QILINGAN QARORLAR

| # | Savol | Qaror |
|---|---|---|
| A1 | Yetkazish holati qayerga | **Yangi ustun.** `files.send` ga TEGILMAYDI |
| A2 | Verdikt qayta yuborilishi mumkinmi | **Ha.** «Yuborilgan = muzlatilgan» BEKOR |
| A3 | `status=3` ni tender tushunadimi | ~~Ha~~ → **kodda yo'q**: validator `in:1,2` (2026-09-23). Standart `3→2` xaritasi; sherik kengaytirgach env bilan 3 ligicha. §7.2 |
| A5/A6 | Autentifikatsiya | **Basic Auth** ikki tomonda. Hozircha test URL va test login/parol |
| B1 | Takror `file_id` | `file_id` **UNIQUE** |
| B2 | `file_id` qayerda noyob | **Butun bazada** |
| B3 | `role` saqlanadimi | **Yo'q** — jadvallar o'zi ajratadi |
| B4 | `status=5` (32 000 qator) | Bizning ixtiyorimizda; yangi bazaga **ko'chirilmaydi** |
| C1 | Cron qayerda | **Alohida** jarayon/konteyner |
| C3 | Yuborish urinishlari | **Maksimum 3** |
| C4 | Xatoda qayta urinish | **Ha** (5xx/timeout) |
| D1 | Fayl manbai | **`apisitender.mc.uz`** |
| D2 | Yuk | Kuniga ~**2 000**, cho'qqi ~**200/daqiqa** |
| D3 | `type` qiymatlari | `excel1`, `excel2`, `excel3`, `loyiha_excel` |
| D4 | Shablon bog'lash | **`tender_id` + `type`** |
| E1 | O'tish | **Darhol** |
| E2 | Mavjud ma'lumot | **Yangi baza** |
| **O1** | `status=0` (texnik) tenderga yuborilsinmi | **HA** (siyosat) — lekin validator `in:1,2` 0 ni **rad etadi** (2026-09-23). Sherik kengaytirmaguncha texnik xabar **yuborilmaydi** (`TENDER_QABUL_STATUSLAR`); 0 hech qachon 2 ga o'girilmaydi. §7.2, §7.5 |
| **O2** | Chiquvchi parallellik | Ular **300** ko'taradi. Biz **20** ishlatamiz (zaxira bilan) |
| **O3** | Bitta obyekt yoki massiv | **Ikkalasi.** Loyiha tenderi — **1 fayl**, pudrat tenderi — **3 fayl** |
| **O4** | Shablon almashtirilsa | **Almashtirilmaydi.** Qayta tekshirish mantig'i **kerak emas** |
| **S1** | Muvaffaqiyat mezoni | **Faqat HTTP 200.** Javob tanasi o'qilmaydi |

---

## 3. NIMA ANIQ, NIMA YO'Q

Buyurtmachining oltita savoli 2026-09-23 da yopildi (§2). **Kod yozishni
hech narsa to'xtatmaydi.** Qolgan ochiq savollar — **§13 da**, bosqichga qarab
guruhlangan (sinovdan oldin / deploy'dan oldin / siyosat / mayda).

Eng muhim ikkitasi shu yerda ham takrorlanadi, chunki ular **texnik emas,
tashkiliy** va ko'pincha e'tibordan chetda qoladi:

- **A1 — sinov manzili.** Bizda bitta URL bor. Agar u ishlab chiqarish bo'lsa,
  sinov verdiktlari **haqiqiy ishtirokchilarga ko'rinadi**.
- **A2 — operator kim.** §7.5 dagi himoyalar (toshqin to'xtatgichi, `holat=3`)
  **to'xtab, odamni kutadi**. Operatorsiz ular ishga tushsa, texnik xabarlar
  **jimgina abadiy to'xtaydi** — ya'ni himoya himoya bo'lmay qoladi.

### O'lchangan texnik fakt — `http://` ishlamaydi

Buyurtmachi bergan chiquvchi manzil `http://api.shaffofxarid.uz/...` edi.
2026-09-23 da o'lchandi:

| Port | Natija |
|---|---|
| **443 (HTTPS)** | OCHIQ, sertifikat haqiqiy — CN `*.shaffofxarid.uz`, 2027-02-17 gacha |
| **80 (HTTP)** | **JAVOB BERMAYDI** (`TimeoutError`) |

Ya'ni bu taklif emas, **zaruriyat**: `https://` bo'lmasa integratsiya umuman
ishga tushmaydi. Batafsil — §8.1.

---

## 4. YANGI BAZA SXEMASI

### 4.1 Sherik jadvallari

```sql
create table files (
    id          bigserial primary key,
    file_id     bigint       not null,
    tender_id   bigint       not null,
    link        varchar(255) not null,   -- HAR DOIM to'liq, normallashtirilgan URL
    type        varchar(255) not null,
    status      smallint     not null default 0,   -- VERDIKT: 0/1/2/3
    comment     text,

    -- Sherik merosi. HECH KIM YOZMAYDI, har doim 0.
    -- ⚠️ OLIB TASHLAMANG: `_jobs_ustunlarini_aniqla` da
    --    `kerak = {"link","status","send","comment","type"}` (jobs_worker.py:209)
    --    va yetishmasa `SxemaXatosi` (`:211-213`) — worker UMUMAN ishga tushmaydi.
    --    Bundan tashqari `_yuborilmagan` (`:246-257`) `WHERE send = 0` shartini
    --    4 joyga qo'yadi: `:335`, `:381`, `:460`, `:1665`.
    send        smallint     not null default 0,

    -- YETKAZISH HOLATI BU YERDA EMAS — `yuborish_navbati` jadvalida (§7.1).
    -- Sabab: bir faylga ketma-ket IKKI xabar kerak bo'lishi mumkin (avval
    -- «texnik», keyin haqiqiy verdikt). Bitta ustunlar to'plami bunga
    -- sig'maydi — birinchi xabar yuborilgach ikkinchisi hech qachon
    -- yetkazilmasdi (3 mustaqil ko'rik shu xulosaga keldi).

    created_at  timestamp(0),
    updated_at  timestamp(0),
    deleted_at  timestamp(0),

    constraint files_file_id_uniq   unique (file_id),
    constraint files_status_chk     check (status in (0,1,2,3))
);

create table templates (
    id          bigserial primary key,
    file_id     bigint       not null,
    tender_id   bigint       not null,
    link        varchar(255) not null,
    type        varchar(255) not null,
    status      smallint     not null default 0,
    created_at  timestamp(0),
    updated_at  timestamp(0),
    deleted_at  timestamp(0),
    constraint templates_file_id_uniq unique (file_id)
);

create index files_navbat_idx      on files (status, send) where deleted_at is null;
create index files_tender_type_idx on files (tender_id, type);
create index templates_tender_type_idx on templates (tender_id, type, id desc);

-- `yuborish_navbati` (outbox) DDL — §7.1 da.
```

### 4.2 XIZMAT JADVALLARI — unutilmasin

Yangi bazada quyidagilar ham yaratilishi **shart**:

| Jadval | Manba | Yo'qligi nimaga olib keladi |
|---|---|---|
| `jobs_state` | `jobs_schema.sql` | Navbat umuman ishlamaydi |
| `jobs_validation_log` | `jobs_schema.sql` | Audit jurnali yo'q |
| `validation_evidence` | **faqat `jobs_worker.py --init-db`** (`_evidence.jadval_yarat`, `jobs_worker.py:1495-1498`) | ⚠️ **REVIEW→1 yo'li JIM o'chadi** |
| `yuborish_navbati` | **yangi** (§7.1) | Hech bir verdikt tenderga yetkazilmaydi |

> ⚠️ **`validation_evidence` eng xavflisi.** REVIEW→1 kafolati
> (`jobs_worker.py:1269-1297`) iz yozilmasa verdiktni **bekor qiladi** va faylni
> `texnik_qoyib_yubor` bilan `status=0` da qoldiradi; `_soya_evidence` esa
> xatoni **yutadi** (`:787-795`). Ya'ni jadval bo'lmasa xato ko'rinmaydi —
> fayllar shunchaki verdikt olmaydi va 5 urinishdan keyin o'lik bo'ladi
> (`olik_belgilash`, `:620-632`).
> `jobs_schema.sql` da bu jadval **yo'q**, `docker-compose.yml:41-42` initdb ga
> faqat `01_sherik_jadvallar.sql` + `jobs_schema.sql` ulangan.
> **Yechim:** deploy'da bir martalik `python jobs_worker.py --init-db` qadami.

### 4.3 Ikki ustunni ARALASHTIRMANG

| Ustun | Ma'nosi | Kim yozadi |
|---|---|---|
| **`status`** | **Verdikt/holat.** 0=navbatda, 1=qabul, 2=rad, 3=aynan nusxa, **4=texnik, taslim** (tekshirib bo'lmadi — rad EMAS; buyurtmachi 2026-09-23) | Worker (`natijani_yoz`); 4 ni faqat `olik_belgilash` |
| **`yuborish_navbati.holat`** | **Yetkazish.** 0=navbatda, 1=xato (qayta urinadi), 2=yuborildi, 3=yuborilmadi (3 urinish tugadi). **Boshqa jadvalda** | Yuboruvchi |

> C3 javobida «status 4 ga o'tkazish» deyilgan edi. **`files.status` ga
> yozilmaydi** — u verdikt ustuni va qiymati tenderga yuboriladi. Yetkazib
> bo'lmagan fayl «verdikt 4» bo'lsa hujjat haqidagi hukm yo'qoladi.
> Shuning uchun `yuborish_navbati.holat = 3` — `files.status` ga tegilmaydi.

---

## 5. KIRUVCHI API

### 5.1 Shartnoma

```
POST https://<HOST>/api-v2/tender-v2/check          ← HOST hali aniqlanmagan (§13 B1)
Authorization: Basic <base64(login:parol)>
Content-Type: application/json
```

Kalitlar **`.env` da**: `KIRUVCHI_LOGIN`, `KIRUVCHI_PAROL`
(namuna: `deploy/.env.api.namuna`). **Kodda yoki bu hujjatda parol yozilmaydi.**

**Sherikning HAQIQIY formati (2026-09-23 da yubordi) — ASOSIY:**

```json
[
  {"lot_type": "pudrat", "user_type": "offeror", "tender_id": 12345,
   "files": [{"file_id": 123, "link": "https://...", "type": "excel1"},
             {"file_id": 124, "link": "https://...", "type": "excel2"},
             {"file_id": 125, "link": "https://...", "type": "excel3"}]},
  {"lot_type": "loyiha", "user_type": "offeror", "tender_id": 12346,
   "files": [{"file_id": 223, "link": "https://...", "type": "loyiha_excel"}]}
]
```

Fayllar **tender bo'yicha guruhlangan**: `tender_id`, `user_type`
(`consulting`/`offeror`) va `lot_type` (`pudrat`/`loyiha`) **guruhda**, fayllar
`files` ichida (`file_id`, `link`, `type`). API har guruhni ichki fayllarga
**yoyadi** — guruh maydonlari har faylga ko'chiriladi, keyin har fayl mustaqil
tekshiriladi. `lot_type` saqlanmaydi; `type` unga mos kelmasa **faqat
ogohlantirish** (rad emas — har fayl mustaqil, 3-BOSQICH qarori). `MAX_TOPLAM`
**yoyilgan fayllar** soniga qaraydi.

Eski **tekis** shakl ham qabul qilinadi (bitta obyekt / massiv /
`{"fayllar": [...]}`, `role` yoki `user_type`) — testlar va qo'lda sinov uchun.

> ⚠️ **Nega bu muhim edi.** Prompt tekis format bilan yozilgan, sherik esa
> guruhlangan format yubordi. Moslashtirilmaganda API ularning yukini **100%**
> rad etardi (`tender_id` ichki elementda yo'q) — va C3 bo'yicha ular bizning
> `xato` javobimizni o'qimasligi mumkin edi.

| Tender turi | Fayllar | `type` qiymatlari |
|---|---|---|
| **Loyiha** | 1 ta | `loyiha_excel` |
| **Pudrat** | 3 ta | `excel1`, `excel2`, `excel3` |

> ⚠️ Bu **kutilgan** taqsimot, **tekshiriladigan qoida emas**. Kod «pudrat
> tenderida 3 ta bo'lishi shart» deb tekshirmaydi va 1 yoki 2 ta kelsa rad
> etmaydi — har fayl **mustaqil** tekshiriladi (3-BOSQICH qarori: «har fayl
> darhol, alohida — 3 fayl to'planishi kutilmaydi»).

Javob:
```json
{
  "jami": 4, "qabul": 1, "yangilandi": 1, "takror": 1, "xato": 1,
  "natijalar": [
    {"file_id": 123456, "holat": "qabul_qilindi", "id": 42},
    {"file_id": 123457, "holat": "yangilandi",    "id": 17},
    {"file_id": 123458, "holat": "takror",        "id": 18},
    {"file_id": 123459, "holat": "xato", "sabab": "link: faqat https va apisitender.mc.uz"}
  ]
}
```

`holat` **faqat to'rt qiymat**: `qabul_qilindi` | `yangilandi` | `takror` | `xato`.

| Kod | Qachon |
|---|---|
| `200` | So'rov ishlandi (elementlar ichida xato bo'lsa ham) |
| `400` | JSON buzuq yoki massiv bo'sh |
| `401` | Basic Auth yo'q/noto'g'ri |
| `413` | `MAX_TOPLAM` (500) dan ko'p element |
| `503` | Baza ishlamayapti |

**API bazaga yozgach darhol javob qaytaradi — tekshirilishini kutmaydi.**

### 5.2 Validatsiya (har element)

| Maydon | Qoida |
|---|---|
| `file_id`, `tender_id` | butun son > 0, majburiy |
| `type` | `excel1` / `excel2` / `excel3` / `loyiha_excel` |
| `user_type` (yoki `role`) | `consulting` / `offeror` |
| `lot_type` | ixtiyoriy; `pudrat` / `loyiha` — tekshirilmaydi, faqat mos kelmasa ogohlantirish |
| `link` | **`https` only**, host **`apisitender.mc.uz`**, to'liq qiymati ≤ 255 belgi |

**`link` normallashtirish — majburiy:**
Nisbiy havola (`271592/excel2/...`) qabul qilinadi, lekin bazaga **HAR DOIM
to'liq URL** yoziladi (`FILE_BASE_URL` qo'shilgan holda), va **uzunlik aynan
shu to'liq qiymatda** tekshiriladi.

> **Nega.** `common.toliq_havola` (`common.py:218-240`) yuklash paytida nisbiy
> qiymatga `FILE_BASE_URL` ni **qaytadan** qo'shadi — xom saqlansa, API
> tekshirgan manzil bilan haqiqatda yuklanadigan manzil bir xil bo'lmasligi
> mumkin (`FILE_BASE_URL` o'zgarsa allowlist butunlay chetlab o'tiladi).
> Bundan tashqari `toliq_havola` da `if os.path.isfile(h): return h`
> (`common.py:232`) — nisbiy qiymat tasodifan lokal faylga to'g'ri kelsa disk
> o'qiladi. To'liq qiymat 255 dan oshsa INSERT baza xatosi bilan yiqiladi.

### 5.3 Marshrutlash

| `role` | Qayerga | Keyin |
|---|---|---|
| `consulting` | `templates` | **`shablon_kelganini_tekshir`** (pastda) |
| `offeror` | `files` | `jobs_state` ga navbat yozuvi |

Boshqa qiymat → element `xato`, bazaga yozilmaydi.

**F1 uyg'otish — ikkita shart bor, ikkalasi ham bajarilsin:**

1. API ishga tushganda (yoki har ulanishda) **bir marta**
   `jobs_worker._jobs_ustunlarini_aniqla(conn)` chaqirilsin.
   > **Nega.** `shablon_kelganini_tekshir` ning birinchi qatori:
   > `if not _SXEMA.get("tender_id"): return 0` (`jobs_worker.py:551-552`).
   > `_SXEMA` — moduldagi global, boshlang'ich qiymati `{"aniqlandi": False}`
   > (`:159`) va uni faqat `_jobs_ustunlarini_aniqla` (`:166-235`) to'ldiradi.
   > API alohida jarayon bo'lgani uchun u yerda `_SXEMA` **hech qachon
   > to'lmaydi** → funksiya jimgina 0 qaytaradi: xato ham, log ham yo'q
   > (log faqat `n` nolmas bo'lsa, `:569-570`).

2. `shablon_kelganini_tekshir(conn)` **template INSERT COMMIT qilingandan
   KEYIN, alohida ulanishda** chaqirilsin — funksiya ichida `conn.commit()` bor
   (`jobs_worker.py:568`).

### 5.4 `jobs_state` ga navbat yozuvi

```sql
INSERT INTO jobs_state (uuid) VALUES (<files.id>::text) ON CONFLICT (uuid) DO NOTHING
```

> **INVARIANT: `jobs_state.uuid` = `files.id::text`. Boshqa hech narsa emas**
> (UUID, `file_id`, havola — **yo'q**).
> **Nega.** `kashf_qil` aynan shuni yozadi (`SELECT j.{pk}::text`,
> `jobs_worker.py:345`), `ish_ol` esa join'da `j.id = s.uuid::bigint` qiladi
> (`_kalit_join`, `:304-306`). **Bitta** raqamsiz `uuid` butun so'rovda cast
> xatosi beradi — o'sha bitta qator tufayli `ish_ol` hech kimga ish bermay
> qoladi.

### 5.5 Takrorlanish

**Takror IKKALA jadvalda qidiriladi** (`files` UNION ALL `templates`).

> **Nega.** §4.1 da ikki **mustaqil** UNIQUE bor; bir xil `file_id` ikkala
> jadvalda ham bo'lishi hech qaysi cheklovni buzmaydi — ya'ni B2 («butun bazada
> noyob») DDL bilan kafolatlanmaydi. Sherikning slot xatosi bu loyihada
> allaqachon ommaviy bo'lgan (CLAUDE.md 10-BOSQICH: 364/633 fayl noto'g'ri
> slotda).

| Holat | Natija |
|---|---|
| Topilmadi | INSERT → `qabul_qilindi` |
| Topildi, `link` **o'zgarmagan** | Hech narsa → `takror` |
| Topildi, `link` **o'zgargan** | Pastdagi to'liq yangilash → `yangilandi` |
| Topildi, lekin **boshqa jadvalda** (`role` mos emas) | `xato`. Mavjud qator **o'chirilmaydi** |
| Topildi, lekin **boshqa `tender_id` yoki `type`** | `xato` — **takror EMAS, to'qnashuv**. Jim `takror` deyilsa ikkinchi tender uchun shablon hech qachon yozilmaydi va fayllari abadiy «shablon kutilmoqda» da qoladi (e2e da topildi, 2026-09-23) |

**`link` o'zgarganda — BIR TRANZAKSIYADA uch ish:**

```sql
-- 1) fayl qatori: verdikt nollanadi
--    YETKAZISH ustunlari YO'Q — outbox'dagi eski xabarga TEGILMAYDI
--    (u allaqachon yuborilgan tarix; yangi verdikt YANGI qator qo'shadi).
UPDATE files SET
    link = :yangi_link, status = 0, comment = NULL, updated_at = now()
WHERE file_id = :file_id;

-- 2) navbat holati: TOZALANADI (aks holda qayta OLINMAYDI)
UPDATE jobs_state SET
    claimed_at = NULL, claim_token = NULL, attempts = 0,
    last_error = NULL, retry_after = NULL,
    dead_at = NULL, dead_reason = NULL, validated_at = NULL,
    updated_at = now()
WHERE uuid = :files_id::text;

-- 3) qator yo'q bo'lsa yaratiladi
INSERT INTO jobs_state (uuid) VALUES (:files_id::text) ON CONFLICT (uuid) DO NOTHING;
```

> ⚠️ **2-qadamsiz fayl HECH QACHON qayta tekshirilmaydi.**
> `ish_ol` nomzodga qat'iy shart qo'yadi (`jobs_worker.py:381-385`):
> `dead_at IS NULL AND (claimed_at IS NULL OR eskirgan) AND
> (retry_after IS NULL OR retry_after <= now()) AND attempts < 5`
> (`MAX_ATTEMPTS`, `:121`). `kashf_qil` yordam bermaydi — u **mavjud** qatorga
> tegmaydi (`ON CONFLICT (uuid) DO NOTHING`, `:347`). Ya'ni fayl ilgari o'lik
> bo'lgan (`olik_belgilash`, `:620-632`), shablon kutgan (`shablon_kutilsin`
> `retry_after = now()+600s`, `:519-526`) yoki 5 urinishni sarflagan bo'lsa —
> faqat `files.status=0` yozish uni **qaytarmaydi**.
> Tayyor namuna: **`qayta_navbat.py:103-108`** aynan shu nollashni bajaradi.

**Shablon (`consulting`) takror kelsa.** Buyurtmachi tasdiqladi: **shablon
almashtirilmaydi** (O4). Shuning uchun:

| Holat | Nima qilinadi |
|---|---|
| `link` **o'zgarmagan** | `takror` |
| `link` **o'zgargan** | **ANOMALIYA.** `templates` yangilanadi va `status=0` ga qaytariladi, **`WARNING` log'i** yoziladi. Eski fayllar **qayta navbatga QO'YILMAYDI** |

> **Nega qayta navbatga qo'yilmaydi.** Bu ataylab. Eski fayllar allaqachon
> verdikt olib tenderga yuborilgan; ularni qayta tekshirsak **yuborilgan
> verdikt o'zgaradi** — ishtirokchi «qabul» ko'rgan bo'lsa «rad» ga aylanishi
> mumkin. Buyurtmachi bu holat bo'lmaydi degani uchun avtomatik qayta tekshiruv
> **yozilmaydi**; `WARNING` esa operatorga «kutilmagan narsa bo'ldi» deb
> aytadi va qaror **odam** tomonidan qabul qilinadi (`qayta_navbat.py` bor).

> **Texnik fon.** `templates_db.tekshirilgan_deb_belgila` shablonni ko'rgach
> `status=1` qiladi va faqat `status=0` qatorga tegadi
> (`templates_db.py:392-402`) — yangilangan shablon qayta belgilanmaydi.
> Etalon keshi **havola xeshi** bilan kalitlangan (`tplh_<sha16>`,
> `templates_db.py:99-133`), ya'ni yangi link → yangi kesh, eski verdiktlar
> esa eskirgan shablonga asoslangan holicha qoladi.

**Takror javobi `200 + "takror"`, `409` emas.**
> Siz «xatolik qaytarish mumkin» degansiz. Lekin takrorning eng keng tarqalgan
> sababi — **bizning javobimiz yo'lda yo'qolgani**: tender tizimi 200 ni
> ko'rmay qaytadan yuboradi. 409 qaytarsak, ular faylni «yuborilmadi» deb
> hisoblab, boshqa yubormasligi mumkin — fayl tekshirilmay qoladi.
> Kerak bo'lsa `TAKROR_409=1` bilan yoqiladi.

**Parallel takror:** bir xil `file_id` ikki so'rovda bir vaqtda kelsa
`UNIQUE` cheklovi `IntegrityError` beradi — u **tutilib**, `takror` deb
qaytariladi (butun to'plam yiqilmaydi).

---

## 6. NAVBAT VA TEKSHIRUV

### Buzilmasligi shart

1. **`jobs_worker.ishni_bajar(conn, ish, token, registr)` AYNAN chaqiriladi**
   (`jobs_worker.py:953`). Zanjir nusxa qilinmaydi: Q1 darvoza → S1b →
   `validate_one` → `bosh_hujjat_qabulmi` → `hukm_shakl_erkin` → `hukm` →
   REVIEW izi.
2. **Har ish `jobs_state` claim'i bilan.** Claim'siz chaqirilsa `natijani_yoz`
   `rowcount == 0` bilan **jim qaytadi** va verdikt yo'qoladi
   (`jobs_worker.py:429-448`).
3. **`ish` lug'ati aynan 5 kalit:** `{uuid, link, type, bidder_id, tender_id}`
   (`jobs_worker.py:405-413`). `uuid` = `files.id::text`,
   **`bidder_id` = `files.file_id`** — bu avtomatik: guruh ustuni
   `next((c for c in ("bidder_id","file_id") if c in ust), "")` bilan
   tanlanadi (`jobs_worker.py:205`), yangi sxemada `bidder_id` yo'q, demak
   `file_id`.
   > DIQQAT: shu qiymat `jobs_validation_log.bidder_id` ustuniga ham yoziladi
   > (`natijani_yoz`, `:466-473`) — yangi sxemada u **aslida `files.file_id`**.
   > Ustun nomi tarixiy, o'zgartirilmaydi; `jobs_schema.sql` ga izoh qo'shiladi.
4. **`kashf_qil` (`:309-360`) QOLADI** — API qator yozib, navbatga qo'yolmay
   qolsa (xatolik, qayta yoqilish) shu skan uni tutadi. *Cheklovi:* u faqat
   **yangi** qatorlarni tutadi, mavjud `jobs_state` qatoriga tegmaydi (§5.5).
5. **OLTIN QOIDA:** texnik xato hech qachon `status=2` bermaydi va tenderga
   yuborilmaydi.
6. **`main.py` sikli `_sessiya` nusxasi EMAS — unda F1 uyg'otish yo'q.**
   `main.py:224-230` — navbat bo'shagan blok (`if ish is None:` → `break` yoki
   `time.sleep(jw.POLL_INTERVAL)`). `jw.shablon_kelganini_tekshir(conn)`
   **`time.sleep` dan oldin** (`:228-229` orasi) qo'yiladi.
   > **Nega.** `shablon_kelganini_tekshir` faqat `_sessiya` da chaqiriladi
   > (`jobs_worker.py:1434, 1453`); `main.py` o'z siklini yuritadi
   > (`:210-265`) va bu nomni umuman ishlatmaydi. Deploy esa aynan
   > `main.py --davomiy` ni ishlatadi. Ilgak bo'lmasa kutayotgan fayl faqat
   > `ETALON_KUTISH_INTERVAL` = 600 s o'tgach ko'riladi.

### Yuk hisobi (D2)

Cho'qqi **200/daqiqa**, bitta worker **~90/daqiqa** (1,5 fayl/s). Cho'qqida
navbat o'sadi — navbat aynan shuning uchun. Kunlik 2 000 fayl ≈ 22 daqiqalik
ish. **2 ta worker** tavsiya etiladi; `jobs_state` fencing parallel ishlashga
tayyor.

---

## 7. CHIQUVCHI YUBORISH

> **Bu bo'lim 2026-09-23 da QAYTA LOYIHALANDI.** Sabab: buyurtmachi `status=0`
> (texnik holat) ni ham yuborishga ruxsat berdi (O1), va 3 mustaqil adversarial
> ko'rik bir faylga **ikki xil xabar** kerak bo'lishini ko'rsatdi — avval
> «texnik muammo», keyin (qayta tekshirilgach) haqiqiy verdikt. `files` dagi
> bitta yetkazish slotiga bu **sig'maydi**: birinchi xabar yuborilgach
> `yuborish_holati=2` bo'ladi va keyingi verdikt **hech qachon yetkazilmaydi**.
> Shuning uchun yetkazish `files` dan **ajratildi**.

### 7.1 `yuborish_navbati` — outbox jadvali

**Bitta qator = bitta XABAR** (bitta fayl emas).

```sql
create table yuborish_navbati (
    id          bigserial primary key,
    fayl_id     bigint      not null references files(id),
    file_id     bigint      not null,          -- tenderga yuboriladigan kalit
    status      smallint    not null,          -- 0/1/2/3 — payload'dagi qiymat
    comment     text        not null,          -- payload'dagi matn, AYNAN
    sabab       varchar(64) not null,          -- 'verdikt' | 'texnik'
    holat       smallint    not null default 0,-- 0 navbatda, 1 xato, 2 yuborildi, 3 tashlandi
    urinish     smallint    not null default 0,
    band_until  timestamptz,
    yuborilgan_at timestamptz,
    xato        text,
    created_at  timestamptz not null default now(),
    constraint yn_holat_chk   check (holat in (0,1,2,3)),
    constraint yn_urinish_chk check (urinish between 0 and 3),
    constraint yn_status_chk  check (status in (0,1,2,3)),
    constraint yn_sabab_chk   check (sabab in ('verdikt','texnik'))
);

create index yn_navbat_idx on yuborish_navbati (holat, band_until, id)
    where holat in (0,1);
-- Takror yozuvni TO'SADI: bir fayl uchun bir xil (status, comment) ikki marta
-- navbatga tushmaydi, lekin YANGI verdikt (boshqa matn) bemalol qo'shiladi.
create unique index yn_takror_idx on yuborish_navbati (fayl_id, status, md5(comment));
```

**`files` dagi `yuborish_*` ustunlari OLIB TASHLANADI** (§4.1 dan chiqariladi) —
ularning vazifasini shu jadval bajaradi.

| Nima yechiladi | Qanday |
|---|---|
| Bir faylga ikki xabar | Ikki qator, ikkalasi ham yetkaziladi |
| A2 — verdikt qayta yuborilishi | Yangi verdikt = yangi qator; eskisiga tegilmaydi |
| Tartib | `ORDER BY id` — xabarlar **yozilgan tartibda** ketadi |
| **Huquqiy dalil** | Ishtirokchi **aynan nimani o'qigani** (`comment`) va **qachon** (`yuborilgan_at`) bazada qoladi |
| `qayta_navbat.py` nomuvofiqligi | U `files` ni o'zgartiradi, outbox esa o'z qatorini saqlaydi — ikkisi bir-biriga xalal bermaydi |
| `files` ustidagi raqobat | Yuboruvchi `files` ni **UPDATE qilmaydi** — faqat `yuborish_navbati` ni |

> **Nega `files` dagi ustun yetmaydi (o'lchangan emas, isbotlangan).** Ko'rikning
> uchala nigohi ham mustaqil ravishda shu xulosaga keldi. Ketma-ketlik:
> fayl o'ladi → texnik xabar ketadi → `yuborish_holati=2` → operator
> `--requeue` qiladi → fayl verdikt oladi → cron `yuborish_holati IN (0,1)`
> shartida uni **ko'rmaydi**. Yagona qutqaruvchi §7.4 dagi «qayta ochish»
> `UPDATE` si bo'lardi, u esa ilgak ichida, `try/except Exception` da —
> ya'ni jim yiqilishi mumkin.

### 7.2 Payload

```
POST https://api.shaffofxarid.uz/api/integration/ai/set-result
Authorization: Basic <base64(login:parol)>
Content-Type: application/json

{"results": [{"file_id": 123456, "status": 1, "comment": "Hujjat to'g'ri to'ldirilgan."}]}
```

**Shakl sherikning Laravel validatoridan** (2026-09-23 da ko'rsatdi):

```php
'results'           => 'required|array',
'results.*.file_id' => 'required|integer|exists:files,id',
'results.*.status'  => 'required|integer|in:1,2',
'results.*.comment' => 'required|string',
```

Oqibatlari (hammasi `yuboruvchi.py` da):
- Yuk **`{"results": [...]}`** — tekis obyekt 422 olardi. Bitta xabar = bitta
  element: massivda bittasi `exists` da yiqilsa butun to'plam 422 bo'ladi.
- **`status` faqat `1,2`** — buyurtmachi «0 va 3 ni tushunadi» degan bo'lsa ham,
  kod hozircha olmaydi. Standart shunga mos: `TENDER_STATUS_XARITA=3:2`
  (aynan nusxa → rad, `comment` sababni aytadi — 2026-09-18 gacha aynan shunday
  edi), `TENDER_QABUL_STATUSLAR=1,2` (0 yuborilmaydi, texnik qator yozilmaydi).
  Sherik `in:0,1,2,3` qilgach ikki env o'zgaradi, kod emas. **`0 → 2` xaritasi
  taqiqlangan** — texnik holat rad emas (OLTIN QOIDA), ishga tushishda FATAL.
- **`comment` bo'sh bo'lmasin** — bo'sh kelsa status bo'yicha zaxira matn
  (`IZOH_ZAXIRA`).
- **`exists:files,id`** — sinov `file_id` lari ularning bazasida **mavjud**
  bo'lishi shart, aks holda 422 → «tashlandi».

`comment` qayta formatlanmaydi, kesilmaydi.

> ⚠️ **`https://`, `http://` EMAS** — buyurtmachi bergan manzilda `http://`
> yozilgan edi, lekin 2026-09-23 da tekshirilganda **80-port javob bermadi**
> (`TimeoutError`), 443 esa ochiq va sertifikati haqiqiy (`*.shaffofxarid.uz`,
> 2027-02-17 gacha). `http://` bilan integratsiya **umuman ishlamaydi**; §8.1.

### 7.3 Navbatga QO'YISH — ikki manba

**(a) Verdikt** — `ishni_bajar` da, `natijani_yoz` muvaffaqiyatli bo'lgach.

`ishni_bajar` hozir hech narsa qaytarmaydi: barcha chiqish nuqtalari bo'sh
`return` (`jobs_worker.py:966, 975, 1034, 1043, 1297, 1303`), oxirida faqat
`finally` (`:1312-1314`). Verdikt haqidagi yagona signal — `natijani_yoz` ning
True/False qiymati (`:448, 464, 475`), u **1300-qatorda ichkarida qolib ketadi**.

Shuning uchun:

1. **`ishni_bajar` natija qaytaradigan qilinadi** — `natijani_yoz` True bo'lsa
   `{"id", "file_id", "status", "comment"}`, qolgan **hamma** yo'lda `None`.
2. **Outbox INSERT `natijani_yoz` bilan BIR TRANZAKSIYADA** — aniq joyi:
   `yozildi = natijani_yoz(...)` (`:1300`) → `if not yozildi: return`
   (`:1301-1303`) → `_soya_evidence` bloki (`:1304-1307`) → **SHU YERGA**,
   `finally` dan (`:1312`) oldin.
   > **Nega bir tranzaksiyada.** Verdikt yozilib, outbox qatori yozilmasa
   > verdikt **hech qachon yetkazilmaydi** va buni hech narsa ko'rsatmaydi.
   > `_soya_evidence` naqshidan (`:787-795`) farqli — u xatoni yutishi mumkin,
   > chunki iz yo'qolsa verdikt baribir ishlaydi. Bu yerda esa aksincha.
3. **Inline yuborish YO'Q.** Ilgak faqat **navbatga qo'yadi**; HTTP so'rovni
   **faqat cron** yuboradi.
   > **Nega olib tashlandi.** Inline yuborish ikkita muammo tug'diradi:
   > (a) sekin tender API har faylga +N soniya qo'shib, bitta worker tezligini
   > (90/daqiqa) bo'g'adi; (b) ilgak va cron bir qatorni bir vaqtda yuborishi
   > mumkin — band oynasi ilgak tomonda olinmagan edi (ko'rik topdi). Cron
   > 3 daqiqada bir ishlagani uchun kechikish sezilmaydi, kafolat esa bitta
   > joyda bo'ladi.

**(b) Texnik xabar** — alohida, kechikish bilan; §7.5.

### 7.4 Yuborish — qulflash va javob

```sql
-- 1) Nomzodlarni tanlab DARHOL band qilish (bitta bayonot).
--    DIQQAT: band shartlari TASHQI `WHERE` da ham TAKRORLANADI.
WITH nomzod AS (
    SELECT id FROM yuborish_navbati
     WHERE holat IN (0,1)
       AND urinish < :max_urinish
       AND (band_until IS NULL OR band_until < now())
     ORDER BY id
     LIMIT :toplam
     FOR UPDATE SKIP LOCKED           -- outbox BIZNIKI: jadval-darajali UPDATE bor
)
UPDATE yuborish_navbati y
   SET band_until = now() + interval '2 minutes'
  FROM nomzod n
 WHERE y.id = n.id
   AND y.holat IN (0,1)                                  -- ⚠️ TAKRORLANDI
   AND y.urinish < :max_urinish                          -- ⚠️ TAKRORLANDI
   AND (y.band_until IS NULL OR y.band_until < now())    -- ⚠️ TAKRORLANDI
RETURNING y.id, y.file_id, y.status, y.comment;
```

2. **COMMIT** — shundan keyingina HTTP (ochiq tranzaksiyada emas)
3. Yuborish
4. Natija **alohida tranzaksiyada** + `band_until = NULL`

> ⚠️ **Shartlar nega ikki marta yozilgan.** `WITH ... UPDATE ... FROM` naqshi
> o'zicha ikkilanishni **to'smaydi**. `READ COMMITTED` da PostgreSQL qulf
> kutilgandan keyin **faqat tashqi `UPDATE` ning `WHERE`** ini qayta baholaydi —
> CTE ichidagi shartlarni emas. Tashqi shart faqat `y.id = n.id` bo'lsa ikkita
> yuboruvchi **bir qatorni ikkalasi ham** `RETURNING` da oladi va ishtirokchiga
> bir xil xabar ikki marta ketadi. Uchala ko'rik nigohi ham buni mustaqil topdi.
>
> `FOR UPDATE SKIP LOCKED` bu yerda **ishlaydi**, chunki `yuborish_navbati`
> **bizning** jadvalimiz va unga jadval-darajali `UPDATE` beriladi — `files`
> da bu mumkin emas edi (§8.5).

| Javob | Natija |
|---|---|
| **HTTP 200** | `holat=2`, `yuborilgan_at=now()`, `xato=NULL` |
| **4xx** (408, 429, **401**, **403** dan tashqari) | `holat=3`, `urinish=3`. Qayta urinilmaydi |
| **401 / 403** | `holat=1` (vaqtinchalik — parol eskirgan bo'lishi mumkin) + **ogohlantirish log'i** |
| **5xx / timeout / tarmoq** | `holat=1`, `urinish += 1` |
| **3-urinish ham xato** | `holat=3` — operator ko'rigi |

> **Nega 401/403 alohida.** Ular ham 4xx, lekin sababi hujjat emas — **bizning
> kalitimiz**. «Doimiy xato» deb sanasak, parol eskirgan paytda butun navbat
> bir zumda `holat=3` bo'lib qoladi va qo'lda tiklash kerak bo'ladi.

> **Muvaffaqiyat mezoni — FAQAT HTTP 200** (buyurtmachi tasdiqladi, S1).
> Javob tanasi o'qilmaydi. Kelajakda ular tanada belgi qaytaradigan bo'lsa,
> `TENDER_JAVOB_KALITI` env qo'shish uchun **bitta joy** qoldiriladi (javobni
> baholaydigan funksiya alohida bo'lsin).

### 7.5 TEXNIK xabar (`status=4`) — eng ehtiyotkor qism

Buyurtmachi ruxsat berdi (O1) va kodini belgiladi: **`4` = texnik, taslim**
(2026-09-23; ilgari 0 edi — u endi faqat «navbatda»). Bu **eng xavfli yo'l**:
bu yerda biz ishtirokchiga **o'zimiz hukm chiqara olmaganimizni** aytamiz.
Noto'g'ri matn yoki noto'g'ri vaqt — ishtirokchini **aybdor** qilib ko'rsatadi.

`files.status = 4` ni **faqat `olik_belgilash`** qo'yadi (`dead_at IS NOT NULL
AND status = 0` → 4); `requeue`, N1 uyg'otish va API (link o'zgarsa) 0 ga
qaytaradi. Tender 4 ni olmasa (`in:1,2`) — xabar **umuman yozilmaydi**
(`TENDER_QABUL_STATUSLAR`); `4→2` xaritasi FATAL.

#### Qaysi qator yuboriladi

**Yagona belgi — `jobs_state.dead_at IS NOT NULL` (+ `files.status = 4`).**

```sql
INSERT INTO yuborish_navbati (fayl_id, file_id, status, comment, sabab)
SELECT f.id, f.file_id, 4, :matn, 'texnik'
  FROM jobs_state s
  JOIN files f ON {_kalit_join}                    -- ⚠️ qattiq cast EMAS, §7.6
 WHERE s.dead_at IS NOT NULL                       -- (1) «taslim bo'ldik»
   AND s.dead_at < now() - :kechikish              -- (2) cho'kish oynasi
   AND (s.claimed_at IS NULL
        OR s.claimed_at < now() - :lease)          -- (3) worker hamon ishlayotgan bo'lishi mumkin
   AND f.status = 4                                -- (4) texnik, taslim (verdikt YO'Q)
   AND {_yuborilmagan('f')}                        -- (5) muzlatilgan qator emas
   AND f.deleted_at IS NULL
   AND NOT EXISTS (SELECT 1 FROM yuborish_navbati y
                    WHERE y.fayl_id = f.id AND y.sabab = 'texnik'
                      AND y.created_at > s.dead_at)   -- (6) shu o'lim uchun bir marta
 ORDER BY s.dead_at
 LIMIT :toplam
ON CONFLICT DO NOTHING;
```

> **Nega `dead_at`, boshqa hech narsa emas.** `status=0` har qatorning
> **boshlang'ich** qiymati — uni o'zicha ishlatsak **hali tekshirilmagan** har
> faylni «texnik muammo» deb yuborardik. `dead_at` esa butun kodda **3 joyda**
> qo'yiladi — `texnik_qoyib_yubor(doimiy=True)` (`:488`), `shablon_kutilsin`
> 72 soatdan keyin (`:530`), `olik_belgilash` (`:623`) — va uchalasi ham
> ataylab «boshqa urinmaymiz» degani. U sxemada e'lon qilingan, indekslangan
> (`ix_js_olik`) va **vaqt belgili**. Teskari yo'nalish ham to'g'ri: `dead_at`
> ikki joyda atomik tozalanadi — `natijani_yoz` (`:433`) va `--requeue`
> (`:1532/1537`).
>
> **`attempts >= MAX_ATTEMPTS` ISHLATILMAYDI.** `ish_ol` claim paytida
> `attempts = attempts + 1` qiladi (`:392`) — ya'ni ayni paytda 5-urinishi
> ustida ishlanayotgan **sog'lom** faylda ham `attempts=5` turadi.
>
> **So'rov `jobs_state` dan boshlanadi**, `files` dan emas: `ix_js_olik`
> (`jobs_schema.sql:37-39`) o'lik qatorlarni darhol beradi, `files` dan
> boshlansa 180 000+ `status=0` qator skanerlanardi.

#### Uchta majburiy himoya (H = *himoya*; §13 dagi A/B/C belgilari bilan aloqasi yo'q)

**H1 — KECHIKISH 6 SOAT** (`YUBORISH_TEXNIK_KECHIKISH=21600`, 10 daqiqa emas).

> **Nega shuncha uzoq.** HTTP 404 faylni **bitta urinishda** o'ldiradi
> (`PermanentDownloadError` → `doimiy=True`). Saqlash tizimi yoki
> `FILE_BASE_URL` buzilsa butun navbat **bir necha daqiqada** o'ladi va
> minglab ishtirokchiga bir vaqtda xabar ketadi — **bizning sozlama xatomiz
> uchun**. 6 soat operatorga buni sezib, tuzatib, `--requeue` qilishga vaqt
> beradi (`dead_at` tozalanadi → xabar **umuman** yozilmaydi).

**H2 — TOSHQIN TO'XTATGICHI** (`TEXNIK_TOSHQIN_CHEGARA=50`).

Navbatga qo'yishdan oldin nomzodlar **sanaladi**. Soni chegaradan oshsa —
**hech narsa yozilmaydi**, `ERROR` log va `--health` da qizil.

> **Nega soni, tezligi emas.** Tezlikka qaragan to'xtatgich toshqinni
> to'xtatmaydi, faqat **kechiktiradi** (ko'rik topdi). Ommaviy o'lim deyarli
> har doim **bizning** nosozligimiz; 50 dan ortiq fayl bir vaqtda o'lgan bo'lsa,
> bu ishtirokchilar emas, **tizim** haqida xabar.

**H3 — YOZISHDAN OLDIN QAYTA TEKSHIRUV.** `sabab='texnik'` qatori yozilayotgan
lahzada shart yana bir marta baholanadi (`dead_at` hamon o'rnida, `status`
hamon 0). `--requeue` yoki kech kelgan verdikt orasida qolib ketmasin.

#### Xabar matnlari

**Uchta qat'iy qoida:**

1. **Hech qachon «rad etildi» ma'nosini bermaydi.**
2. **Hech qachon ishtirokchini aybdor qilmaydi** — xususan «faylni qaytadan
   yuklang» **deyilmaydi**.
   > **Nega.** `files.link` ni **platforma** yozadi, ishtirokchi emas —
   > o'lchangan: 194 322/194 325 havola UUID naqshida (CLAUDE.md 13-BOSQICH).
   > Ishtirokchi 404 ni tuzata **olmaydi**; undan buni so'rash — mavjud
   > bo'lmagan aybni yuklash.
3. **Bajarib bo'lmaydigan va'da berilmaydi.** «Tekshiruv qaytadan
   o'tkaziladi» faqat **avtomatik qayta urinish haqiqatan bor** bo'lsa
   yoziladi.
   > **Nega.** `dead_at` ni avtomatik tozalaydigan **hech narsa yo'q** —
   > faqat qo'lda `--requeue` (`jobs_worker.py:1528-1543`). Ko'rik: 14 ta
   > taklif qilingan matndan **9 tasi** bu va'dani berardi.

| Sinf | Matn |
|---|---|
| Havola ishlamadi (404/410), 0 bayt, Excel emas, tarmoq/5xx | «Hujjat fayli saqlash tizimidan olinmadi, shuning uchun avtomatik tekshiruv o'tkazilmadi. Bu hujjat mazmuniga berilgan baho emas va sizdan hech narsa talab qilinmaydi.» |
| Buyurtmachi shabloni yo'q / o'qilmadi / notanish shakl | «Solishtirish uchun zarur bo'lgan buyurtmachi shabloni mavjud emas yoki avtomatik o'qilmadi. Bu hujjatingizga berilgan baho emas.» |
| `TYPE_NOMALUM`, fayl juda katta, ichki xato, qolgan hammasi | «Hujjatni avtomatik tekshirish texnik sabab bilan yakunlanmadi. Bu hujjat mazmuniga berilgan baho emas va sizdan hech narsa talab qilinmaydi.» |

> **Nega atigi uchta, 14 ta emas.** Ko'rik har sinf uchun alohida matn taklif
> qilgan edi; adversarial tekshiruv esa ularning yarmida **noto'g'ri faktik
> da'vo** borligini ko'rsatdi. Ikkita aniq misol:
> - «Havoladagi fayl Excel emas (masalan PDF)» — `common.py:313-318` aynan shu
>   matnni **HTML xato sahifasi** uchun ham beradi (`_bosh_qism` izohi,
>   `common.py:244-245`, aynan shu holat uchun yozilgan). Ya'ni saqlash
>   serverining xatosi ishtirokchining «PDF yuklagani» qilib ko'rsatilardi.
> - «Buyurtmachi shabloni topilmadi» — `EtalonOqilmadi` (shablon **bor**,
>   biz **yuklab/o'qiy olmadik**) faqat log qilinadi (`jobs_worker.py:998-999`),
>   `shablon_yoq` False qoladi va oqim `:1044-1047` ga tushadi. Ya'ni **bizning**
>   nosozligimiz uchun **buyurtmachi** aybdor qilib ko'rsatilardi — uchinchi
>   tomon haqida yolg'on faktik da'vo.
>
> Sinf qanchalik yirik bo'lsa, noto'g'ri da'vo ehtimoli shuncha kichik. Aniq
> sabab **bizda qoladi**: `yuborish_navbati.comment` da ishtirokchi ko'rgan
> matn, `jobs_state.dead_reason` va `validation_evidence` da xom sabab.

### 7.6 Ikkita mavjud NUQSON — bu ish doirasida tuzatiladi

Ko'rik jonli koddagi ikki nuqsonni ochdi. Ikkalasi ham API dan **oldin** ham
bor edi, lekin texnik xabar ularni **ko'rinadigan** qiladi — ya'ni yolg'on
xabar sifatida ishtirokchiga yetib boradi.

**N1 — F1 da o'lgan fayl shablon kelsa ham UYG'ONMAYDI.**

`shablon_kutilsin` o'lik qilganda ikkala maydonni ham o'zgartiradi:
`dead_at = now()` **va** `retry_after = NULL` (`jobs_worker.py:530-531`).
`shablon_kelganini_tekshir` esa aynan ularning **teskarisini** talab qiladi:
`s.dead_at IS NULL` (`:560`) **va** `s.retry_after IS NOT NULL AND
s.retry_after > now()` (`:562`).

Natija: 72 soat kutib o'lgan fayl, shablon kelganda ham, **abadiy** verdiktsiz
qoladi. **Tuzatish:** `shablon_kelganini_tekshir` ga ikkinchi `UPDATE` —
`dead_at IS NOT NULL AND dead_reason LIKE 'ETALON_KUTILMOQDA%'` bo'lgan
qatorlarni `dead_at=NULL, dead_reason=NULL, attempts=0, retry_after=now()`
bilan tiriltiradi (shablon **haqiqatan** kelgani `EXISTS` bilan tekshirilgan
holda).

**N2 — `main.py --davomiy` navbat bo'sh bo'lganda `olik_belgilash` ni
chaqirmaydi.**

`olik_belgilash` ikki joyda chaqiriladi: `main.py:200` (sikldan **oldin** bir
marta) va `main.py:262` (har **ishlangan** fayldan keyin). Navbat bo'shaganda
esa `:224-230` bloki `:230` da `continue` qiladi va `:262` ga **yetib
bormaydi**. Ya'ni lease'i tugagan, `attempts>=5` qatorlar `dead_at` siz
muallaq qoladi — na texnik xabar ketadi, na `--dead-letters` da ko'rinadi.

**Tuzatish:** `main.py:228-229` (sleep dan oldin) ga `jw.olik_belgilash(conn)`
— o'sha yerga `jw.shablon_kelganini_tekshir(conn)` ham qo'shiladi
(§6 ning 6-bandi).

### 7.7 Sozlamalar

| Env | Standart | Izoh |
|---|---|---|
| `TENDER_API_URL` | — | `https://api.shaffofxarid.uz/api/integration/ai/set-result` |
| `TENDER_API_LOGIN` / `TENDER_API_PAROL` | — | Basic Auth (`.env` da, kodda emas) |
| `TENDER_TIMEOUT` | 30 | bitta so'rov timeouti |
| `YUBORISH_OQIM` | **20** | parallellik. Ular **300** ko'taradi (O2) — 15 barobar zaxira ataylab: 2 000 fayl/kun uchun 20 ham ortiqcha, chegaraga yaqinlashish esa 429/503 xavfini tug'diradi |
| `YUBORISH_TOPLAM` | 100 | bitta cron aylanishida |
| `YUBORISH_MAX_URINISH` | 3 | ⚠️ `yn_urinish_chk` (0..3) dan OSHMASIN |
| `CRON_INTERVAL` | 180 | soniya = 3 daqiqa |
| **`YUBORISH_TEXNIK_KECHIKISH`** | **21600** | 6 soat — H1 |
| **`YUBORISH_TEXNIK_LEASE`** | 1800 | 30 daqiqa — (3)-shart |
| **`TEXNIK_TOSHQIN_CHEGARA`** | **50** | H2 to'xtatgichi |
| **`TEXNIK_YUBORISH`** | **1** | `0` — texnik xabarlar butunlay o'chiriladi (favqulodda kalit) |
| `KIRUVCHI_LOGIN` / `KIRUVCHI_PAROL` | — | Kiruvchi API |
| `MAX_TOPLAM` | 500 | so'rovdagi element chegarasi |
| `RUXSAT_HOSTLAR` | `apisitender.mc.uz` | allowlist |

### 7.8 Ikkita qattiq yozilmasligi kerak bo'lgan narsa

1. **`s.uuid::bigint` qattiq yozilmaydi** — `jobs_worker._kalit_join('f','s')`
   ishlatiladi (`:304-306`). U sxemaga qarab yo'nalishni **o'zi** tanlaydi;
   qattiq cast bo'lsa `jobs_state` dagi **bitta** raqamsiz `uuid` butun texnik
   cron'ni istisno bilan yiqitadi — verdikt navbati esa ishlayverib, nosozlik
   yarim ko'rinmas bo'ladi.
2. **`f.send = 0` qattiq yozilmaydi** — `jobs_worker._yuborilmagan('f')`
   (`:246-257`). `send` boolean bo'lgan sxemada (mahalliy sinov bazasi) qattiq
   `= 0` SQL tip xatosi beradi.

---

## 8. XAVFSIZLIK

### 8.1 HTTPS — muzokara mavzusi emas (§13 B4)

**Ikkala yo'nalishda ham `https://` majburiy.** Basic Auth parolni **base64**
bilan o'raydi — bu kodlash, **shifr emas**: yo'ldagi har kim ochib o'qiy oladi.
HTTP orqali yuborilsa parol ochiq ketadi.

Chiquvchi manzil uchun bu nazariy emas — **o'lchandi (2026-09-23):**

| Port | Natija |
|---|---|
| **443 (HTTPS)** | OCHIQ, sertifikat haqiqiy — CN `*.shaffofxarid.uz`, 2027-02-17 gacha |
| **80 (HTTP)** | **JAVOB BERMAYDI** — `TimeoutError` |

Ya'ni buyurtmachi bergan `http://api.shaffofxarid.uz/...` manzili bilan
integratsiya **umuman ishga tushmaydi**. `TENDER_API_URL` `https://` bilan
yoziladi.

**Kodda himoya:** `TENDER_API_URL` `https://` bilan boshlanmasa — ishga
tushishda **FATAL** (`sys.exit`), so'rov yuborilmaydi. Sabab: bu sozlama xatosi,
uni jimgina «ishlatib ketish» parolni ochiq yuborish demakdir.
`TLS_TEKSHIRUVSIZ=1` kabi chetlab o'tish kaliti **qo'shilmaydi**.

Kiruvchi API ham `https://` da turadi — to'g'ridan-to'g'ri yoki TLS ni
tugatadigan reverse proxy (nginx/traefik) orqali. Ochiq HTTP portda
tinglanmaydi.

### 8.2 Parollar (§13 C4)

- **Faqat `.env` da** — kodda, log'da, javob tanasida, `git` da yo'q.
  Namuna: `deploy/.env.api.namuna` (faqat o'zgaruvchi nomlari, qiymatsiz).
- **Paketga tushmasin** — `paket_yasa.tekshir` va `zip_sir_tekshir.py` bilan
  tekshiriladi. `.dockerignore` `.env` ni bloklaydi, qiymatlar **muhit
  o'zgaruvchisi** orqali beriladi (15-BOSQICHdagi nuqson #1 shu edi).
- **Xatolik matnida URL bosilmasin** — Basic Auth kaliti ba'zan URL ichiga
  tushib qoladi; `yuborish_navbati.xato` ga yoziladigan matn **filtrlansin**.
- ⚠️ **Chiquvchi parol almashtirilsin.** Hozirgi qiymat ishlab chiqish
  yozishmasida ochiq uzatilgan. Integratsiya ishga tushgach sherikdan **yangi
  parol** so'raladi; eski qiymat hech qayerda saqlanmasin.
### 8.3 `link` allowlist — IKKI joyda

- (a) API dagi kiruvchi `link` (normallashtirilgandan keyin)
- (b) **yuklab olishda — har yo'naltirishdan keyingi yakuniy URL hosti**
   > **Nega (b) shart.** `common.download` httpx mijozini
   > `follow_redirects=True` bilan ochadi (`common.py:282`). Ya'ni
   > `apisitender.mc.uz` 302 bilan ichki manzilga (`169.254.169.254`,
   > `10.x.x.x`) yuborsa worker uni **so'zsiz** oladi. API darajasidagi
   > tekshiruv buni tutmaydi.
### 8.4 Yuk cheklovi

Bitta so'rovda ≤ `MAX_TOPLAM` (500) element; kerak bo'lsa IP bo'yicha rate limit.

### 8.5 Baza huquqlari

`deploy/huquqlar.sql` yangilanadi:
- `files`, `templates` ga **INSERT**, sequence'ga **USAGE** (kiruvchi API)
- **`yuborish_navbati` ga `SELECT, INSERT, UPDATE`** + sequence'ga `USAGE`
  (bizning jadvalimiz — jadval-darajali, ustun-darajali emas)
- `huquqlar.sql:120-121` tekshiruvi (`files INSERT` yo'qligini kutadi)
  yangilanadi
- `jobs_worker._yozish_huquqi` (`:1546-1600`) `yuborish_navbati` ni ham
  tekshirsin — huquq yo'q bo'lsa **hech bir verdikt yetkazilmaydi** va buni
  hech narsa ko'rsatmaydi

> ⚠️ **`files` da `FOR UPDATE` ustun-darajali GRANT bilan ishlamaydi.**
> Qator qulflash **jadval-darajali UPDATE** huquqini talab qiladi;
> `huquqlar.sql:51` esa faqat `GRANT UPDATE (status, comment, updated_at)`
> beradi va tekshiruv ham ustun-darajali (`has_column_privilege`, `:105`).
> Mavjud kod aynan shuning uchun `files` ni emas, faqat `jobs_state` ni
> qulflaydi (`FOR UPDATE OF s`, `:388`; izoh `:73-74`).
> **Shuning uchun `files` HECH QACHON qulflanmaydi.** Chiquvchi navbat
> `yuborish_navbati` da (§7.4) — u **bizning** jadvalimiz, unga jadval-darajali
> `UPDATE` beriladi va `FOR UPDATE SKIP LOCKED` bemalol ishlaydi.

---

## 9. QAMROVGA KIRMAYDI

- **Verdikt mantig'i** (`tender_engine/`, `set_validator.py`, `decision.py`)
- **`ikki_darvoza_server/`** ustiga qurilmaydi — alohida, soddalashtirilgan
  dvigatel (Q1 / Q1+S1B)
- **`test_ui.py`** namuna emas
- **Eski 194 325 qator** ko'chirilmaydi (E2)

---

## 10. DEPLOY

| Xizmat | Nima | Ishga tushirish |
|---|---|---|
| `api` | Kiruvchi API (FastAPI + uvicorn) | `uvicorn api_server:app` |
| `worker` | Tekshiruv — **faqat yuborish ilgagi qo'shiladi** (§7.2); verdikt mantig'i tegilmaydi | `python main.py --davomiy -y` |
| `yuboruvchi` | Cron (3 daqiqa) | `python yuboruvchi.py --davomiy` |

**Bir martalik qadam:** `python jobs_worker.py --init-db` — `validation_evidence`
faqat shu yerda yaratiladi (§4.2).

**Manifest 5 joyda sinxron** (aks holda konteyner `ModuleNotFoundError` bilan
yiqiladi — bu loyihada allaqachon bo'lgan):
1. `requirements-prod.txt` — FastAPI + uvicorn qo'shiladi
2. `Dockerfile` COPY (`:31-45`)
3. **`Dockerfile:49`** — build-vaqt smoke-test:
   `RUN python -c "import main, jobs_worker, korish, ishga_tushir, templates_db"`
   ga `api_server`, `yuboruvchi` qo'shiladi
   > **Nega alohida.** Manifest testi fayl nomini Dockerfile **matni** ichidan
   > qidiradi (`tests/test_xavfsizlik.py:639-642`) — faqat COPY ga qo'shilsa
   > test yashil bo'ladi, lekin smoke-test yangi modulni import qilmaydi va
   > `ModuleNotFoundError` build'da emas, **ishlab chiqarishda** chiqadi.
4. `deploy/ornatish.sh`
5. `tests/test_xavfsizlik.py` PROD_FAYLLAR (`:38-46`) — **yagona haqiqat manbai**

Har xizmatga `/health` yoki `--health`. `docker-compose.yml` ga `api` va
`yuboruvchi` qo'shiladi.

---

## 11. QABUL MEZONLARI

**Kiruvchi API**
1. `consulting` → `templates` da 1 qator, 200
2. `offeror` → `files` + `jobs_state` da 1 qatordan, 200
3. **Idempotentlik:** bir xil `file_id` 3 marta → 1 qator, 1 marta tekshiriladi,
   tenderga 1 marta javob
4. **`link` o'zgarib takror kelsa** → qator yangilanadi, `jobs_state` tozalanadi,
   fayl **qayta tekshiriladi** va tenderga **qayta yuboriladi**
5. Bir xil `file_id` boshqa `role` bilan kelsa → `xato`, mavjud qator saqlanadi
6. Noto'g'ri `role`/`type`/`link` → o'sha element `xato`, **qolganlari ishlanadi**
7. Auth yo'q/noto'g'ri → 401, bazaga yozilmaydi
8. `file://`, `http://`, IP-manzil, begona host → `xato`
9. 500 elementli so'rov ishlaydi; 501 → 413
10. Parallel bir xil `file_id` → `IntegrityError` tutiladi, `takror` qaytadi

**Navbat va tekshiruv**
11. **F1:** `offeror` (shablonsiz) → `status=0`,
    `jobs_state.last_error LIKE 'ETALON_KUTILMOQDA%'`. Keyin `consulting`
    kelgach fayl **avtomatik** tekshiriladi — va `shablon_kelganini_tekshir`
    **qaytgan soni > 0** bo'lishi bilan isbotlanadi (0 qaytsa ilgak ishlamagan)
12. **Zanjir:** bayt-teng → `status=3`; to'ldirilgan → 1; bo'sh → 2
13. **OLTIN QOIDA:** havolasi 500 qaytaradigan fayl → `status` 0, `comment`
    NULL, `jobs_state.dead_at` **qo'yilmaguncha** tenderga hech narsa
    yuborilmaydi; o'lgach ham **`YUBORISH_TEXNIK_KECHIKISH` (6 soat) o'tmaguncha**
    outbox'ga qator **yozilmaydi**
14. **Parallellik:** 2 worker + API bir vaqtda 200 qator → har fayl aynan
    1 marta (`jobs_validation_log` da 200 yozuv)
15. `validation_evidence` jadvali **yo'q** qilinsa — fayllar verdikt olmaydi va
    bu **ko'rinadi** (jim o'tmaydi)

**Yuborish**
16. Mock 200 → `holat=2`, payload aynan `{file_id, status, comment}`
17. Mock 500 → `1`, `urinish=1`; cron 3 daqiqadan keyin qayta yuboradi
18. 3 marta 500 → `3`, boshqa urinilmaydi
19. Mock 400 → darhol `3`; mock **401** → `1` (doimiy emas) + ogohlantirish
20. **Atomik band qilish:** ikkita yuboruvchi jarayon bir vaqtda ishlasa
    — mock'da har xabar **aynan 1 marta**. Test tashqi `WHERE` dagi
    takrorlangan shartlar **olib tashlanganda QIZIL** bersin (§7.4)
21. Verdikt yozilib, outbox qatori yozilmasa — **ikkalasi ham** qaytariladi
    (bitta tranzaksiya); `natijani_yoz` dan keyin sun'iy xato qo'yib sinaladi
22. Worker verdikt yozib to'xtasa → cron keyin topadi va yuboradi
23. **Ikki xabar ketma-ketligi:** fayl o'ladi → (6 soat) texnik xabar →
    `--requeue` → verdikt → **ikkinchi** xabar ham yetkaziladi, tartib saqlanadi

**Texnik xabar (`status=0`)**
24. Hali tekshirilmagan fayl (`dead_at IS NULL`) → outbox'ga **tushmaydi**
25. `status5_to_0.py` kabi vosita `files.status` ni 0 qilib, `jobs_state` ni
    tozalamasa — eski `dead_at` tufayli **yolg'on** texnik xabar chiqmasin:
    H3 (yozishdan oldin qayta tekshiruv) buni tutsin
26. **Toshqin:** 51 ta fayl bir vaqtda o'lsa — **hech narsa yozilmaydi**,
    `ERROR` log va `--health` qizil (H2)
27. **Kechikish:** endigina o'lgan fayl uchun outbox **bo'sh**; 6 soatdan
    keyin bitta qator paydo bo'ladi (H1)
28. `--requeue` kechikish oynasi ichida bajarilsa — texnik xabar **umuman**
    yozilmaydi
29. **N1:** F1 da o'lgan fayl uchun shablon kelsa — qator tiriladi
    (`dead_at=NULL`) va tekshiriladi
30. **N2:** navbat bo'sh `--davomiy` rejimda lease'i tugagan qator
    `olik_belgilash` bilan o'lik deb belgilansin
31. Matnlarda **«qaytadan yuklang»** va **«rad»** so'zlari yo'qligi
    avtomatik tekshirilsin (matn ro'yxati bo'yicha testda qotirilgan)

**Regressiya va butunlik**
24. `pytest tests/` to'liq yashil — `test_xavfsizlik.py` (manifest 5 joy) va
    `test_regressiya_korpus.py` (**B da regressiya 0**)
25. `python tekshiruv.py --fayl` → 27/27
26. `jobs_worker.py --health` — yangi huquqlar yetishmasa **qizil**
27. `docker compose up -d` → uchala xizmat `healthy`; uchdan-uchiga:
    1 `consulting` + 2 `offeror` → 2 verdikt + mock tenderda 2 so'rov

---

## 12. ISH TARTIBI

Har bosqichdan keyin `pytest tests/`.

1. **Sxema** — `deploy/sql/01_sherik_jadvallar.sql` yangilanadi (sherik
   jadvallari) + `jobs_schema.sql` (xizmat jadvallari) + `--init-db`
   (`validation_evidence`).
   > Yangi bazada **migratsiya yo'q** — DDL bir marta bajariladi.
   > ROLLBACK-sinov naqshi faqat ma'lumot bor bazaga ustun/CHECK qo'shilganda
   > kerak.
2. **Kiruvchi API** — auth, validatsiya (link normallashtirish bilan),
   marshrutlash, ikki jadvalli takror tekshiruvi, `_jobs_ustunlarini_aniqla`
   + F1 uyg'otish. Testlar bazasiz (mock ulanish).
3. **Navbatga ulash** — `jobs_state` INSERT, link-o'zgarish yo'lida tozalash,
   `kashf_qil` zaxirasi.
4. **Outbox + verdikt ilgagi** — `yuborish_navbati` DDL; `ishni_bajar`
   natija qaytaradigan qilinadi (`jobs_worker.py:1300`), outbox INSERT
   `natijani_yoz` bilan **bir tranzaksiyada**; ikkala siklga ilgak
   (`main.py:244`, `_sessiya:1461`).
5. **N1 + N2 tuzatishlari** (§7.6) — mustaqil, kichik, o'z testlari bilan.
   `main.py:224-230` ga `olik_belgilash` + `shablon_kelganini_tekshir`.
6. **`yuboruvchi.py`** — §7.4 qulfi (tashqi `WHERE` takrorlangan holda),
   4xx/5xx/401 farqi. Mock tender bilan test (`mock_server.py` naqshi).
7. **Texnik xabar yo'li** (§7.5) — H1/H2/H3 himoyalari bilan; `TEXNIK_YUBORISH=0`
   bilan butunlay o'chirilishi sinaladi.
8. **Cron** — alohida jarayon.
9. **Docker/compose + manifest 5 joy.**
10. **Uchdan-uchiga sinov.**

**Uslub:** izoh va nomlar **o'zbekcha**, mavjud kodga mos (`jobs_worker.py`).
Har nozik qaror yoniga **nega** shunday qilingani yoziladi. Chegara/sozlama
qiymati qo'yilsa — **qayerdan olingani** izohda ko'rsatiladi.

---

## 13. OCHIQ SAVOLLAR VA KERAKLI NARSALAR

Buyurtmachining oltita savoli 2026-09-23 da yopildi (§2). Quyidagilar
**qolganlari** — hech biri kod yozishni to'xtatmaydi, lekin har biri ma'lum
bosqichda kerak bo'ladi.

### A. To'xtatadi — birinchi SINOVDAN oldin

| # | Nima | Nega to'xtatadi |
|---|---|---|
| **A1** | **Sinov manzili yoki prodda sinashga aniq ruxsat** | Bizda bitta URL bor (`api.shaffofxarid.uz`). Agar u ishlab chiqarish bo'lsa, sinov verdiktlari **haqiqiy ishtirokchilarga ko'rinadi**. Kerak: alohida test instansi, yoki «shu `file_id` lar bilan sinash mumkin» degan ro'yxat |
| **A2** | **Operator kim** | Qo'yilgan himoyalarning hammasi **to'xtab, odamni kutadi**: H2 toshqin to'xtatgichi, `holat=3` (3 urinish tugadi), `--requeue`, `--health` qizil. Operatorsiz H2 ishga tushsa texnik xabarlar **jimgina abadiy to'xtaydi**. Kerak: javobgar shaxs/jamoa, xabar kanali (email/Telegram), ko'rish davriyligi |

### B. To'xtatadi — DEPLOY dan oldin

| # | Nima | Izoh |
|---|---|---|
| **B1** (eski H1) | **Kiruvchi API hosti** — domen/IP + port | Yo'l (`/api-v2/tender-v2/check`) va kalitlar tayyor, faqat host yetishmaydi |
| **B2** | **TLS sertifikati** shu hostga | To'g'ridan-to'g'ri yoki reverse proxy (nginx/traefik) orqali; §8.1 |
| **B3** | **Xizmat qayerda ishlaydi** — sherik serveri / bizniki / bulut | B1 shundan kelib chiqadi; bazaga tarmoq kirishi va kim restart qila olishi ham |
| **B4** (eski X1) | `TENDER_API_URL` ni **`https://`** ga o'tkazish tasdig'i | `http://` da 80-port javob bermaydi — o'lchangan; §8.1 |
| **B5** | **O'tish lahzasi va orqaga qaytish rejasi** | «Darhol» (E1) deyilgan, lekin aniq vaqt, kim boshqaradi va nosozlikda nima qilish yozilmagan |

### C. Siyosat qarorlari — standart qiymat bor, lekin SIZNIKI

| # | Savol | Hozirgi standart |
|---|---|---|
| **C1** | **Texnik xabar (`status=0`) matnini ISHTIROKCHI ko'radimi**, yoki faqat tender operatorigami? | Ishtirokchi ko'radi deb faraz qilingan — shuning uchun matnlar ayblovsiz va §7.5 himoyalari qat'iy |
| **C2** | **Texnik xabar kechikishi** | `YUBORISH_TEXNIK_KECHIKISH=21600` (6 soat). ⚠️ **C1 ga bog'liq:** faqat operator ko'rsa 30 daqiqa yetadi; ishtirokchi ko'rsa 6 soat — himoya |
| **C3** | **Tender bizning javobimizdagi `natijalar` massivini o'qiydimi?** | O'qimaydi deb faraz qilinadi — har rad etilgan element uchun `ERROR` log + `--health` hisoblagichi (§5.1). Ular o'qisa, xato darhol ko'rinadi |
| **C4** (eski X2) | **Chiquvchi parolni almashtirish** | Hozirgi qiymat yozishmada ochiq uzatilgan; §8.2 |

### D. Mayda — standart bilan ketaveradi

| # | Savol | Qabul qilingan |
|---|---|---|
| **D1** | «300 ta» — daqiqasigami yoki bir vaqtdagi ulanishmi? | `YUBORISH_OQIM=20` — ikkala talqinda ham xavfsiz |
| **D2** | Takror `file_id` da `200 + takror` mi, `409` mi? | `200 + takror` (§5.5 da asoslangan); `TAKROR_409=1` bilan o'zgaradi |
| **D3** | Bir so'rovdagi element chegarasi | `MAX_TOPLAM=500`; kutilgan hajm 1 yoki 3 |

### Sherikka beriladigan narsalar

`KIRUVCHI_LOGIN` / `KIRUVCHI_PAROL` — `.env` da, **xavfsiz kanal orqali**
(parol menejeri yoki shifrlangan kanal). Yozishmada yubormang — C4 aynan
shundan kelib chiqdi.
