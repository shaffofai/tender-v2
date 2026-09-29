-- ============================================================================
-- 0001_boshlangich_sxema.sql — tender-v2 bazasining BOSHLANG'ICH sxemasi
--
-- Uch manbaning AYNAN birlashmasi (2026-09-29 refaktoridan oldingi holat):
--   1) deploy/sql/01_sherik_jadvallar.sql   — files, templates (+ cheklovlar, indekslar)
--   2) jobs_schema.sql                       — jobs_state, jobs_validation_log, yuborish_navbati
--   3) tender_engine/evidence.py DDL + MIGRATSIYA — validation_evidence (append-only)
--
-- Har bayonot IDEMPOTENT (IF NOT EXISTS, DROP ... IF EXISTS + ADD, CREATE OR
-- REPLACE): eski skriptlar bilan yaratilgan MAVJUD baza ustida ham xavfsiz —
-- `python -m app.db.migrate` uni o'zgartirmay «qabul qiladi» va versiyani yozadi.
--
-- Yangi o'zgarish = YANGI fayl (0002_...). Qo'llangan faylni TAHRIRLAMANG.
-- ============================================================================
-- ─── 1) deploy/sql/01_sherik_jadvallar.sql ───────────────────────────────────
-- ============================================================================
-- 01_sherik_jadvallar.sql — `files` va `templates` jadvallari
--
-- 2026-09-23 dan boshlab bu jadvallar BIZNING bazamizda (API integratsiyasi):
-- tender tizimining bazaga to'g'ridan-to'g'ri kirishi to'xtatildi, ma'lumot
-- `api_server.py` orqali keladi. Shuning uchun endi CHECK va UNIQUE
-- cheklovlarini ham qo'shamiz — ilgari sherik DDL si aynan ko'chirilar edi.
--
-- Avtomatik: docker compose up -d postgres  (docker-entrypoint-initdb.d, birinchi ishga tushishda)
-- Qo'lda:    psql "$DATABASE_URL" -f deploy/sql/01_sherik_jadvallar.sql
--
-- Xizmat jadvallari (jobs_state, jobs_validation_log, yuborish_navbati) —
-- `jobs_schema.sql` da. `validation_evidence` — FAQAT `jobs_worker.py --init-db`.
-- ============================================================================

create table if not exists files
(
    id         bigserial
        primary key,
    link       varchar(255)                   not null,
    file_id    bigint                         not null,
    tender_id  bigint                         not null,
    type       varchar(255)                   not null,
    status     smallint default '0'::smallint not null,

    -- ⚠️ `send` USTUNINI OLIB TASHLAMANG.
    -- Yangi arxitekturada uni hech kim yozmaydi (har doim 0), lekin
    -- `jobs_worker._jobs_ustunlarini_aniqla` uni MAJBURIY deb biladi
    -- (`kerak = {"link","status","send","comment","type"}`) va yo'q bo'lsa
    -- `SxemaXatosi` ko'taradi — worker umuman ishga tushmaydi.
    -- Bundan tashqari `_yuborilmagan()` bu ustunga tayangan `WHERE send = 0`
    -- shartini to'rt joyga qo'yadi.
    send       smallint default '0'::smallint not null,

    comment    text,
    created_at timestamp(0),
    updated_at timestamp(0),
    deleted_at timestamp(0)
);

create table if not exists templates
(
    id         bigserial
        primary key,
    link       varchar(255)                   not null,
    file_id    bigint                         not null,
    tender_id  bigint                         not null,
    type       varchar(255)                   not null,
    status     smallint default '0'::smallint not null,
    created_at timestamp(0),
    updated_at timestamp(0),
    deleted_at timestamp(0)
);

