# Spec: Refactor to Screaming Architecture (Consolidated)

**Change**: refactor-screaming-architecture
**Version**: 2.1 — reconciled with design v3 (lead_level→debtor_level, sorelia_debtors, column drops, enum remap, atomic deploy)
**Contract**: All existing tests MUST stay green after every slice. Gate: `uv run pytest tests/ -v`.

---

## Purpose

Four-dimension change applied in a single coordinated refactor:
1. **Structural** — screaming-architecture layout (feature-first, thin api, shared kernel, tenancy).
2. **Dead-code removal** — delete `opportunity_detector.py` and its wiring.
3. **Full domain rename** — `lead` → `debtor` across code, tool contract, and storage layers.
4. **Data migration (prod-safe)** — PostgreSQL column + table renames with dual-read backward-compat.

The `sorelia_conversations.debtor_data` column addition is additive (dual-read safe). The `sorelia_leads→sorelia_debtors` table rename, `lead_level→debtor_level` column rename, enum value remap, and column drops are NOT dual-read-safe — they ship as one atomic deploy in slice 9 (code + migration + dashboard SQL together). Brief dashboard-only 500s during the deploy window are the accepted tradeoff; no chat hot-path is affected.

---

## Dimension 1 — Structural Reorganization

### Requirement: Five-Feature Layout

After all structural slices the codebase MUST contain exactly five top-level features and NO `leads` feature directory.

| Feature | Contents |
|---|---|
| `conversation` | debtor_state, debtor_profile, skills/, persistence/ (visitor_memory + redis_store), agent logic |
| `cobranza` | cobranza tools (L1–707 of current cobranza.py) |
| `comprobantes` | comprobantes tools (L708–870 of current cobranza.py) + certificate_pdf |
| `messaging` | whatsapp + chathub (single feature, NOT split) |
| `analytics` | analytics modules |

#### Scenario: Feature directory audit passes

- GIVEN all structural slices are applied
- WHEN `ls apps/agent/features/` is run
- THEN exactly these directories exist: `conversation/`, `cobranza/`, `comprobantes/`, `messaging/`, `analytics/`
- AND `features/leads/` does NOT exist

#### Scenario: comprobantes not nested under cobranza

- GIVEN the features directory is populated
- WHEN `features/cobranza/` is inspected
- THEN no `comprobantes` module or subdirectory is present within it

#### Scenario: messaging contains both channels

- GIVEN the messaging slice is applied
- WHEN `features/messaging/` is inspected
- THEN both WhatsApp and ChatHub modules are present within it

---

### Requirement: Shared Kernel and Tenancy Layout

The shared kernel MUST be named `shared/` (not `kernel/`, `platform/`) and contain: `llm`, `persistence` (pure), `rate_limit`, `webhooks`, `config`. The tenancy layer MUST reside in `tenancy/` containing: `tenant_loader`, `soul`, `pricing`, `responses_spec`.

#### Scenario: Kernel directory named correctly

- GIVEN shared modules are moved
- WHEN imports are resolved
- THEN all shared imports reference `shared.X` (not `core.X` or `kernel.X`)

---

### Requirement: Dependency Rules Enforced

After all slices the following rules MUST hold and MUST be verifiable by a static check:

- Features MAY import from `shared/` and `tenancy/` only; they MUST NOT import each other directly.
- `shared/` MUST NOT import any feature module.
- `api/` MUST only orchestrate (import features + shared; no business logic).

#### Scenario: Cross-feature direct import detected

- GIVEN the final structure is in place
- WHEN a static import check is run
- THEN any direct import from one feature into another is flagged as a violation

#### Scenario: Shared imports a feature

- GIVEN the final structure is in place
- WHEN a static import check is run
- THEN any import of a feature module from within `shared/` is flagged as a violation

---

### Requirement: `api/main.py` Size Reduction

After the final slice `api/main.py` MUST be ≤ 150 lines. Business logic MUST move to features; routing logic MUST move to `api/routers/`.

