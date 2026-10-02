-- ============================================================================
-- huquqlar.sql — ilova roli uchun MINIMAL huquqlar (API, worker, yuboruvchi)
--
-- `python -m app.db.migrate` har safar qo'llaydi (idempotent): {rol} —
-- APP_DB_USER, {baza} — joriy baza. Rolning o'zini (LOGIN, parol,
-- NOSUPERUSER ...) migrate.py yaratadi/yangilaydi.
--
-- Nega kerak (2026-09-14 ko'rigi): worker SUPERUSER bilan ulangan edi —
--   1. `validation_evidence` dagi append-only trigger — verdikt o'zgarmasligining
--      ASOSIY dalili — superuser uni chetlab o'ta oladi;
--   2. kodimizdagi xato istalgan jadvalni o'zgartira/o'chira olardi.
-- Jadvallar EGASI — migratsiya roli (POSTGRES_USER); ilova roli egasi EMAS,
-- shuning uchun `ALTER TABLE ... DISABLE TRIGGER` ham qila olmaydi.
--
-- Ro'yxat KODDAN kelib chiqqan — ortiqcha bironta huquq yo'q. YANGI jadval
-- (yangi migratsiya) qo'shilsa, huquqini SHU YERGA qo'shing.
-- ============================================================================

GRANT CONNECT ON DATABASE {baza} TO {rol};
GRANT USAGE   ON SCHEMA public   TO {rol};

-- ── files — verdikt (worker) + yangi qator va havola (kiruvchi API) ─────────
-- O'qish: queue.ish_ol / kashf_qil / olik_belgilash, korish, kuzatuv
GRANT SELECT ON files TO {rol};
-- Worker: verdict.natijani_yoz, queue.olik_belgilash / requeue /
--         shablon_kelganini_tekshir  →  status, comment, updated_at
-- API:    store.elementni_yoz  →  INSERT; havola o'zgarsa link
GRANT UPDATE (status, comment, updated_at) ON files TO {rol};
GRANT INSERT ON files TO {rol};
GRANT UPDATE (link) ON files TO {rol};
GRANT USAGE ON SEQUENCE files_id_seq TO {rol};

-- ── templates — «tekshirildi» belgisi (worker) + yangi qator (API) ──────────
GRANT SELECT ON templates TO {rol};
GRANT UPDATE (status, updated_at) ON templates TO {rol};
GRANT INSERT ON templates TO {rol};
GRANT UPDATE (link) ON templates TO {rol};
GRANT USAGE ON SEQUENCE templates_id_seq TO {rol};

-- ── jobs_state — worker xizmat holati ───────────────────────────────────────
-- `FOR UPDATE OF s SKIP LOCKED` ham UPDATE huquqini talab qiladi.
-- DELETE ATAYLAB BERILMAYDI: ishlab chiqarish oqimi hech qachon o'chirmaydi.
GRANT SELECT, INSERT, UPDATE ON jobs_state TO {rol};

-- ── jobs_validation_log — APPEND-ONLY ──────────────────────────────────────
GRANT SELECT, INSERT ON jobs_validation_log TO {rol};
GRANT USAGE ON SEQUENCE jobs_validation_log_id_seq TO {rol};

-- ── validation_evidence — APPEND-ONLY (audit izi) ─────────────────────────
-- UPDATE/DELETE BERILMAYDI — trigger ham taqiqlaydi (ikki qavat himoya).
GRANT SELECT, INSERT ON validation_evidence TO {rol};
GRANT USAGE ON SEQUENCE validation_evidence_id_seq TO {rol};

-- ── yuborish_navbati — chiquvchi navbat (outbox) ───────────────────────────
-- `FOR UPDATE SKIP LOCKED` JADVAL-darajali UPDATE talab qiladi.
-- DELETE BERILMAYDI — yuborilgan xabar TARIX (ishtirokchi nimani o'qigani).
GRANT SELECT, INSERT, UPDATE ON yuborish_navbati TO {rol};
GRANT USAGE ON SEQUENCE yuborish_navbati_id_seq TO {rol};

-- ── sorov_jurnali — so'rov jurnali, APPEND-ONLY (0002) ─────────────────────
-- Yozish: app/jurnal.py (yozuvchi oqim; `--requeue` / `--qayta-och` amallari)
-- O'qish: GET /jurnal (app/api/jurnal.py)
-- UPDATE/DELETE BERILMAYDI (jobs_validation_log kabi, triggersiz): muddati
-- o'tgan yozuvlarni faqat jadval EGASI o'chiradi — DEPLOY.md, 6-bo'lim.
-- DIQQAT: bu fayl `format()` dan o'tadi — izohda ham jingalak qavs yozmang.
GRANT SELECT, INSERT ON sorov_jurnali TO {rol};
GRANT USAGE ON SEQUENCE sorov_jurnali_id_seq TO {rol};

-- ── schema_migrations — faqat o'qish: xizmatlar ishga tushganda sxema
--    versiyasini tekshiradi (`app/db/schema.tekshir`).
GRANT SELECT ON schema_migrations TO {rol};
