-- =============================================================================
-- MIGRATION: Rename sorelia_leads → sorelia_debtors, lead_* → debtor_* columns
-- Enum value remap: PRE_LEAD→PRE_DEBTOR, LEAD→DEBTOR, LEAD_ENRICHED→DEBTOR_VERIFIED
-- Drop dead columns: district_interest, purpose, budget
-- Add dual-read column: debtor_data (additive, backward-compat with lead_data)
-- =============================================================================
--
-- PREFLIGHT — MANDATORY. Run this block manually BEFORE executing Section 1.
-- -------------------------------------------------------------------------
-- 1. ROWS AFFECTED: for each active tenant schema run:
--
--    SELECT COUNT(*) FROM {schema}.sorelia_leads;           -- rows to RENAME
--    SELECT COUNT(*) FROM {schema}.sorelia_conversations;   -- rows for debtor_data + debtor_level rename
--    SELECT COUNT(*) FROM {schema}.sorelia_conversations WHERE lead_data IS NOT NULL;  -- rows to backfill
--    SELECT COUNT(*) FROM {schema}.sorelia_conversations WHERE debtor_data IS NOT NULL; -- already migrated
--
-- 2. IDEMPOTENCY: Every DDL statement uses IF EXISTS / IF NOT EXISTS / CASE
--    guards so re-running this script is a no-op after first successful run.
--
-- 3. ROLLBACK PLAN (reverse migration — run ONLY if you need to revert):
--    See Section 4 at the bottom of this file.
--    NOTE: district_interest, purpose, budget can only be restored from pg_dump.
--
-- 4. pg_dump BEFORE DROP (UNRECOVERABLE after DROP):
--    Run BEFORE executing Section 2 (DROP COLUMN block):
--
--    pg_dump \
--      --host=<DB_HOST> --port=5432 --username=<DB_USER> \
--      --dbname=<DB_NAME> \
--      --table=<schema>.sorelia_leads \
--      --column-inserts \
--      --no-acl --no-owner \
--      > /tmp/sorelia_leads_backup_$(date +%Y%m%d_%H%M%S).sql
--
--    Repeat for each active tenant schema (replace <schema>).
--    Verify the dump before proceeding: wc -l /tmp/sorelia_leads_backup_*.sql
--
-- 5. HUMAN CONFIRMATION GATE — REQUIRED before running this migration:
--    [ ] Confirmed: NO out-of-repo ETL (Doris, BigQuery, external pipelines)
--        reads {schema}.sorelia_leads directly from Postgres.
--        (Dashboard reads are inside this repo via api/dashboard.py — already updated.)
--        Contact: Angeles / Paola to confirm no external BQ pull on sorelia_leads.
--    [ ] pg_dump of district_interest, purpose, budget completed and verified.
--    [ ] Code deployment (PR5) is staged and ready to go live atomically with this script.
--    [ ] Maintenance window communicated: dashboard endpoint will return 500 briefly
--        during the RENAME step. Chat hot-path is NOT affected (no sorelia_leads in chat path).
--
-- DO NOT RUN without all checkboxes above confirmed. This migration is IRREVERSIBLE
-- for the dropped columns without the pg_dump backup.
-- =============================================================================
--
-- DEPLOY RUNBOOK (atomic deploy order):
-- 1. Run Section 0 (additive): ADD COLUMN debtor_data (backward-compat, no downtime).
-- 2. Backfill debtor_data from lead_data (no downtime — reads continue from lead_data).
-- 3. Deploy PR5 code to production (dual-read code writes debtor_data, reads both).
-- 4. Run Section 1 (atomic): RENAME TABLE, RENAME COLUMN, enum remap.
--    Dashboard will show 500 errors for ~5 seconds during RENAME. Chat is unaffected.
-- 5. Run Section 2 (DROP): Remove district_interest, purpose, budget.
--    Only after verifying Section 1 + code are both live and green.
-- 6. Smoke-test: /api/v1/dashboard/stats returns 200 with sorelia_debtors data.
-- =============================================================================


-- =============================================================================
-- Section 0: ADDITIVE (dual-read safe, zero-downtime — run before code deploy)
-- =============================================================================

-- Add debtor_data column alongside lead_data in sorelia_conversations.
-- This is the dual-read safe part: old code reads lead_data, new code writes
-- debtor_data and falls back to lead_data.
-- Run this while old code is still live — no downtime required.

DO $$
DECLARE
    schema_name TEXT;