#### Scenario: Line count verified after split

- GIVEN the api-thin slice (slice 10) has been applied
- WHEN `wc -l apps/agent/api/main.py` is run
- THEN the result is ≤ 150

---

### Requirement: `core/` Fully Dissolved

After all slices the `core/` directory MUST NOT exist.

#### Scenario: core/ directory absent after final slice

- GIVEN all slices have been applied
- WHEN the filesystem is inspected
- THEN `apps/agent/core/` does not exist

---

### Requirement: Git History Preservation

Every file move MUST use `git mv`. Copy-delete sequences that break `git log --follow` are prohibited.

#### Scenario: Moved file retains history

- GIVEN a module is relocated from `core/X.py` to `features/Y/X.py`
- WHEN `git log --follow features/Y/X.py` is run
- THEN the full pre-move commit history is shown

---

### Requirement: Slice Order Outside-In

Slices MUST be applied in this order to minimize broken-import windows:

0. Scaffold (`features/`, `shared/`, `tenancy/` directories + `__init__.py` stubs)
1. `shared/` kernel
2. `tenancy/`
3. `analytics` feature
4. `comprobantes` + shared delivery
5. `messaging` feature
6. `cobranza` feature
7. `conversation` feature (absorbs lead_machine → debtor_state, prospect_profile → debtor_profile, visitor_memory → persistence/)
8. Rename — code + tool contract (no storage touch)
9. Storage + migration (riskiest, preflight-gated, separate PR)
10. `api/main.py` thin split (final slice)

#### Scenario: Slice 10 attempted before conversation is moved

- GIVEN slices 0–9 are not yet all applied
- WHEN a contributor attempts to apply slice 10 (api thin)
- THEN the slice is blocked — api split MUST be the final slice

---

### Requirement: Characterization Tests Before God-File Splits

Where `api/main.py` (1905 lines) and `tools/cobranza.py` (870 lines) have coverage gaps, characterization tests MUST be committed BEFORE the split slice begins.

#### Scenario: Coverage gap detected pre-split

- GIVEN a god file is about to be split
- AND coverage analysis reveals untested branches
- WHEN the split slice is planned
- THEN characterization tests covering those branches MUST be committed first, suite green, before splitting proceeds

---

### Requirement: Per-Slice Rollback Safety

Each slice MUST be an atomic commit or small set of commits within one PR. `git revert <sha>` MUST restore the prior green state without affecting other committed slices.

#### Scenario: Slice revert

- GIVEN slice N is committed and suite is green
- WHEN `git revert <sha-of-slice-N>` is executed
- THEN suite returns to pre-slice-N green state without affecting slices 1..N-1

---

### Requirement: Import Re-Mapping (Tests Included)

All 18 test files MUST have import paths updated to new module locations. Test logic MUST NOT change — only `from core.X import Y` → `from features.Z.X import Y` (or `shared.X`) re-maps are permitted.

#### Scenario: Test imports re-mapped without logic change

- GIVEN a feature module has been moved
- WHEN the corresponding test file is updated
- THEN only import statements change; no assertion, fixture, or mock logic is modified

---

## Dimension 2 — Dead Code Removal

### Requirement: opportunity_detector.py Deleted

`apps/agent/core/opportunity_detector.py` MUST be deleted. The hardcoded `opportunities: []` field in `tools/__init__.py:138` MUST be removed. The `opportunities` prompt-render in `prompts/system.py` (lines 178, 184) MUST be removed. This is a zero-behavior change — no test currently asserts the presence of this field.

#### Scenario: opportunity_detector deleted with no test regression

- GIVEN slice 7 (conversation feature move) is applied and opportunity_detector is deleted
- WHEN `uv run pytest tests/ -v` is run
- THEN all tests pass — no test references or asserts the opportunities field

#### Scenario: Dead wiring removed from tools/__init__ and system.py