-- ── Cheklovlar ──────────────────────────────────────────────────────────────
-- `IF NOT EXISTS` CONSTRAINT uchun yo'q, shuning uchun DO blok bilan.
-- Idempotent: skript qayta yurgizilsa xato bermaydi.
do $$
begin
    -- B1/B2: `file_id` butun bazada noyob. Kiruvchi API takrorni shu orqali
    -- tutadi (`IntegrityError` → javobda `takror`).
    if not exists (select 1 from pg_constraint where conname = 'files_file_id_uniq') then
        alter table files add constraint files_file_id_uniq unique (file_id);
    end if;
    if not exists (select 1 from pg_constraint where conname = 'templates_file_id_uniq') then
        alter table templates add constraint templates_file_id_uniq unique (file_id);
    end if;

    -- Status qiymatlari: 0 tekshirilmagan (navbatda), 1 qabul, 2 rad, 3 aynan
    -- nusxa (2026-09-18), 4 texnik — tekshirib bo'lmadi, taslim bo'lindi
    -- (2026-09-23; rad EMAS, `--requeue` bilan 0 ga qaytadi).
    -- DROP + ADD: mavjud bazada eski (0..3) cheklov 4 ga kengaysin (idempotent).
    alter table files drop constraint if exists files_status_chk;
    alter table files add constraint files_status_chk check (status in (0,1,2,3,4));
end $$;

-- ── Indekslar ───────────────────────────────────────────────────────────────
--   navbat:  WHERE status = 0 AND send = 0 AND deleted_at IS NULL
--   shablon: WHERE tender_id = ? AND type = ? ORDER BY id DESC
create index if not exists files_navbat_idx
    on files (status, send) where deleted_at is null;
create index if not exists files_tender_type_idx
    on files (tender_id, type);
create index if not exists templates_tender_type_idx
    on templates (tender_id, type, id desc);