BEGIN
    FOR schema_name IN
        SELECT schema_name
        FROM information_schema.schemata
        WHERE schema_name NOT IN ('pg_catalog', 'information_schema', 'public', 'pg_toast')
        AND schema_name NOT LIKE 'pg_%'
    LOOP
        -- Add debtor_data column if it does not exist yet
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = schema_name
              AND table_name = 'sorelia_conversations'
              AND column_name = 'debtor_data'
        ) THEN
            EXECUTE format(
                'ALTER TABLE %I.sorelia_conversations ADD COLUMN debtor_data JSONB DEFAULT ''{}''::jsonb',
                schema_name
            );
            RAISE NOTICE 'Added debtor_data to %.sorelia_conversations', schema_name;
        ELSE
            RAISE NOTICE 'debtor_data already exists in %.sorelia_conversations — skipping', schema_name;
        END IF;

        -- Backfill debtor_data from lead_data where debtor_data is still empty/null
        -- Safe to run multiple times (idempotent: only updates rows where debtor_data is empty)
        IF EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = schema_name
              AND table_name = 'sorelia_conversations'
              AND column_name = 'lead_data'
        ) THEN
            EXECUTE format(
                $sql$
                UPDATE %I.sorelia_conversations
                SET debtor_data = lead_data
                WHERE lead_data IS NOT NULL
                  AND lead_data != '{}'::jsonb
                  AND (debtor_data IS NULL OR debtor_data = '{}'::jsonb)
                $sql$,
                schema_name
            );
            RAISE NOTICE 'Backfilled debtor_data from lead_data in %.sorelia_conversations', schema_name;
        END IF;
    END LOOP;
END
$$;


-- =============================================================================
-- Section 1: ATOMIC (NOT dual-read safe — deploy code + this section together)
-- =============================================================================
-- Order: RENAME TABLE → RENAME COLUMN (both tables) → Enum value remap
-- The RENAME TABLE is the moment of 500s on the dashboard.

DO $$
DECLARE
    schema_name TEXT;
BEGIN
    FOR schema_name IN
        SELECT schema_name
        FROM information_schema.schemata
        WHERE schema_name NOT IN ('pg_catalog', 'information_schema', 'public', 'pg_toast')
        AND schema_name NOT LIKE 'pg_%'
    LOOP

        -- 1a. RENAME sorelia_leads → sorelia_debtors
        IF EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = schema_name AND table_name = 'sorelia_leads'
        ) THEN
            EXECUTE format('ALTER TABLE %I.sorelia_leads RENAME TO sorelia_debtors', schema_name);
            RAISE NOTICE 'Renamed %.sorelia_leads → %.sorelia_debtors', schema_name, schema_name;
        ELSE
            RAISE NOTICE '%.sorelia_leads does not exist (already renamed or missing) — skipping', schema_name;
        END IF;

        -- 1b. RENAME lead_level → debtor_level in sorelia_debtors
        IF EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = schema_name
              AND table_name = 'sorelia_debtors'
              AND column_name = 'lead_level'
        ) THEN
            EXECUTE format(
                'ALTER TABLE %I.sorelia_debtors RENAME COLUMN lead_level TO debtor_level',
                schema_name
            );
            RAISE NOTICE 'Renamed lead_level → debtor_level in %.sorelia_debtors', schema_name;
        ELSE
            RAISE NOTICE 'lead_level already renamed in %.sorelia_debtors — skipping', schema_name;
        END IF;

        -- 1c. RENAME lead_level → debtor_level in sorelia_conversations
        IF EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = schema_name
              AND table_name = 'sorelia_conversations'
              AND column_name = 'lead_level'
        ) THEN
            EXECUTE format(
                'ALTER TABLE %I.sorelia_conversations RENAME COLUMN lead_level TO debtor_level',
                schema_name
            );
            RAISE NOTICE 'Renamed lead_level → debtor_level in %.sorelia_conversations', schema_name;
        ELSE
            RAISE NOTICE 'lead_level already renamed in %.sorelia_conversations — skipping', schema_name;
        END IF;

        -- 1d. Enum value remap in sorelia_debtors.debtor_level
        --     PRE_LEAD → PRE_DEBTOR, LEAD → DEBTOR, LEAD_ENRICHED → DEBTOR_VERIFIED
        --     VISITOR stays unchanged.
        IF EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = schema_name
              AND table_name = 'sorelia_debtors'
              AND column_name = 'debtor_level'
        ) THEN
            EXECUTE format(
                $sql$
                UPDATE %I.sorelia_debtors
                SET debtor_level = CASE debtor_level
                    WHEN 'PRE_LEAD'       THEN 'PRE_DEBTOR'
                    WHEN 'LEAD'           THEN 'DEBTOR'
                    WHEN 'LEAD_ENRICHED'  THEN 'DEBTOR_VERIFIED'
                    ELSE debtor_level
                END
                WHERE debtor_level IN ('PRE_LEAD', 'LEAD', 'LEAD_ENRICHED')
                $sql$,
                schema_name
            );
            RAISE NOTICE 'Remapped enum values in %.sorelia_debtors.debtor_level', schema_name;
        END IF;

        -- 1e. Enum value remap in sorelia_conversations.debtor_level
        IF EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = schema_name
              AND table_name = 'sorelia_conversations'
              AND column_name = 'debtor_level'
        ) THEN
            EXECUTE format(
                $sql$
                UPDATE %I.sorelia_conversations
                SET debtor_level = CASE debtor_level
                    WHEN 'PRE_LEAD'       THEN 'PRE_DEBTOR'
                    WHEN 'LEAD'           THEN 'DEBTOR'
                    WHEN 'LEAD_ENRICHED'  THEN 'DEBTOR_VERIFIED'
                    ELSE debtor_level
                END
                WHERE debtor_level IN ('PRE_LEAD', 'LEAD', 'LEAD_ENRICHED')
                $sql$,
                schema_name
            );
            RAISE NOTICE 'Remapped enum values in %.sorelia_conversations.debtor_level', schema_name;
        END IF;

    END LOOP;