- GIVEN the deletion slice is applied
- WHEN `grep -r "opportunities" apps/agent/` is run
- THEN zero occurrences remain in `tools/__init__.py`, `prompts/system.py`, or `opportunity_detector.py`

---

## Dimension 3 — Full Domain Rename: lead → debtor

### Requirement: Code-Layer Rename (Symbols)

All domain symbols MUST be renamed:

| Old | New |
|---|---|
| `lead_state` | `debtor_state` |
| `class LeadMachine` | `DebtorState` |
| `lead_machine.py` | `debtor_state.py` |
| `prospect_profile.py` / `build_prospect_profile` | `debtor_profile.py` / `build_debtor_profile` |
| `self.lead` | `self.debtor` |
| `lead_data` parameter | `debtor_data` (code layer; storage rename is Dimension 4) |
| `lead_level` (code constants, params, column references in persistence/state) | `debtor_level` |
| `_get_lead_status` / `get_lead_status` | `_get_debtor_status` / `get_debtor_status` |

EXCLUDED from rename (these are NOT debtor-domain — MUST remain verbatim): `settings.webhook_lead_url`, `webhook_config.lead_transition_url`, `website_leads_only` mode (settings.py, main.py).

#### Scenario: Excluded identifiers unchanged

- GIVEN slice 8 (code rename) is applied
- WHEN `grep -rn "webhook_lead_url\|lead_transition_url\|website_leads_only" apps/agent/` is run
- THEN all three identifiers remain exactly as-is

#### Scenario: Domain symbols renamed throughout

- GIVEN slice 8 is applied
- WHEN `grep -rn "LeadMachine\|lead_state\|prospect_profile\|self\.lead[^_]" apps/agent/` is run
- THEN zero matches are found (excluding the excluded identifiers above)

---

### Requirement: Tool Contract Rename (LLM-facing)

The tool name `get_lead_status` MUST be renamed `get_debtor_status` in: `tools_schema.py` (name, description, docstring), `tools/__init__.py` (dispatch table + handler function), `SKILL.md`, and all prompt files referencing the old name. A test MUST be added that asserts the tool schema name is `get_debtor_status` (locks the LLM contract).

#### Scenario: Tool schema reports new name

- GIVEN slice 8 is applied
- WHEN the tool registry or schema is inspected
- THEN the tool name is `get_debtor_status` and `get_lead_status` does NOT appear in tool definitions

#### Scenario: LLM contract test asserts new tool name

- GIVEN the rename slice includes a new test
- WHEN `uv run pytest tests/ -v` is run
- THEN a test explicitly asserts `tool.name == "get_debtor_status"` (or equivalent schema check)

---

## Dimension 4 — Data Migration (Prod-Safe)

### Requirement: sorelia_conversations.lead_data Column Rename (Additive / Dual-Read)

The column `lead_data` in `sorelia_conversations` MUST be renamed to `debtor_data`. The migration MUST use `ADD COLUMN IF NOT EXISTS debtor_data` + a one-shot idempotent `UPDATE` + retain `lead_data` until a future cleanup release. Dual-read logic MUST be present: read `debtor_data`, fall back to `lead_data` if null, write only to `debtor_data`. This operation is additive and is the ONLY part of slice 9 that is dual-read-safe. This migration is DESTRUCTIVE and MUST be preceded by a preflight block at apply time (rows affected, idempotency proof, rollback SQL).

#### Scenario: Dual-read backward-compat during migration window

- GIVEN slice 9 migration is applied and `lead_data` column still exists
- WHEN a conversation record with only `lead_data` populated is read
- THEN the system reads `debtor_data` first, falls back to `lead_data`, and returns correct data

#### Scenario: New writes go to debtor_data only

- GIVEN slice 9 is applied
- WHEN a conversation state is persisted
- THEN only `debtor_data` is written; `lead_data` is not updated

#### Scenario: Migration is idempotent

- GIVEN slice 9 migration SQL is run a second time
- WHEN the migration executes
- THEN no error is raised and no data is duplicated or corrupted