-- ─── 2) jobs_schema.sql ───────────────────────────────────────────────────────
-- ============================================================================
-- jobs_schema.sql — `jobs` jadvaliga TEGMASDAN ishlash uchun sxema
--
-- MUHIM: `jobs` jadvali BOSHQA JAMOAGA tegishli. Bu skript unga hech qanday
-- ustun qo'shmaydi va o'zgartirmaydi. Worker o'z xizmat holatini (urinishlar,
-- lease, o'lik belgisi) ALOHIDA `jobs_state` jadvalida saqlaydi.
--
-- Worker `jobs` da faqat: SELECT ... va UPDATE status/comment (send=false bo'lsa).
--
--   psql "$DATABASE_URL" -f jobs_schema.sql
--   yoki: python jobs_worker.py --init-db
-- ============================================================================

-- 1) Worker xizmat holati — HAR BIR jobs qatori uchun bitta yozuv
CREATE TABLE IF NOT EXISTS jobs_state (
    uuid             text PRIMARY KEY,        -- jobs.uuid ga mos (FK QO'YILMAYDI —
                                              -- jobs boshqa jamoaniki, qattiq bog'lanmaymiz)
    claimed_at       timestamptz,             -- lease: NULL yoki eskirgan = olish mumkin
    claim_token      text,                    -- fencing: zombi worker yozmasin
    attempts         int NOT NULL DEFAULT 0,  -- necha marta urinildi
    last_error       text,                    -- oxirgi TEXNIK xato
    retry_after      timestamptz,             -- backoff — shu vaqtgacha tegilmaydi
    dead_at          timestamptz,             -- urinishlar tugadi, ODAM aralashuvi kerak
    dead_reason      text,
    template_file_id text,                    -- qaysi etalonga solishtirildi
    role             text,                    -- aniqlangan rol
    validated_at     timestamptz,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now()
);

-- Ishga tayyor qatorlarni tez topish uchun
CREATE INDEX IF NOT EXISTS ix_js_tayyor
    ON jobs_state (created_at)
    WHERE dead_at IS NULL;

CREATE INDEX IF NOT EXISTS ix_js_olik
    ON jobs_state (dead_at)
    WHERE dead_at IS NOT NULL;


-- 2) Audit izi — har tekshiruvning to'liq natijasi
CREATE TABLE IF NOT EXISTS jobs_validation_log (
    id         bigserial PRIMARY KEY,
    uuid       text,
    bidder_id  bigint,                        -- ⚠️ nomi TARIXIY: `tender_id` sxemasida
                                              -- bu ustunga aslida `files.file_id` yoziladi
                                              -- (guruh ustuni `_jobs_ustunlarini_aniqla`
                                              -- da `bidder_id` → `file_id` tartibida
                                              -- tanlanadi). Nom o'zgartirilmaydi —
                                              -- mavjud audit yozuvlari bilan mos qolsin.
    type       text,
    file       text,
    role       text,
    status     smallint,
    findings   jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_jvl_uuid ON jobs_validation_log (uuid);


-- ============================================================================
-- 3) CHIQUVCHI NAVBAT (outbox) — tenderga yuboriladigan xabarlar
--
-- BITTA QATOR = BITTA XABAR (bitta fayl emas!).
--
-- Nega alohida jadval, `files` dagi ustunlar emas: bir faylga ketma-ket IKKI
-- xil xabar kerak bo'lishi mumkin — avval «texnik muammo» (status=0, fayl
-- o'lganda), keyin (qayta tekshirilgach) haqiqiy verdikt. `files` da bitta
-- yetkazish holati bo'lsa, birinchi xabar yuborilgach ikkinchisi HECH QACHON
-- yetkazilmasdi.
--
-- Qo'shimcha foyda: ishtirokchi AYNAN nimani o'qigani (`comment`) va QACHON
-- (`yuborilgan_at`) bazada qoladi — huquqiy nizoda yagona kerak bo'ladigan dalil.
-- ============================================================================
CREATE TABLE IF NOT EXISTS yuborish_navbati (
    id            bigserial PRIMARY KEY,
    fayl_id       bigint      NOT NULL,       -- files.id (FK QO'YILMAYDI — §jobs_state bilan bir xil sabab)
    file_id       bigint      NOT NULL,       -- tenderga yuboriladigan kalit
    status        smallint    NOT NULL,       -- payload'dagi qiymat: 0/1/2/3
    comment       text        NOT NULL,       -- payload'dagi matn, AYNAN
    sabab         varchar(64) NOT NULL,       -- 'verdikt' | 'texnik'
    holat         smallint    NOT NULL DEFAULT 0,  -- 0 navbatda, 1 xato, 2 yuborildi, 3 tashlandi
    urinish       smallint    NOT NULL DEFAULT 0,
    band_until    timestamptz,
    yuborilgan_at timestamptz,
    xato          text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT yn_holat_chk   CHECK (holat   IN (0,1,2,3)),
    CONSTRAINT yn_urinish_chk CHECK (urinish BETWEEN 0 AND 3),
    CONSTRAINT yn_status_chk  CHECK (status  IN (0,1,2,3,4)),   -- 4: texnik, taslim
    CONSTRAINT yn_sabab_chk   CHECK (sabab   IN ('verdikt','texnik'))
);

-- Mavjud jadvalda `status` cheklovi 4 ga kengaysin (2026-09-23; idempotent).
ALTER TABLE yuborish_navbati DROP CONSTRAINT IF EXISTS yn_status_chk;
ALTER TABLE yuborish_navbati ADD CONSTRAINT yn_status_chk CHECK (status IN (0,1,2,3,4));

-- Cron shu indeks bilan navbatni oladi.
CREATE INDEX IF NOT EXISTS yn_navbat_idx
    ON yuborish_navbati (holat, band_until, id)
    WHERE holat IN (0,1);

-- Takror yozuvni TO'SADI: bir fayl uchun AYNAN bir xil (status, comment) ikki
-- marta navbatga tushmaydi. YANGI verdikt (boshqa matn) bemalol qo'shiladi —
-- A2 («verdikt qayta yuborilishi mumkin») aynan shu orqali ishlaydi.
CREATE UNIQUE INDEX IF NOT EXISTS yn_takror_idx
    ON yuborish_navbati (fayl_id, status, md5(comment));

-- Texnik xabar yozishda «shu o'lim uchun allaqachon yozilganmi» tekshiruvi.
CREATE INDEX IF NOT EXISTS yn_texnik_idx
    ON yuborish_navbati (fayl_id, created_at)
    WHERE sabab = 'texnik';


-- ============================================================================
-- 4) FAQAT TEST/DEV UCHUN: `jobs` jadvalining namunasi
--
-- ASOSIY JADVAL BU YERDA YARATILMAYDI!
--
-- Ishlab chiqarishda uni Laravel (AiManager) migratsiyasi yaratadi: `files`.
-- Avval bu yerda sinov uchun `jobs` jadvali yaratilardi, lekin u XAVFLI edi:
-- Laravel'ning O'Z `jobs` jadvali (navbat uchun) bilan nom to'qnashadi va
-- `CREATE INDEX ... WHERE status = 0` xatoga olib keladi.
--
-- Sinov uchun jadval kerak bo'lsa `tender_schema.sql` dan foydalaning
-- (u `tender` nomli alohida jadval yaratadi).
-- ============================================================================