END
$$;


-- =============================================================================
-- Section 2: DROP dead columns (UNRECOVERABLE — pg_dump REQUIRED first)
-- =============================================================================
-- DROP district_interest, purpose, budget from sorelia_debtors.
-- project_interest is KEPT (LIVE: /stats top_projects endpoint reads it).
-- Run only AFTER Section 1 is verified live and green.

DO $$
DECLARE
    schema_name TEXT;
    col TEXT;
    dead_cols TEXT[] := ARRAY['district_interest', 'purpose', 'budget'];
BEGIN
    FOR schema_name IN
        SELECT schema_name
        FROM information_schema.schemata
        WHERE schema_name NOT IN ('pg_catalog', 'information_schema', 'public', 'pg_toast')
        AND schema_name NOT LIKE 'pg_%'
    LOOP
        FOREACH col IN ARRAY dead_cols LOOP
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = schema_name
                  AND table_name = 'sorelia_debtors'
                  AND column_name = col
            ) THEN
                EXECUTE format(
                    'ALTER TABLE %I.sorelia_debtors DROP COLUMN IF EXISTS %I',
                    schema_name, col
                );
                RAISE NOTICE 'Dropped %.sorelia_debtors.%', schema_name, col;
            ELSE
                RAISE NOTICE 'Column % already absent from %.sorelia_debtors — skipping', col, schema_name;
            END IF;
        END LOOP;
    END LOOP;
END
$$;


-- =============================================================================
-- Section 4: ROLLBACK / Reverse migration
-- =============================================================================
-- Run ONLY if you need to revert. Execute in reverse order:
-- Section 2 reverse: restore district_interest, purpose, budget from pg_dump.
--   psql -h <host> -U <user> -d <db> -f /tmp/sorelia_leads_backup_<date>.sql
--   (This re-inserts rows; manual ALTER + INSERT may be needed for column add.)
--
-- Section 1 reverse (per schema — replace <schema> with actual schema name):
--
-- UPDATE <schema>.sorelia_debtors
-- SET debtor_level = CASE debtor_level
--     WHEN 'PRE_DEBTOR'      THEN 'PRE_LEAD'
--     WHEN 'DEBTOR'          THEN 'LEAD'
--     WHEN 'DEBTOR_VERIFIED' THEN 'LEAD_ENRICHED'
--     ELSE debtor_level
-- END
-- WHERE debtor_level IN ('PRE_DEBTOR', 'DEBTOR', 'DEBTOR_VERIFIED');
--
-- UPDATE <schema>.sorelia_conversations
-- SET debtor_level = CASE debtor_level
--     WHEN 'PRE_DEBTOR'      THEN 'PRE_LEAD'
--     WHEN 'DEBTOR'          THEN 'LEAD'
--     WHEN 'DEBTOR_VERIFIED' THEN 'LEAD_ENRICHED'
--     ELSE debtor_level
-- END
-- WHERE debtor_level IN ('PRE_DEBTOR', 'DEBTOR', 'DEBTOR_VERIFIED');
--
-- ALTER TABLE <schema>.sorelia_conversations RENAME COLUMN debtor_level TO lead_level;
-- ALTER TABLE <schema>.sorelia_debtors RENAME COLUMN debtor_level TO lead_level;
-- ALTER TABLE <schema>.sorelia_debtors RENAME TO sorelia_leads;
--
-- Section 0 reverse (only needed if rolling back before code deploy):
-- ALTER TABLE <schema>.sorelia_conversations DROP COLUMN IF EXISTS debtor_data;
--
-- IMPORTANT: After rollback, also revert code to the pre-PR5 commit:
--   git revert <PR5-commit-hash>  (or git checkout refactor/screaming-arch-pr4-rename)
-- =============================================================================