#### Scenario: Rollback path documented at apply

- GIVEN slice 9 is about to be applied
- WHEN the preflight block is presented
- THEN it includes: exact row count, idempotency proof, and rollback SQL (`DROP COLUMN debtor_data`)

---

### Requirement: Dashboard Table and Column Rename — Atomic Deploy

The table `sorelia_leads` MUST be renamed to `sorelia_debtors`. The function `upsert_lead` in `api/dashboard.py` MUST be renamed to `upsert_debtor`. The column `lead_level` in both `sorelia_leads` (to be renamed `sorelia_debtors`) and `sorelia_conversations` MUST be renamed to `debtor_level`. These renames use `ALTER TABLE RENAME` and `ALTER TABLE RENAME COLUMN`, which are NOT dual-read-safe — old code breaks immediately. Therefore slice 9 MUST ship code + migration + dashboard SQL as ONE atomic deploy. A brief window of dashboard-only 500s during the deploy window is the accepted tradeoff (no chat hot-path impact).

The dashboard rename MUST NOT ship until a human has confirmed that no out-of-repo ETL (e.g. a scheduled PG→BigQuery job) reads `{schema}.sorelia_leads` directly. This is a hard gate — the preflight block MUST include this confirmation.

EXCLUDED from rename: `settings.webhook_lead_url`, `webhook_config.lead_transition_url`, `website_leads_only` mode — these are NOT debtor-domain and MUST remain unchanged.

#### Scenario: External ETL human-confirmation gate

- GIVEN slice 9 is about to be applied
- WHEN the preflight block is presented
- THEN it includes a checkbox item: "Confirmed: no out-of-repo ETL reads `{schema}.sorelia_leads` from Postgres" with explicit human sign-off
- AND the migration MUST NOT run without that confirmation

#### Scenario: Consumer audit documented in preflight

- GIVEN slice 9 begins
- WHEN the preflight block is presented
- THEN it documents the internal consumer mapping: `api/dashboard.py` (5 SQL sites), no Doris/BigQuery carry of `sorelia_leads` columns, and the result of the external ETL check

#### Scenario: Atomic deploy — code, migration, and dashboard SQL land together

- GIVEN the slice 9 PR is merged and deployed
- WHEN the deploy completes
- THEN `sorelia_debtors` table exists, `sorelia_leads` does not, `debtor_level` column exists, `lead_level` does not, and `api/dashboard.py` queries reference `sorelia_debtors` and `debtor_level`

---

### Requirement: debtor_level Enum Value Remap

The `lead_level` column values MUST be remapped as follows:

| Old value | New value |
|---|---|
| `PRE_LEAD` | `PRE_DEBTOR` |
| `LEAD` | `DEBTOR` |
| `LEAD_ENRICHED` | `DEBTOR_VERIFIED` |
| `VISITOR` | `VISITOR` (unchanged) |

The state machine constants `_CONTACT_LEVELS` MUST be updated to `{"DEBTOR", "DEBTOR_VERIFIED"}`. Dead dashboard filter values `CONTACT` and `QUALIFIED` MUST be removed — the state machine has never emitted them (always counted 0; zero-behavior removal).

The remap MUST be applied to both `sorelia_debtors.debtor_level` and `sorelia_conversations.debtor_level` via idempotent `UPDATE ... SET debtor_level = CASE ... END` statements in the migration script.

#### Scenario: Enum values remapped in both tables

- GIVEN slice 9 migration is applied
- WHEN `SELECT DISTINCT debtor_level FROM sorelia_debtors` is run
- THEN no row contains `PRE_LEAD`, `LEAD`, or `LEAD_ENRICHED`; values are from {`PRE_DEBTOR`, `DEBTOR`, `DEBTOR_VERIFIED`, `VISITOR`}

#### Scenario: Dead funnel filters removed from dashboard

