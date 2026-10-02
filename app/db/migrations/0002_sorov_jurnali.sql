-- ============================================================================
-- 0002_sorov_jurnali.sql — so'rov jurnali (`sorov_jurnali`)
--
-- Shu paytgacha DOIMIY qayd etilmagan narsalar uchun bitta jadval:
--   sorov   API ga kelgan HAR so'rov (yo'l, holat kodi, davomiylik, tanalar)
--   kirish  rad etilgan autentifikatsiya (401) — kim deb da'vo qilgani bilan
--   log     `tender` loggerining satrlari (api, worker, sender jarayonlari)
--   amal    operator amallari (`--requeue`, `--qayta-och`)
-- Verdikt dalili BU YERDA EMAS — u `validation_evidence` va
-- `jobs_validation_log` da (0001).
--
-- `yaratildi` — HODISA vaqti: yozuvchi oqim qatorlarni keyinroq, to'plam bilan
-- yozadi, shuning uchun INSERT uni HAR DOIM o'zi beradi. `DEFAULT now()` faqat
-- qo'lda yozilgan qator uchun zaxira.
--
-- Trigger ham, CHECK ham YO'Q — ataylab:
--   • jurnal yozuvi hech qachon rad etilmasin (yangi `tur` — yangi migratsiya
--     talab qilmaydi);
--   • append-only ilova roli uchun HUQUQLAR bilan ta'minlanadi (huquqlar.sql:
--     faqat SELECT, INSERT) — `jobs_validation_log` kabi. `ve_append_only`
--     singari trigger egasining muddati o'tgan yozuvlarni o'chirishini ham
--     to'sib qo'yardi (DEPLOY.md §6).
--
-- Yozadi: app/jurnal.py.  O'qiydi: GET /jurnal (app/api/jurnal.py).
-- ============================================================================

CREATE TABLE IF NOT EXISTS sorov_jurnali (
    id                 bigserial   PRIMARY KEY,
    yaratildi          timestamptz NOT NULL DEFAULT now(),  -- HODISA vaqti (yozilgan vaqt emas)
    tur                text        NOT NULL,                -- 'sorov' | 'kirish' | 'log' | 'amal'
    hodisa             text,        -- 'http', 'kirish_rad', 'log', 'requeue', 'qayta_och' ...
    daraja             text,        -- 'info' | 'warning' | 'error'
    manba              text        NOT NULL,                -- jarayon: 'api' | 'worker' | 'sender'
    sorov_id           text,        -- so'rov va uning davomida yozilgan log satrlarini bog'laydi
    usul               text,
    yol                text,
    sorov_qatori       text,
    holat_kodi         int,
    davomiylik_ms      int,
    ip                 text,
    login              text,        -- DA'VO qilingan login; tekshirilgani — `tasdiqlangan`
    tasdiqlangan       boolean,
    user_agent         text,
    sorov_sarlavhalari jsonb,       -- oq ro'yxatdagilar; Authorization dan faqat sxemasi
    sorov_tanasi       text,
    javob_tanasi       text,
    xabar              text,
    qoshimcha          jsonb
);

-- O'qish har doim vaqt oralig'i bilan (standart: oxirgi 24 soat).
CREATE INDEX IF NOT EXISTS sj_yaratildi_idx ON sorov_jurnali (yaratildi);
CREATE INDEX IF NOT EXISTS sj_tur_idx       ON sorov_jurnali (tur, yaratildi);