-- ─── 3) validation_evidence (tender_engine/evidence.py: DDL + MIGRATSIYA) ─────
CREATE TABLE IF NOT EXISTS validation_evidence (
    id              BIGSERIAL PRIMARY KEY,
    fayl_id         TEXT,                -- o'sha paytdagi files.id (ma'lumot uchun)
    link            TEXT NOT NULL,
    tender_id       BIGINT,
    type            TEXT,
    fayl_hash       TEXT NOT NULL,       -- sha256 — asosiy bog'lanish
    etalon_tpl_id   BIGINT,
    etalon_hash     TEXT,
    rol             TEXT,
    ichki_status    TEXT NOT NULL CONSTRAINT validation_evidence_ichki_status_check
        CHECK (ichki_status IN
        ('ACCEPT_FILLED','REJECT_NO_PRICE','STRUCTURE_VIOLATION',
         'TECHNICAL_ERROR','REVIEW_AMBIGUOUS','IDENTICAL_COPY')),
    tashqi_status   SMALLINT NOT NULL CONSTRAINT validation_evidence_tashqi_status_check
        CHECK (tashqi_status IN (0, 1, 2, 3)),
    sinf            TEXT,                -- 'review' — REVIEW->1 izi (nizo dalili)
    kodlar          JSONB NOT NULL DEFAULT '[]',
    notelar         JSONB NOT NULL DEFAULT '[]',
    comment_uz      TEXT,
    varaq_dalil     JSONB,               -- per-jadval: bandlar, narxlangan, ustun
    validator_version TEXT NOT NULL,
    yaratildi       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ve_hash_idx   ON validation_evidence (fayl_hash);
CREATE INDEX IF NOT EXISTS ve_tender_idx ON validation_evidence (tender_id, type);
CREATE INDEX IF NOT EXISTS ve_review_idx ON validation_evidence (yaratildi)
    WHERE sinf = 'review';

-- APPEND-ONLY kafolati DB darajasida (audit jadvali o'zgarmas bo'lsin).
-- Kod hech qayerda UPDATE/DELETE qilmaydi, lekin bu faqat konvensiya edi —
-- GRANT ALL berilgan rol (yoki xato skript) yozuvni o'zgartira olardi.
-- Trigger buni jismonan taqiqlaydi; idempotent (CREATE OR REPLACE, PG14+).
CREATE OR REPLACE FUNCTION ve_ozgartirish_taqiqi() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'validation_evidence append-only: % taqiqlangan', TG_OP;
END $$ LANGUAGE plpgsql;

CREATE OR REPLACE TRIGGER ve_append_only
    BEFORE UPDATE OR DELETE ON validation_evidence
    FOR EACH ROW EXECUTE FUNCTION ve_ozgartirish_taqiqi();
ALTER TABLE validation_evidence
    DROP CONSTRAINT IF EXISTS validation_evidence_ichki_status_check;
ALTER TABLE validation_evidence
    ADD CONSTRAINT validation_evidence_ichki_status_check CHECK (ichki_status IN
        ('ACCEPT_FILLED','REJECT_NO_PRICE','STRUCTURE_VIOLATION',
         'TECHNICAL_ERROR','REVIEW_AMBIGUOUS','IDENTICAL_COPY'));
ALTER TABLE validation_evidence
    DROP CONSTRAINT IF EXISTS validation_evidence_tashqi_status_check;
ALTER TABLE validation_evidence
    ADD CONSTRAINT validation_evidence_tashqi_status_check
        CHECK (tashqi_status IN (0, 1, 2, 3));