- GIVEN slice 9 is applied
- WHEN `grep -n "CONTACT\|QUALIFIED" features/analytics/dashboard.py` is run (post-move)
- THEN zero matches are found (the dead filters are gone)

#### Scenario: State machine constants updated

- GIVEN slice 8 (code rename) is applied
- WHEN `_CONTACT_LEVELS` is inspected in `features/conversation/debtor_state.py`
- THEN its value is `{"DEBTOR", "DEBTOR_VERIFIED"}`

---

### Requirement: Real-Estate Column Drops

The columns `district_interest`, `purpose`, and `budget` MUST be dropped from `sorelia_debtors`. These columns are confirmed not read by `api/dashboard.py` or any internal sink. The column `project_interest` MUST NOT be dropped — it is live in `/stats top_projects` and `/conversations` in `api/dashboard.py`.

Before the DROP, a `pg_dump` of the three columns MUST be taken as a backup. The preflight block MUST show `SELECT COUNT(*) WHERE district_interest IS NOT NULL` (and equivalent for the others) for each active tenant schema. Drop proceeds only after human "go".

#### Scenario: project_interest preserved after migration

- GIVEN slice 9 is applied
- WHEN `\d sorelia_debtors` is run in psql
- THEN `project_interest` column is present and `district_interest`, `purpose`, `budget` are absent

#### Scenario: pg_dump backup taken before DROP

- GIVEN slice 9 preflight is presented
- WHEN the preflight block is shown
- THEN it includes a `pg_dump` command covering the three columns for each active schema, to be run BEFORE the DROP statements execute

#### Scenario: Non-null count check in preflight

- GIVEN the apply phase for slice 9
- WHEN the preflight block is presented
- THEN it shows `SELECT COUNT(*) FROM sorelia_leads WHERE district_interest IS NOT NULL` (and equivalent for purpose, budget) per tenant schema

---

### Requirement: Redis Key Self-Expiry (No Migration Required)

Redis keys `sorelia:conv:{id}:lead_data` have a 24-hour TTL. No active migration is required — the dual-read fallback covers the overlap window. After 24 hours all stale keys are self-expired.

#### Scenario: Redis overlap handled by TTL

- GIVEN slice 9 is applied and old Redis keys still exist
- WHEN a key `sorelia:conv:{id}:lead_data` is encountered
- THEN the code reads the new key pattern first, falls back to old pattern, and the old key expires within 24 hours without manual intervention

---

## Cross-Cutting Requirements

### Requirement: Test-Green Gate on Every Slice

`uv run pytest tests/ -v` MUST pass after every individual slice is applied and committed. No slice MAY be merged with a failing test.

#### Scenario: Slice committed green

- GIVEN any slice is applied
- WHEN `uv run pytest tests/ -v` executes
- THEN all tests pass with zero failures and zero errors

#### Scenario: Slice breaks a test

- GIVEN a slice is applied and a test fails
- THEN the slice MUST NOT be committed; imports are fixed before merge, not test logic

---

### Requirement: Slices 9 and 10 in Separate Chained PRs

Slice 9 (storage migration, ~400+ line risk) and slice 10 (api thin split, ~400+ line risk) MUST each be delivered as separate PRs chained off the prior slice's merge. Neither MAY be combined with other slices.

#### Scenario: Slice 9 PR boundary enforced

- GIVEN slice 8 (code+tool rename) is merged
- WHEN slice 9 (storage migration) is submitted
- THEN it is its own PR targeting the post-slice-8 branch, with preflight block visible in PR description

---

### Requirement: Zero Observable Behavior Change (Structural + Rename Slices)

For slices 0–8, no observable behavior change is permitted. API contracts, response shapes, tool outputs, tenant routing, and integration behaviors MUST be identical before and after.

#### Scenario: Full suite passes without test logic changes (slices 0–8)

- GIVEN slices 0–8 are applied
- WHEN `uv run pytest tests/ -v` is run
- THEN all 18 test files pass with the same assertions as before the refactor (only import paths differ)
