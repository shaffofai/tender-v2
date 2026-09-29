# DEPLOY — tender-v2 ni serverga qo'yish

Loyihaning mazmuni va tuzilmasi — `README.md`. Bu hujjat **server** uchun.

## 1. Nima ishlaydi

```
tender tizimi ──POST /api-v2/tender-v2/check──► api ──► files/templates + jobs_state
                                                          │
                                        worker — tekshiruv (main.py --davomiy -y)
                                                          │
                              files.status + yuborish_navbati (outbox) — BITTA tranzaksiya
                                                          │
                          sender ──POST .../ai/set-result──► tender tizimi (verdikt)
```

Compose loyihasi `shaffofai-v2-tender-v2`, beshta xizmat:

| Xizmat | Konteyner | Vazifa |
|---|---|---|
| `db` | `shaffofai-v2-tender-v2-db` | PostgreSQL 17 — tender-v2 ning **o'z** bazasi. Faqat ichki tarmoqda, porti ochilmaydi |
| `migrate` | `shaffofai-v2-tender-v2-migrate` | Bir martalik: sxema migratsiyalari, ilova roli va huquqlari. Har `up` da idempotent yuradi |
| `api` | `shaffofai-v2-tender-v2-api` | Kiruvchi API (Basic Auth). Platforma tarmog'ida `tender-v2:8000` |
| `worker` | `shaffofai-v2-tender-v2-worker` | Tekshiruv: yuklab olish, shablon, verdikt |
| `sender` | `shaffofai-v2-tender-v2-sender` | Verdiktlarni tender tizimiga yuboradi |

Nomlar serverdagi eski `~/tender_deploy` konteynerlari (`tender-postgres`,
`tender-ai`, `tender-api`, `tender-yuboruvchi`) bilan to'qnashmaydi.

**Kafolatlar** (kod o'zgarmagan — 2026-09-29 refaktori faqat tuzilmani o'zgartirdi):

- Texnik nosozlik HECH QACHON `status = 2` (rad) bermaydi; urinishlar tugasa `4`
  (texnik, taslim — `--requeue` qaytaradi), tenderga `4` faqat 6 soatdan keyin ketadi.
- Verdikt va chiquvchi xabar bitta tranzaksiyada — biri yozilib, ikkinchisi
  yozilmay qolishi mumkin emas. Yetkazilgan deb FAQAT HTTP 200 sanaladi.
- Ilova bazaga minimal huquqli `tender_ai` roli bilan ulanadi; jadvallar egasi
  (`POSTGRES_USER`) faqat `migrate` da. `validation_evidence` append-only
  (trigger) — ilova uni o'chira olmaydi.

## 2. Talablar

| | |
|---|---|
| Docker | 24+ va `docker compose` v2 |
| Platforma | gateway stack ishlab turgan bo'lsin — `shaffofai-v2` tarmog'ini u yaratadi |
| Tarmoq | chiquvchi: `apisitender.mc.uz` (fayllar), `api.shaffofxarid.uz` (verdiktlar) |
| Disk | ~3 GB (etalon keshi 2 GB gacha) + baza |
| RAM | worker chegarasi 4 GB (odatda 200–400 MB), api 512 MB, sender 256 MB |

## 3. Birinchi o'rnatish

```bash
cd ~ && git clone https://github.com/shaffofai/tender-v2.git shaffofai-tender-v2
cd ~/shaffofai-tender-v2
cp .env.example .env && chmod 600 .env
nano .env        # POSTGRES_PASSWORD, APP_DB_PASSWORD, KIRUVCHI_*, TENDER_API_*,
                 # EDGE_BIND=192.168.100.60   (parollar: openssl rand -hex 24)

# O'tish davri: router/edge hali 192.168.100.60:8084 ni kutadi → edge fayli bilan
docker compose -f docker-compose.yml -f docker-compose.edge.yml pull
docker compose -f docker-compose.yml -f docker-compose.edge.yml up -d
docker compose logs migrate          # «0001_boshlangich_sxema qo'llandi»
docker compose ps                    # db, api, worker, sender — healthy
```

Qulaylik uchun (faqat shu papkada):

```bash
export COMPOSE_FILE=docker-compose.yml:docker-compose.edge.yml
```

Keyin oddiy `docker compose ...` ikkala faylni ham oladi.

