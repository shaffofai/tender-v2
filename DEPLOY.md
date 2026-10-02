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
- So'rov jurnali (`sorov_jurnali`, migratsiya `0002`) asosiy ishni to'xtatmaydi:
  alohida oqim, o'z ulanishi; baza yotsa API avvalgidek javob beradi, jurnal
  yozuvlari esa navbatda kutadi (to'lsa — tashlanadi). Ilova roli jurnalga faqat
  qo'sha oladi (SELECT, INSERT). Nima yozilishi — `README.md`, «So'rov jurnali».

## 2. Talablar

| | |
|---|---|
| Docker | 24+ va `docker compose` v2 |
| Platforma | gateway stack ishlab turgan bo'lsin — `shaffofai-v2` tarmog'ini u yaratadi |
| Tarmoq | chiquvchi: `apisitender.mc.uz` (fayllar), `api.shaffofxarid.uz` (verdiktlar) |
| Disk | ~3 GB (etalon keshi 2 GB gacha) + baza |
| RAM | worker chegarasi 4 GB (odatda 200–400 MB), api 512 MB, sender 256 MB |

## 3. Birinchi o'rnatish (bo'sh server)

> **Serverda eski `~/tender_deploy` bo'lsa — bu bo'lim EMAS, §4.** §4 ham
> o'rnatadi, ham ma'lumotni ko'chiradi. Bu yerdagi `up -d` esa worker va
> sender'ni BO'SH baza bilan ishga tushiradi.

Serverda `docker` buyruqlari `sudo` bilan (`debian` foydalanuvchisi `docker`
guruhida emas).

```bash
cd ~ && git clone https://github.com/shaffofai/tender-v2.git shaffofai-tender-v2
cd ~/shaffofai-tender-v2
cp .env.example .env && chmod 600 .env
nano .env        # POSTGRES_PASSWORD, APP_DB_PASSWORD (openssl rand -hex 24), KIRUVCHI_*, TENDER_API_*,
                 # JURNAL_LOGIN, JURNAL_PAROL (openssl rand -hex 24), EDGE_BIND=192.168.100.60, TAG=sha-<commit>
sudo docker compose config --quiet && echo "config OK"
sudo docker compose pull
sudo docker compose up -d --no-build
sudo docker logs shaffofai-v2-tender-v2-migrate    # «0001_boshlangich_sxema qo'llandi», «0002_sorov_jurnali qo'llandi»
sudo docker compose ps                             # db, api, worker, sender — healthy
```

`.env` dagi `COMPOSE_FILE=docker-compose.yml:docker-compose.edge.yml` tufayli
shu papkadagi HAR `sudo docker compose ...` edge faylini ham oladi — API
`EDGE_BIND:8084` da (router/edge hali shu manzilni kutadi). Bu qatorsiz `up -d`
API ni PORTSIZ qayta yaratadi va tashqaridan 502 bo'ladi. `export COMPOSE_FILE`
yordam bermaydi: `sudo` muhit o'zgaruvchilarini o'tkazmaydi.

> ⚠️ **`docker compose down -v` HECH QACHON** — baza volume'i o'chadi.
> ⚠️ **`iptables -F` HECH QACHON** — Docker'ning FORWARD qoidalari o'chadi va
> serverdagi BARCHA konteynerlar tashqaridan ko'rinmay qoladi (2026-09-28).

Tekshiruv:

```bash
curl -s http://192.168.100.60:8084/health; echo                                                     # "baza": "ok"
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://192.168.100.60:8084/api-v2/tender-v2/check   # 401
# tashqaridan (noutbukdan):
curl -s -o /dev/null -w '%{http_code}\n' https://ai.shq.uz/api-v2/tender-v2/health                  # 200
curl -s -o /dev/null -w '%{http_code}\n' -X POST https://ai.shq.uz/api-v2/tender-v2/check           # 401
```

> Kompaniya edge'i `/api-v2/tender-v2/...` ni `192.168.100.60:8084` ga
> prefiksini KESIB yuboradi (2026-09-29 da tekshirildi:
> `POST .../api-v2/tender-v2/api-v2/tender-v2/check` → 401). Shuning uchun ilova
> har yo'lni ikki shaklda qabul qiladi — `/api-v2/tender-v2/check` va `/check`,
> `/api-v2/tender-v2/health` va `/health` (bitta ishlovchi). Edge tuzatilsa ham
> hech narsa buzilmaydi.

## 4. Eski `tender_deploy` dan o'tish (ma'lumot bilan)

Eski stek — `~/tender_deploy`, konteynerlar `tender-postgres`, `tender-ai`,
`tender-api`, `tender-yuboruvchi`. Ularning compose XIZMAT nomlari boshqa
(`postgres`, `tender-ai`, `api`, `yuboruvchi`), shuning uchun quyida
to'xtatish va yoqish konteyner nomi bilan (`docker stop`/`start`). Eski baza
faqat O'QILADI — qaytish yo'li doim ochiq.

Ketma-ketlik 2026-09-29 da PostgreSQL 17 da AYNAN shu matn bilan sinaldi:
eski sxema eski stekning o'z SQL fayllaridan, yangisi `app.db.migrate` dan
qurildi; qatorlar, mazmun va ketma-ketliklar aynan ko'chdi; ilova roli yangi
qatorlarni to'qnashuvsiz qo'shdi; yuklash xato bersa yangi baza BO'SH qoldi
(bitta tranzaksiya).

Hammasi `debian` foydalanuvchisidan. Chiqishlarda sir yo'q — bemalol ulashing.

### 4.0 Yordamchilar — har SSH sessiyada birinchi

```bash
eski()  { sudo docker exec -e PGTZ=UTC tender-postgres           sh -c 'exec psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" "$@"' sh "$@"; }
yangi() { sudo docker exec -e PGTZ=UTC shaffofai-v2-tender-v2-db sh -c 'exec psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" "$@"' sh "$@"; }
JADVALLAR="'files','templates','jobs_state','jobs_validation_log','yuborish_navbati','validation_evidence'"
T=$(eski -At -c "select string_agg(tablename, ' ' order by tablename) from pg_tables where schemaname = 'public' and tablename in ($JADVALLAR)")
echo "tables: $T"
```

### 4.1 Tekshiruv — hech narsani o'zgartirmaydi

```bash
free -m; df -h /
sudo iptables -S FORWARD | grep -E -- '-j DOCKER-(USER|FORWARD)'                  # ikkala qator
sudo docker network ls --filter name='^shaffofai-v2$' --format '{{.Name}}'         # shaffofai-v2
sudo docker ps -a --filter name='^tender-' --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
sudo ss -ltn | grep -q ':8084 ' && echo "8084 IN USE" || echo "8084 free"
eski -c "select pg_size_pretty(pg_database_size(current_database())) as size"
eski -c "select relname as table, n_live_tup as approx_rows from pg_stat_user_tables order by 1"
```

To'xtang, agar: FORWARD qatorlaridan biri yo'q (avval `sudo systemctl restart
docker`); `tables:` da oltitadan kam jadval; eski bazada boshqa, qatorli
jadvallar ham bor.

### 4.2 O'rnatish — eski stek ishlashda davom etadi

Rasm klonlangan commit'ga bog'lanadi (`TAG=sha-<commit>`) — shu commit'ning
CI'i yashil bo'lgach yurgizing (aks holda `pull` rasmni topmaydi, hech narsa
to'xtamaydi — kutib, qayta yurgizing).

```bash
cd ~ && git clone https://github.com/shaffofai/tender-v2.git shaffofai-tender-v2 && cd ~/shaffofai-tender-v2 && git log -1 --format='%h %s'
```

`.env`: eski `.env` dagi sozlamalar (kalitlar, URL lar, sonlar) AYNAN ko'chadi,
qiymatlar ekranga chiqmaydi — faqat nomlar (`+` ko'chdi, `-` ataylab
ko'chmadi: baza, port, yo'llar, olib tashlangan sozlamalar). Yangi baza
parollari yaratiladi. Qayta yurgizish xavfsiz — parollar o'zgarmaydi.

```bash
cd ~/shaffofai-tender-v2
if [ -e .env ]; then echo ".env already exists - left as it is"
elif ! sudo test -s ~/tender_deploy/.env; then echo "~/tender_deploy/.env not found - stop"
else ( umask 077
  sudo cat ~/tender_deploy/.env | awk -v skip='^(DATABASE_URL|DB_[A-Z_]*|POSTGRES_[A-Z_]*|APP_DB_[A-Z_]*|API_PORT|JOBS_TABLE|TEMPLATES_TABLE|JOBS_PK|JOBS_GROUP_COL|ETALON_DISK_FALLBACK|ETALON_DIR|ETALON_CACHE_DIR|DOWNLOAD_DIR|LOG_FILE|EDGE_BIND|EDGE_PORT|TAG|IMAGE|DOCS_ENABLED|COMPOSE_[A-Z_]*|[A-Z_]*_MEMORY)$' '
    NR == FNR { if (match($0, /^[A-Za-z_][A-Za-z0-9_]*=/)) { k = substr($0, 1, RLENGTH - 1)
                  if (k ~ skip) skipped[k] = 1; else old[k] = $0 }
                next }
    match($0, /^[A-Za-z_][A-Za-z0-9_]*=/) { k = substr($0, 1, RLENGTH - 1)
                  if (k in old) { print old[k]; took[k] = 1; next } }
    { print }
    END { for (k in old) if (!(k in took)) { if (!n++) print "\n# --- from ~/tender_deploy/.env (not in .env.example) ---"; print old[k]; took[k] = 1 }
          for (k in took) print "  + " k | "sort >&2"
          for (k in skipped) print "  - " k | "sort >&2" }
  ' - .env.example > .env ); fi
sed -i -e "s/^POSTGRES_PASSWORD=$/POSTGRES_PASSWORD=$(openssl rand -hex 24)/" \
       -e "s/^APP_DB_PASSWORD=$/APP_DB_PASSWORD=$(openssl rand -hex 24)/" \
       -e "s/^EDGE_BIND=.*/EDGE_BIND=192.168.100.60/" \
       -e "s/^TAG=.*/TAG=sha-$(git rev-parse HEAD)/" .env
grep -q '^COMPOSE_FILE=' .env || printf '\n# every `sudo docker compose` in this folder also reads the edge file\nCOMPOSE_FILE=docker-compose.yml:docker-compose.edge.yml\n' >> .env
stat -c '%a %U' .env                                        # 600 debian
grep -nE '^[A-Za-z_][A-Za-z0-9_]*=[[:space:]]*(#.*)?$' .env || echo "no empty values"
sudo docker compose config --quiet && echo "config OK"
sudo docker compose config --images | sort -u              # postgres:17 + ghcr.io/...:sha-<klonlangan commit>
sudo docker compose config | grep -A5 '^    ports:'        # host_ip: 192.168.100.60 / target: 8000 / published: "8084"
```

`+` ro'yxatida `KIRUVCHI_LOGIN`, `KIRUVCHI_PAROL`, `TENDER_API_URL`,
`TENDER_API_LOGIN`, `TENDER_API_PAROL` bo'lishi SHART. Bo'sh qiymat faqat
`TENDER_STATUS_XARITA`, `JURNAL_LOGIN` va `JURNAL_PAROL` da bo'lishi mumkin
(jurnal kalitlari bo'sh bo'lsa `/jurnal` 503 beradi, qolgan hammasi ishlaydi —
§6 da to'ldiriladi).

```bash
sudo docker compose pull
sudo docker image ls ghcr.io/shaffofai/shaffofai-tender-v2   # TAG sha-<klonlangan commit>
sudo docker compose up -d --no-build db migrate
sudo docker wait shaffofai-v2-tender-v2-migrate               # 0
sudo docker logs shaffofai-v2-tender-v2-migrate               # 0001_boshlangich_sxema qo'llandi, 0002_sorov_jurnali qo'llandi, ilova roli yaratildi: tender_ai
USTUN_Q="select table_name||'.'||column_name||' '||data_type from information_schema.columns where table_schema = 'public' and table_name in ($JADVALLAR) order by 1"
diff <(eski -At -c "$USTUN_Q") <(yangi -At -c "$USTUN_Q") && echo "COLUMNS IDENTICAL ($(eski -At -c "$USTUN_Q" | wc -l))"
```

### 4.3 O'tish — to'xtash vaqti, odatda bir necha daqiqa

```bash
cd ~/shaffofai-tender-v2 && mkdir -p ~/backups
sudo docker stop -t 130 tender-api tender-ai tender-yuboruvchi      # worker joriy faylni tugatib to'xtaydi
sudo docker ps -a --filter name='^tender-' --format '{{.Names}}: {{.Status}}'
eski -c "select usename, client_addr, application_name from pg_stat_activity where datname = current_database() and pid <> pg_backend_pid()"
TS=$(date +%F_%H%M); FULL=~/backups/tender_deploy_$TS.dump; DATA=~/backups/tender_data_$TS.sql
( umask 077 &&
  sudo docker exec tender-postgres sh -c 'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" "$@"' sh -Fc > "$FULL" &&
  sudo docker exec tender-postgres sh -c 'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" "$@"' sh \
    --data-only --no-owner --no-privileges --strict-names $(printf -- '-t %s ' $T) > "$DATA" ) && echo "DUMPS OK"
ls -lh "$FULL" "$DATA"
sudo docker exec -i tender-postgres pg_restore --list < "$FULL" | grep -c 'TABLE DATA'
echo "COPY blocks: $(grep -c '^COPY ' "$DATA")  setval: $(grep -c 'setval' "$DATA")"
sudo docker exec -i shaffofai-v2-tender-v2-db sh -c 'exec psql -X -q -v ON_ERROR_STOP=1 --single-transaction -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < "$DATA" > /dev/null && echo "RESTORE OK"
TEKSHIR_Q="$(for t in $T; do printf "select '%s' as t, count(*) as n, coalesce(sum(hashtextextended(to_jsonb(x)::text, 0)), 0) as h from %s x union all " $t $t; done) select 'seq '||sequencename, last_value, 0 from pg_sequences where schemaname = 'public' and sequencename in ('files_id_seq','templates_id_seq','jobs_validation_log_id_seq','yuborish_navbati_id_seq','validation_evidence_id_seq') order by 1"
eski -At -c "$TEKSHIR_Q" > ~/backups/check_old_$TS.txt && yangi -At -c "$TEKSHIR_Q" > ~/backups/check_new_$TS.txt
cat ~/backups/check_new_$TS.txt
diff ~/backups/check_old_$TS.txt ~/backups/check_new_$TS.txt && echo "OLD = NEW (rows, contents, sequences)"
```

Kutiladi: uchta `Exited`, `(0 rows)`, `DUMPS OK`, `TABLE DATA` soni ≥ 6,
`COPY blocks: 6  setval: 5`, `RESTORE OK`, `OLD = NEW`. Boshqacha bo'lsa —
yangi stekni YOQMANG, «Qaytish» ga o'ting.

```bash
sudo docker compose up -d --no-build
sleep 90; sudo docker compose ps --format 'table {{.Name}}\t{{.Status}}'   # api, db, sender, worker: (healthy)
sudo docker logs --tail 2 shaffofai-v2-tender-v2-migrate                   # ... qo'llanadigan migratsiya yo'q
sudo docker compose exec worker python jobs_worker.py --health --toliq     # oxirgi qator: HOLAT: SOG'LOM
sudo docker compose exec sender python yuboruvchi.py --health              # oxirgi qator: SOG'LOM
sudo docker compose exec sender python yuboruvchi.py --holat
curl -s http://192.168.100.60:8084/health; echo                            # ..."baza": "ok", "ogohlantirish" YO'Q
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://192.168.100.60:8084/api-v2/tender-v2/check   # 401
sudo docker compose logs --since 5m worker sender | tail -30
```

Tashqaridan (noutbukdan): `https://ai.shq.uz/api-v2/tender-v2/health` → 200,
`POST https://ai.shq.uz/api-v2/tender-v2/check` (parolsiz) → 401. `.../check`
404 bersa — ishlayotgan rasm `/check` yo'lidan oldingi (3-bo'lim oxiridagi eslatma).

### 4.4 Yakun — tekshiruvlar o'tgach

```bash
sudo docker stop tender-postgres            # eski 0.0.0.0:5432 yopiladi; volume va zaxiralar qoladi
sudo mv ~/tender_deploy/docker-compose.yml ~/tender_deploy/docker-compose.yml.retired
```

Ikkinchi qator: `~/tender_deploy` da tasodifiy `docker compose up` eski worker
va sender'ni ESKI baza bilan qayta yoqib, verdiktlarni ikkinchi marta
yubormasin (2026-09-28 da o'sha papka ogohlantirishsiz qayta yoqilgan).

Tungi zaxira — root crontab'iga (`sudo crontab -e`), platformaniki yoniga;
hafta kuni bo'yicha 7 ta fayl:

```
30 3 * * * docker exec shaffofai-v2-tender-v2-db sh -c 'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > /home/debian/backups/tender-v2-$(date +\%a).dump 2>>/home/debian/backups/nightly.log
```

Bir haftadan keyin: `sudo docker rm tender-api tender-ai tender-yuboruvchi
tender-postgres` (`-v` SIZ — eski baza volume'i oxirgi nusxa bo'lib qoladi).

### Qaytish

```bash
cd ~/shaffofai-tender-v2 && sudo docker compose stop
sudo docker start tender-postgres && sudo docker start tender-yuboruvchi tender-ai tender-api
```

4.4 bajarilgan bo'lsa, eski compose faylini ham qaytaring:
`sudo mv ~/tender_deploy/docker-compose.yml.retired ~/tender_deploy/docker-compose.yml`.
Yangi stek yoqilgandan KEYIN qaytilsa: u chiqargan verdiktlar faqat yangi
bazada qoladi — eski stek o'sha fayllarni qayta tekshirib, bir xil verdiktni
qayta yuboradi.

## 5. Yangilash va qaytish

CI har `main` push'da `ghcr.io/shaffofai/shaffofai-tender-v2:sha-<commit>` chiqaradi.

```bash
cd ~/shaffofai-tender-v2 && git pull --ff-only
sed -i 's/^TAG=.*/TAG=sha-<commit>/' .env
sudo docker compose pull && sudo docker compose up -d --no-build    # migrate avval yuradi
```

Qaytish — oldingi `TAG` bilan aynan shu. Migratsiyalar faqat QO'SHADI (eski
kod yangi sxema bilan ishlaydi); ustun o'chiradigan migratsiya bo'lsa, shu
yerda alohida yoziladi.

**So'rov jurnali qo'shilgan versiyaga o'tish** (sxema 1 → 2). Yuqoridagi uch
qatordan keyin:

```bash
sudo docker logs shaffofai-v2-tender-v2-migrate | tail -2
sudo docker compose exec worker python -m app.db.migrate --holat
```

Kutiladi — `migrate` logining oxirgi ikki satri (har biri sana-vaqt bilan boshlanadi):

```
... INFO    [migratsiya] 0002_sorov_jurnali qo'llandi
... INFO    [migratsiya] «tender_ai» huquqlari yangilandi (app/db/huquqlar.sql)
```

va `--holat` da ikkala migratsiya `qo'llangan`. `0002` faqat yangi jadval
(`sorov_jurnali`) qo'shadi — mavjud jadvallarga tegmaydi. Yangi rasm sxema
versiyasi 2 ni TALAB qiladi (`migrate` yurmagan bo'lsa xizmatlar «baza sxemasi
eski» deb to'xtaydi); eski rasm esa versiya 2 bilan o'zgarishsiz ishlaydi —
qaytish uchun bazada hech narsa qilish shart emas. Jurnalni o'qish kalitlari —
§6 («So'rov jurnali»).

`docker compose stop` **xavfsiz**: worker joriy faylni tugatib to'xtaydi
(SIGTERM, 120 s), yarim ishlangan fayl lease tugagach qayta navbatga tushadi.

## 6. Kuzatish

Serverda — `~/shaffofai-tender-v2` papkasida, `sudo` bilan (`sudo docker compose ...`).

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

**Zaxira nusxa** — har kecha, §4.4 dagi cron qatori (root crontab'i; hafta
kuni bo'yicha 7 ta fayl `/home/debian/backups/` da). Vaqti-vaqti bilan bittasini
serverdan tashqariga ko'chiring: bir diskdagi zaxira disk bilan birga yo'qoladi.

### So'rov jurnali

Kim, qachon, nima yuborgani va nima javob olgani — `sorov_jurnali` jadvalida
(mazmuni: `README.md`, «So'rov jurnali»). O'qish kalitlarini bir marta qo'ying
(sherikka bergan `KIRUVCHI_*` EMAS; parol kamida 24 belgi, aks holda 503):

```bash
cd ~/shaffofai-tender-v2
grep -q '^JURNAL_LOGIN=' .env || printf '\nJURNAL_LOGIN=\nJURNAL_PAROL=\n' >> .env
sed -i -e "s/^JURNAL_LOGIN=$/JURNAL_LOGIN=jurnal/" -e "s/^JURNAL_PAROL=$/JURNAL_PAROL=$(openssl rand -hex 24)/" .env
sudo docker compose up -d --no-build api
```

O'qish (serverning o'zidan; qiymatlar ekranga chiqmaydi — `curl` ularni `.env` dan oladi):

```bash
J=$(sed -n 's/^JURNAL_LOGIN=//p' .env):$(sed -n 's/^JURNAL_PAROL=//p' .env)
curl -s -u "$J" "http://192.168.100.60:8084/jurnal?limit=5"; echo                     # oxirgi 24 soat, yangisi birinchi
curl -s -u "$J" "http://192.168.100.60:8084/jurnal?tur=kirish"; echo                  # rad etilgan kirishlar (401)
curl -s -u "$J" "http://192.168.100.60:8084/jurnal?tur=sorov&holat_kodi=503"; echo    # biz 503 bergan so'rovlar
curl -s -u "$J" "http://192.168.100.60:8084/jurnal?daraja=error&manba=worker"; echo   # worker xatolari
curl -s -u "$J" "http://192.168.100.60:8084/jurnal/12345"; echo                       # bitta yozuv to'liq (tanalari bilan)
curl -s -u "$J" "http://192.168.100.60:8084/jurnal?keyin_id=12345"; echo              # 12345 dan KEYIN yozilganlar
```

Tashqaridan — `https://ai.shq.uz/api-v2/tender-v2/jurnal` (o'sha kalitlar).
Yoki to'g'ridan-to'g'ri SQL bilan (yuqoridagi `psql`):

```sql
SELECT tur, manba, count(*), min(yaratildi), max(yaratildi) FROM sorov_jurnali GROUP BY 1, 2 ORDER BY 1, 2;
SELECT id, yaratildi, usul, yol, holat_kodi, davomiylik_ms, ip, login, tasdiqlangan
  FROM sorov_jurnali WHERE tur = 'sorov' ORDER BY id DESC LIMIT 20;
SELECT yaratildi, ip, login, qoshimcha->>'sabab' AS sabab FROM sorov_jurnali WHERE tur = 'kirish' ORDER BY id DESC LIMIT 20;
SELECT yaratildi, manba, hodisa, qoshimcha FROM sorov_jurnali WHERE tur = 'amal' ORDER BY id DESC;   -- --requeue / --qayta-och
SELECT pg_size_pretty(pg_total_relation_size('sorov_jurnali'));
```

**Saqlash muddati.** Ilova roli jurnalni o'chira OLMAYDI (faqat SELECT, INSERT)
— eskirgan yozuvlarni jadval EGASI o'chiradi, ya'ni `db` konteyneri ichidan
(`POSTGRES_USER`). Jadval tungi zaxiraga ham tushadi (7 ta nusxa), shuning
uchun muddatsiz qoldirmang. Qo'lda (muddatni o'zingiz belgilang — bu yerda 90 kun):

```bash
sudo docker compose exec db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "DELETE FROM sorov_jurnali WHERE yaratildi < now() - make_interval(days => 90)"'
```

Chiqishi — `DELETE <o'chirilgan qatorlar soni>`. Har kecha avtomatik — root
crontab'iga (`sudo crontab -e`), zaxira qatoridan keyin:

```
45 3 * * * docker exec shaffofai-v2-tender-v2-db sh -c 'exec psql -q -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "DELETE FROM sorov_jurnali WHERE yaratildi < now() - make_interval(days => 90)"' >>/home/debian/backups/nightly.log 2>&1
```

API logida (`docker compose logs api`) `[jurnal] bazaga yozilmadi (...)` —
yozuvchi oqim bazaga ulana olmayapti (yozuvlar navbatda, o'zi qayta urinadi);
tiklangach `[jurnal] yozish tiklandi` chiqadi. Har ikkalasi faqat holat
o'zgarganda, bir martadan yoziladi.

## 7. Nosozliklar

| Belgi | Sabab | Yechim |
|---|---|---|
| tashqaridan 502, `docker compose ps` da api'da port yo'q | `up` edge faylisiz bo'lgan | `.env` da `COMPOSE_FILE=docker-compose.yml:docker-compose.edge.yml` bo'lsin, keyin `sudo docker compose up -d --no-build` |
| `migrate` yiqildi | parol noto'g'ri / baza tayyor emas | `docker compose logs migrate`; `.env` dagi `POSTGRES_PASSWORD` baza birinchi yaratilgandagi bilan bir xil bo'lsin |
| `sxema: baza sxemasi qo'llanmagan` | `migrate` yurmagan | `docker compose up -d migrate` |
| `yozish huquqi yetishmaydi` | rol/huquqlar eski | `docker compose up -d migrate` (huquqlarni qayta beradi) |
| `[sozlama] JOBS_TABLE=... endi sozlanmaydi` | eski `.env` | o'zgaruvchini `.env` dan olib tashlang |
| `etalon manbai YO'Q, lekin navbatda N ta fayl` | `templates` bo'sh | sherik shablonlarni yuborishi kerak |
| fayllar `status = 0` da qolmoqda | texnik sabab (rad EMAS) | `python jobs_worker.py --dead-letters`, keyin `--requeue all` |
| 3/4 xabarlari `holat=3` (422) | sherik validatori `in:1,2` | kengaytirilgach `python yuboruvchi.py --qayta-och 3,4` |
| disk to'ldi | etalon keshi | `python jobs_worker.py --kesh-tozala` yoki `ETALON_CACHE_MAX_MB` |
| `/jurnal` → 503 `jurnalni o'qish sozlanmagan` | `JURNAL_LOGIN` / `JURNAL_PAROL` yo'q yoki parol 24 belgidan qisqa | §6 «So'rov jurnali» dagi uch qator |
| `/jurnal` → 401, kalit to'g'ri | `KIRUVCHI_*` kalitlari ishlatilgan | jurnalning O'Z kalitlari: `JURNAL_LOGIN` / `JURNAL_PAROL` |
| logda `[jurnal] yozuv tashlandi (InsufficientPrivilege)` | ilova rolida jurnal huquqi yo'q | `docker compose up -d migrate` (huquqlarni qayta beradi) |
| logda `[jurnal] yozuvchi oqim yurmadi (...)` | konteynerda oqim/jarayon chegarasi tugagan; xizmat ishlayapti, lekin shu jarayon jurnalga YOZMAYAPTI | sababini bartaraf etib, o'sha xizmatni qayta ishga tushiring (`sudo docker compose restart api`) |
| bir necha xizmat logida BIRDAN `[jurnal] bazaga yozilmadi (LockNotAvailable)`, operator buyrug'ida `[jurnal] amal yozilmadi (...): LockNotAvailable` | bitta xizmat jurnalga yozish o'rtasida qotgan (`docker compose pause`, osilgan xost) va jurnal yozish qulfini ushlab turibdi; PostgreSQL uning sessiyasini 10 s da uzadi, keyin hammasi o'zi tiklanadi (`[jurnal] yozish tiklandi`) — shu orada berilgan operator amali jurnalga tushmaydi | cho'zilsa: `sudo docker compose ps` da `Paused` xizmatni `sudo docker compose unpause <xizmat>`; yoki `sorov_jurnali` ni egasi uzoq bloklayapti (`VACUUM FULL`, `ALTER TABLE`) — tugashini kuting |
| jurnalda `hodisa = jurnal_tashlandi`, `qoshimcha.chegaradan` bor | kalitsiz / noto'g'ri kalitli so'rovlar oqimi: daqiqasiga 600 qatordan ortig'i yozilmaydi (to'g'ri kalitli so'rovlar to'liq yoziladi) | `GET /jurnal?tur=kirish` — kim urinayotganini ko'ring; to'sish — edge tomonda |
| jurnal juda katta | saqlash muddati qo'yilmagan | §6 dagi `DELETE` (egasi roli bilan); vaqtincha to'xtatish: `.env` da `JURNAL_YOQILGAN=0` |

## 8. Xavfsizlik

1. `.env` — `chmod 600`, git ga ham, rasmga ham tushmaydi. Parollar faqat harf/raqam.
2. Baza porti ochilmaydi; API porti faqat `EDGE_BIND` da (HECH QACHON `0.0.0.0`).
3. `/docs`, `/redoc`, `/openapi.json` yopiq (`DOCS_ENABLED=0`).
4. Ilova konteynerlarida jadvallar egasining paroli yo'q (`POSTGRES_PASSWORD: ""`).
5. Kiruvchi Basic Auth — TLS ortida (kompaniya edge'i); parol base64, shifr emas.
6. Konteynerlar ildiz huquqisiz (`uid 10001`).
7. So'rov jurnali sherikning so'rov tanalari, IP lar va log satrlarini saqlaydi
   va `/jurnal` edge orqali internetdan ochiq: `JURNAL_PAROL` — kamida 24 belgi
   (`openssl rand -hex 24`), `KIRUVCHI_*` dan alohida va sherikka BERILMAYDI.
   `Authorization` qiymati jurnalga yozilmaydi; ilova roli yozuvni o'zgartira
   ham, o'chira ham olmaydi.
