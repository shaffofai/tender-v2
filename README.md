# Tender-v2 — tender hujjatlarini avtomatik tekshirish

Ishtirokchi yuborgan Excel hujjatni (jamlanma, narxlar, resurs, loyiha) buyurtmachi
shabloniga solishtiradi va verdiktni tender tizimiga (shaffofxarid.uz) qaytaradi.

```
tender tizimi ──POST /api-v2/tender-v2/check──► api ──► files / templates + jobs_state
                                                              │
                                             worker — yuklab olish, shablon, tekshiruv
                                                              │
                                   files.status/comment + yuborish_navbati (BITTA tranzaksiya)
                                                              │
                          sender ──POST .../ai/set-result──► tender tizimi (verdikt)
```

| `status` | Ma'nosi |
|---|---|
| `0` | navbatda (yoki texnik xatodan keyin qayta uriniladi) |
| `1` | hujjat to'g'ri to'ldirilgan |
| `2` | kamchilik — sababi `comment` da (o'zbekcha) |
| `3` | buyurtmachi shabloni bilan AYNAN bir xil (sha256) |
| `4` | texnik, taslim — tekshirib bo'lmadi. **Rad EMAS** (`--requeue` qaytaradi) |

**OLTIN QOIDA:** texnik xato (tarmoq, shablon yo'q, fayl ochilmadi) HECH QACHON `2` bermaydi.

## Tuzilma

```
app/                    xizmat qatlami — faylni olib keladi, verdiktni saqlaydi va yetkazadi
  config.py             HAR BIR sozlama (muhit o'zgaruvchisi) — yagona manba
  log.py, download.py   loglash; himoyalangan yuklab olish + host oq ro'yxati (SSRF)
  jurnal.py             so'rov jurnali (`sorov_jurnali`): navbat + yozuvchi oqim
  templates.py          buyurtmachi shablonlari (`templates`) va etalon keshi
  db/                   qat'iy sxema, versiyalangan migratsiyalar, minimal huquqlar
  worker/               navbat, darvozalar, audit, `ishni_bajar` quvuri, sikllar, CLI
  api/                  kiruvchi API (auth, yukni tekshirish, bazaga yozish, so'rov jurnali)
  sender/               chiquvchi yuboruvchi (status xaritasi, outbox, texnik xabarlar)
  tools/                operator vositalari: korish, kuzatuv, ishga_tushir
tender_engine/          tekshiruv MANTIG'I — `app/` ni bilmaydi
  reader.py, tiklash_*  Excel o'qish + buzuq fayllarni tiklash zanjiri
  normalize.py          matn normallashtirish (ikki xil harf yig'ish — izohiga qarang)
  roles.py, structure.py  rol aniqlash; varaq juftlash / ustun moslashtirish
  rules/                har hujjat turi — bitta modul: jamlanma, narxlar, resurs, loyiha
  validate.py           `validate_one` — kirish nuqtasi
  decision.py, evidence.py  yakuniy hukm (REVIEW, shakl-erkin); verdikt dalillari
main.py, jobs_worker.py, yuboruvchi.py, api_server.py,
korish.py, kuzatuv.py, ishga_tushir.py      kirish nuqtalari (3 qatorli; kod — app/ da)
tests/                  birlik testlari + tests/e2e (butun tizim, haqiqiy PostgreSQL)
docs/                   integratsiya spetsifikatsiyasi (tarixiy)
```

## Buyruqlar

Hammasi loyiha ildizidan (konteynerda `docker compose exec worker ...`):

```bash
python main.py --davomiy -y            # worker (ishlab chiqarish sikli)
python main.py --quruq -n 20           # BAZAGA YOZMASDAN sinash
python yuboruvchi.py --davomiy         # yuboruvchi
python yuboruvchi.py --holat | --health | --qayta-och 3,4
uvicorn api_server:app --port 8000     # kiruvchi API

python -m app.db.migrate               # sxema + ilova roli (= jobs_worker.py --init-db)
python -m app.db.migrate --holat       # qaysi migratsiyalar qo'llangan

python jobs_worker.py --health [--toliq]
python jobs_worker.py --dead-letters
python jobs_worker.py --requeue <files.id|all>
python jobs_worker.py --shablonlar | --shablonlarni-tayyorla | --kesh-tozala
python korish.py [--fayllar|--natijalar|--kamchilik|--tender ID|--sql]
python kuzatuv.py [--soat 2|--fayl ID|--muammo|--xlsx]

curl -u "$JURNAL_LOGIN:$JURNAL_PAROL" "http://127.0.0.1:8000/jurnal?tur=kirish&limit=20"   # so'rov jurnali
```

## Sozlama

`.env.example` → `.env`. Har sozlama `app/config.py` da BIR MARTA o'qiladi va
tekshiriladi (noto'g'ri son — traceback emas, aniq xabar). Guruhlar dangasa:
yuboruvchining xato sozlamasi worker'ni yiqitmaydi.

## Baza

Tender-v2 ning **o'z** PostgreSQL bazasi bor (compose: `db`), platformaning
umumiy bazasidan alohida. Sxema — `app/db/migrations/` (versiyalangan, har biri
o'z tranzaksiyasida, `schema_migrations` da qayd). Qo'llangan migratsiyani
tahrirlamang: o'zgarish = yangi fayl (`0003_...sql`) + `app/db/schema.py`
dagi `KERAKLI_VERSIYA` ni oshirish (hozir 2: `0001` boshlang'ich sxema, `0002`
so'rov jurnali) + yangi jadval huquqi `app/db/huquqlar.sql` ga.

Ilova minimal huquqli `APP_DB_USER` bilan ulanadi (`app/db/huquqlar.sql`);
jadvallar egasi — migratsiya roli. Shuning uchun ilova `validation_evidence`
ning append-only triggerini o'chira olmaydi.

## So'rov jurnali

Verdikt dalili (`validation_evidence`, `jobs_validation_log`) va outbox
(`yuborish_navbati`) ilgaridan bor. Ulardan TASHQARI hamma narsa
`sorov_jurnali` jadvaliga yoziladi (`tur` ustuni):

| `tur` | Nima | Kim yozadi |
|---|---|---|
| `sorov` | API ga kelgan har so'rov: yo'l, holat kodi, davomiylik, IP, da'vo qilingan login, so'rov va javob tanasi (har biri 64 KiB gacha) | `app/api/jurnal.py` — sof ASGI oraliq qatlam |
| `kirish` | rad etilgan autentifikatsiya (401) — sababi bilan | o'sha yerdan |
| `log` | `tender` loggerining satrlari (`manba`: `api`, `worker`, `sender`); so'rov ichida yozilgani o'sha so'rovning `sorov_id` si bilan | `app/jurnal.py` — log ulagichi |
| `amal` | operator amali: `--requeue`, `--qayta-och` | buyruqning o'zi, sinxron |

**Jurnal asosiy ishni to'xtatmaydi.** Yozuvlar xotiradagi navbatga tushadi
(2 000 qator / 8 MiB gacha) va alohida oqim ularni O'Z ulanishida, to'plam bilan
yozadi — API ning yagona ulanishi va qulfiga tegilmaydi. Baza yotgan bo'lsa API
avvalgidek javob beradi; navbat to'lsa yozuv tashlanadi va soni jurnalga
yoziladi. Oraliq qatlam javobga hech narsa qo'shmaydi (sarlavha ham).
Jarayon to'xtaganda oxirgi yozuvlarga 2 soniya beriladi.
Tekshirilmagan so'rovlar (kalitsiz yoki noto'g'ri kalitli: 401, 404, 405 ...)
daqiqasiga 600 qatorgacha yoziladi — ortig'i tashlanadi va o'sha hisobga
qo'shiladi (`hodisa = jurnal_tashlandi`, `qoshimcha.chegaradan`); to'g'ri
kalitli so'rovlar va log satrlari cheklanmaydi.

**Yozilmaydi:** `GET /health` → 200 (healthcheck); `Authorization` qiymati
(faqat sxemasi); `/jurnal` javoblarining tanasi (faqat hajmi); 401 bilan
tugagan so'rovning tanasi (ishlovchi uni o'qimaydi). Kalit ko'rinishidagi
bo'laklar (`Basic ...`, `Bearer ...`, havoladagi — nisbiy havolada ham —
`token=`/imzo, `login:parol@`, `password=...`) `[yashirildi]` ga almashtiriladi.

**O'qish** — `GET /api-v2/tender-v2/jurnal` (edge orqali `/jurnal`), Basic Auth,
kalitlar `JURNAL_LOGIN` / `JURNAL_PAROL` (sherikning `KIRUVCHI_*` kalitlari
ishlamaydi; parol 24 belgidan qisqa bo'lsa — 503):

| Parametr | Ma'nosi |
|---|---|
| `dan`, `gacha` | vaqt oralig'i, ISO 8601 (`2026-09-30T10:00:00Z`; mintaqasiz qiymat — UTC). Standart: oxirgi 24 soat |
| `tur`, `manba`, `daraja` | `sorov`/`kirish`/`log`/`amal`; `api`/`worker`/`sender`; `info`/`warning`/`error` |
| `holat_kodi`, `login`, `yol` | aniq qiymat; `yol` — yo'l boshi bo'yicha |
| `sorov_id` | aniq qiymat: bitta so'rovning hamma qatorlari — `sorov`, `kirish` va so'rov davomidagi `log` satrlari |
| `limit` | 1..500 (standart 100) |
| `oldin_id` | keyingi sahifa (yangisi birinchi): javobdagi `keyingi_oldin_id` |
| `keyin_id` | kuzatish: shu `id` dan KEYIN yozilganlar, o'sish tartibida; javobdagi `keyingi_keyin_id` bilan davom etiladi |

Kuzatishda `keyingi_keyin_id` filtr hech narsa topmasa ham oldinga siljiydi —
so'rov ko'rib chiqqan eng katta `id` ga (sahifa to'la bo'lsa — oxirgi qatorga).
Ro'yxat tanalarsiz (faqat uzunligi); to'liq yozuv — `GET .../jurnal/{id}`.
Xatolar `{"xato": "..."}` (400 — noto'g'ri filtr, 404 — yozuv yo'q).

**Ma'lum chegaralar:** worker'ning har fayl bo'yicha ekranga chiqaradigan
satrlari va `--health` chiqishi `print()` — jurnalga tushmaydi; `migrate` va
faqat o'qiydigan operator vositalari qayd etilmaydi; `amal` yozuvida operator
kimligi yo'q (buyruq `docker compose exec` orqali yuriladi); `ip` — edge'ning
manzili (haqiqiy mijoz `sorov_sarlavhalari` dagi `x-forwarded-for` da, agar edge
uni yuborsa); SIGKILL bo'lsa navbatdagi (≤ 1 s) yozuvlar yo'qoladi. Ilova roli
jurnalni o'chira olmaydi — saqlash muddati `DEPLOY.md` §6 da.

## Testlar

```bash
pip install -r requirements-dev.txt
KORPUS_SHART_EMAS=1 pytest tests            # birlik testlari (bazasiz)
E2E_ADMIN_URL=postgresql://postgres:postgres@127.0.0.1:5432/postgres \
    pytest tests/e2e                        # butun tizim: API → worker → sender
```

`tests/e2e` haqiqiy PostgreSQL da (o'zining `tender_v2_e2e` bazasini yaratadi va
o'chiradi), soxta fayl serveri va soxta tender tizimi bilan 120+ qadamli
ssenariyni yuritadi va natijani (HTTP javoblari, chiqish kodlari, har bosqichdan
keyingi jadvallar, sxema va huquqlar, yuklab olishlar, yetkazilgan xabarlar)
qayd etilgan holat bilan solishtiradi. Xulq ATAYLAB o'zgarsa:
`E2E_YANGILA=1 pytest tests/e2e` va farqni commit'da tushuntiring.

Korpus regressiyasi (`test_regressiya_korpus.py`, `test_nol_siyosati.py`)
haqiqiy hujjatlar korpusini talab qiladi — u repozitoriyda YO'Q (maxfiy).
Korpussiz ular ataylab YIQILADI (darvoza jim o'chmasin); o'tkazib yuborish
faqat `KORPUS_SHART_EMAS=1` bilan.

## 2026-09-29 refaktori — nima o'zgardi

Tekshiruv mantig'i O'ZGARMAGAN. Isbot: (1) ko'chirilgan har funksiya/konstanta
sintaksis daraxti bo'yicha asl nusxa bilan aynan (tender_engine: 404 ta);
(2) nomi o'zgargan 4 ta yordamchi (harf yig'ish) har Unicode kod nuqtasida
aynan bir xil natija beradi; (3) butun tizim ssenariysi (`tests/e2e`) eski va
yangi kodda AYNAN bir xil xulq ko'rsatdi; (4) jamoa testlari bir xil natija.

**Modullar xaritasi** (tashqi skriptlar — masalan `korpus/regressiya/yurgiz.py` —
shu bo'yicha yangilanishi kerak):

| Eski | Yangi |
|---|---|
| `validator.read_file`, `ExcelTooLargeError` | `tender_engine.reader` |
| `validator._norm` / `_normk` / `_fold` / `_is_numeric_nonzero` | `tender_engine.normalize.norm` / `normk_asosiy` / `fold_asosiy` / `is_numeric_nonzero` |
| `profiles.*` (`detect_role`, `_kw`, kalit so'zlar) | `tender_engine.roles` (`_kw` — `tender_engine.normalize`) |
| `set_validator.validate_one`, `_render_comment`, `load_etalon_sheets`, `STATUS_*` | `tender_engine.validate` |
| `set_validator.validate_<rol>` | `tender_engine.rules.<rol>` |
| `set_validator._varaq_moslash`, `_lcs_col_map` | `tender_engine.structure` |
| `set_validator` yordamchilari (`_q3_tekshir`, `_Q3_SOZLAMA`, ...) | `tender_engine.rules.common` |
| `common.download`, `host_ruxsatmi`, `PermanentDownloadError` | `app.download` |
| `common.log` | `app.log` |
| `templates_db.*` | `app.templates` |
| `jobs_worker.ishni_bajar` | `app.worker.pipeline` (endi `ishni_bajar(conn, ish, token)`) |
| `jobs_worker` darvozalari (`_aynan_nusxa_res`, `_mazmun_nusxa_res`, ...) | `app.worker.gates` |
| `jobs_worker` navbati (`ish_ol`, `kashf_qil`, `texnik_qoyib_yubor`, `shablon_*`, `olik_belgilash`, `requeue`) | `app.worker.queue` |
| `jobs_worker.natijani_yoz`, `outbox_ga_qoy` | `app.worker.verdict` |
| `jobs_worker._soya_evidence`, `_texnik_iz`, `_etalon_kop_note` | `app.worker.audit` |
| `jobs_worker.health`, `_yozish_huquqi` | `app.worker.health` |
| `jobs_worker.main_loop` / `main` | `app.worker.loop` / `app.worker.cli` |
| `main.py` kodi | `app.worker.run` |
| `api_server.*` | `app.api.{auth,intake,store,app}` |
| `yuboruvchi.*` | `app.sender.{statuses,delivery,outbox,technical,cycle,cli}` |
| `jobs_worker.DATABASE_URL`, `JOBS_TABLE`, `_pk`, `_yashamayotgan`, ... | `app.db`, `app.db.schema` |

**Olib tashlandi** (hammasi ishlab chiqarishda o'lik yoki xavfli yo'l edi):

- Ish vaqtida jadval tuzilmasini aniqlash (`_jobs_ustunlarini_aniqla`, `_SXEMA`) va
  eski `jobs`/`tender` sxemalari uchun shartlar — baza endi biziniki, sxema qat'iy
  (`app/db/schema.py`). `JOBS_TABLE`, `TEMPLATES_TABLE`, `JOBS_PK`,
  `JOBS_GROUP_COL` endi sozlanmaydi (boshqa qiymat — aniq xato bilan to'xtash).
- Diskdagi etalon zaxirasi (`etalon_registry.py`, `ETALON_DISK_FALLBACK`,
  `ETALON_DIR`, `--etalonlar`) — fayl nomi bo'yicha tasodifan boshqa tender
  shabloniga mos kelib yolg'on verdikt berishi mumkin edi; `tender_id` bilan
  allaqachon yopiq edi.
- «`tender_engine` o'rnatilmagan» holatidagi zaxira yo'llari — dvigatel endi
  paketning majburiy qismi (qoidalarning o'zi unda).
- `jobs_schema.sql`, `tender_schema.sql`, `deploy/sql/01_sherik_jadvallar.sql`,
  `evidence.DDL` → `app/db/migrations/0001_boshlangich_sxema.sql` (aynan birlashma).
- `deploy/huquqlar.sql` → `app/db/huquqlar.sql` (migratsiya qo'llaydi);
  systemd o'rnatuvchisi (`deploy/ornatish.sh`, `tender-worker.service`) va zip
  qo'llanmasi (`handoff/`) — o'rniga Docker + CI rasmi.
- `/docs`, `/redoc`, `/openapi.json` standart YOPIQ (`DOCS_ENABLED=1` ochadi).

**Ataylab SAQLANDI:** API ning to'rt xil yuk shakli (sherik integratsiyasi
tayanadi), API dagi yagona baza ulanishi + qulf (ulanishlar hovuzi EMAS —
sabablari `app/api/store.py` da), ikki alohida harf yig'ish jadvali, barcha
log/xabar matnlari (disk-etalon va sxema-aniqlash satrlaridan tashqari).