> ⚠️ **`docker compose down -v` HECH QACHON** — baza volume'i o'chadi.
> ⚠️ **`iptables -F` HECH QACHON** — Docker'ning FORWARD qoidalari o'chadi va
> serverdagi BARCHA konteynerlar tashqaridan ko'rinmay qoladi (2026-09-28).

Tekshiruv — tashqaridan:

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://ai.shq.uz/api-v2/tender-v2/check   # 401 (auth kerak)
```

> Kompaniya edge'i hozir `/api-v2/tender-v2/` prefiksini KESIB 8084 ga
> yuboradi — ilova `/check` ni oladi va 404 qaytaradi. DevOps prefiksni
> saqlasin (yoki bu qoidani olib tashlasin — so'rovlar gateway router orqali
> keladi, u yo'lni to'liq uzatadi).

## 4. Eski `tender_deploy` dan ko'chish (ma'lumot bilan)

Eski stek (`~/tender_deploy`, `tender-postgres`) ma'lumotini yangi bazaga
ko'chirish. Ikki stek bir vaqtda tura oladi — nomlar to'qnashmaydi.

```bash
# 0) Zaxira nusxa (eski baza, butunicha)
mkdir -p ~/backups
ESKI_USER=$(grep -E '^POSTGRES_USER=' ~/tender_deploy/.env | cut -d= -f2)
ESKI_DB=$(grep -E '^POSTGRES_DB=' ~/tender_deploy/.env | cut -d= -f2)
docker exec tender-postgres pg_dump -U "$ESKI_USER" -d "$ESKI_DB" -Fc \
  > ~/backups/tender_deploy_$(date +%F_%H%M).dump

# 1) Yangi baza + sxema (ilova HALI ishga tushmaydi)
cd ~/shaffofai-tender-v2
docker compose up -d db migrate && docker compose logs migrate

# 2) Eski yozuvchilarni to'xtatish (baza QOLADI; `down` EMAS)
cd ~/tender_deploy && docker compose stop tender-api tender-ai tender-yuboruvchi

# 3) Qaysi jadvallar bor — dump ro'yxatini shunga moslang
docker exec tender-postgres psql -U "$ESKI_USER" -d "$ESKI_DB" -c '\dt'

# 4) Faqat MA'LUMOT (sxema yangi bazada allaqachon bor), ketma-ketliklar bilan
DATA=~/backups/tender_data_$(date +%F_%H%M).sql
docker exec tender-postgres pg_dump -U "$ESKI_USER" -d "$ESKI_DB" \
  --data-only --no-owner --no-privileges \
  -t files -t templates -t jobs_state -t jobs_validation_log \
  -t yuborish_navbati -t validation_evidence > "$DATA"
#    (`validation_evidence` yoki `yuborish_navbati` 3-qadamda ko'rinmasa — ro'yxatdan olib tashlang)

# 5) Yangi bazaga yuklash (egasi bilan)
cd ~/shaffofai-tender-v2
docker compose exec -T db sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
  < "$DATA"

# 6) Solishtirish — sonlar teng bo'lsin
for t in files templates jobs_state yuborish_navbati; do
  echo "$t: eski=$(docker exec tender-postgres psql -U "$ESKI_USER" -d "$ESKI_DB" -Atc "select count(*) from $t") \
yangi=$(docker compose exec -T db sh -c "psql -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -Atc 'select count(*) from $t'")"
done

# 7) Yangi ilovani ishga tushirish
docker compose up -d
docker compose ps && docker compose exec worker python jobs_worker.py --health --toliq
```

Eski stekni bir hafta TO'XTATILGAN holda saqlang (qaytish yo'li), keyin
`docker compose rm` (volume'ni o'chirmasdan — zaxira sifatida).

**Qaytish:** `docker compose stop` (bu papkada), eski papkada `docker compose
start tender-api tender-ai tender-yuboruvchi`. Ko'chishdan KEYIN yangi bazaga
yozilganlar eski bazada yo'q — ular kerak bo'lsa 4-qadam teskari tomonga.

## 5. Yangilash va qaytish

CI har `main` push'da `ghcr.io/shaffofai/shaffofai-tender-v2:sha-<commit>` chiqaradi.

```bash
cd ~/shaffofai-tender-v2 && git pull --ff-only
sed -i 's/^TAG=.*/TAG=sha-<commit>/' .env
docker compose pull && docker compose up -d        # migrate avval yuradi
```

Qaytish — oldingi `TAG` bilan aynan shu. Migratsiyalar faqat QO'SHADI (eski
kod yangi sxema bilan ishlaydi); ustun o'chiradigan migratsiya bo'lsa, shu
yerda alohida yoziladi.

`docker compose stop` **xavfsiz**: worker joriy faylni tugatib to'xtaydi
(SIGTERM, 120 s), yarim ishlangan fayl lease tugagach qayta navbatga tushadi.

## 6. Kuzatish

```bash
docker compose ps                                          # hammasi healthy
docker compose logs -f worker | api | sender
docker compose exec worker python jobs_worker.py --health --toliq
docker compose exec sender python yuboruvchi.py --health
docker compose exec sender python yuboruvchi.py --holat    # outbox holati
docker compose exec api python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health').read().decode())"
docker compose exec worker python korish.py [--natijalar|--kamchilik|--tender ID|--sql]
docker compose exec worker python kuzatuv.py [--soat 2|--muammo|--fayl ID]
docker compose exec worker python -m app.db.migrate --holat
```

Healthcheck **faqat** baza, sxema, etalon manbai va yozish huquqlarini
tekshiradi — navbat uzunligi EMAS (katta navbat normal holat; aks holda Docker
konteynerni cheksiz qayta yoqaverardi). `sender --health` qo'shimcha: https,
kalitlar, 20 dan ortiq tashlangan xabar, texnik xabar toshqini.

API `/health` dagi `hisob.xato` o'sib borsa — tender noto'g'ri ma'lumot
yuboryapti; loglarda `[qabul qilinmadi]` sababi bor.

SQL (baza ichida — port ochiq emas):

```bash
docker compose exec db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```

```sql
SELECT status, count(*) FROM files GROUP BY status ORDER BY status;
SELECT holat, sabab, count(*) FROM yuborish_navbati GROUP BY 1, 2 ORDER BY 1, 2;
SELECT id, file_id, status, xato FROM yuborish_navbati WHERE holat = 3;   -- tashlanganlar
SELECT f.file_id, s.dead_at, s.dead_reason FROM jobs_state s
  JOIN files f ON f.id = s.uuid::bigint WHERE s.dead_at IS NOT NULL ORDER BY s.dead_at;
```

**Zaxira nusxa** (har kecha, cron):

```bash
0 3 * * * docker exec shaffofai-v2-tender-v2-db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > $HOME/backups/tender_v2_$(date +\%F).dump
```

## 7. Nosozliklar

| Belgi | Sabab | Yechim |
|---|---|---|
| `migrate` yiqildi | parol noto'g'ri / baza tayyor emas | `docker compose logs migrate`; `.env` dagi `POSTGRES_PASSWORD` baza birinchi yaratilgandagi bilan bir xil bo'lsin |
| `sxema: baza sxemasi qo'llanmagan` | `migrate` yurmagan | `docker compose up -d migrate` |
| `yozish huquqi yetishmaydi` | rol/huquqlar eski | `docker compose up -d migrate` (huquqlarni qayta beradi) |
| `[sozlama] JOBS_TABLE=... endi sozlanmaydi` | eski `.env` | o'zgaruvchini `.env` dan olib tashlang |
| `etalon manbai YO'Q, lekin navbatda N ta fayl` | `templates` bo'sh | sherik shablonlarni yuborishi kerak |
| fayllar `status = 0` da qolmoqda | texnik sabab (rad EMAS) | `python jobs_worker.py --dead-letters`, keyin `--requeue all` |
| 3/4 xabarlari `holat=3` (422) | sherik validatori `in:1,2` | kengaytirilgach `python yuboruvchi.py --qayta-och 3,4` |
| disk to'ldi | etalon keshi | `python jobs_worker.py --kesh-tozala` yoki `ETALON_CACHE_MAX_MB` |

## 8. Xavfsizlik

1. `.env` — `chmod 600`, git ga ham, rasmga ham tushmaydi. Parollar faqat harf/raqam.
2. Baza porti ochilmaydi; API porti faqat `EDGE_BIND` da (HECH QACHON `0.0.0.0`).
3. `/docs`, `/redoc`, `/openapi.json` yopiq (`DOCS_ENABLED=0`).
4. Ilova konteynerlarida jadvallar egasining paroli yo'q (`POSTGRES_PASSWORD: ""`).
5. Kiruvchi Basic Auth — TLS ortida (kompaniya edge'i); parol base64, shifr emas.
6. Konteynerlar ildiz huquqisiz (`uid 10001`).
