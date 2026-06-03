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

### Requirement: Shared Kernel and Tenancy

`shared/` MUST contain:
- `llm/`: LLM orchestration and response generation
- `persistence/`: pure data access patterns (no feature logic)
- `rate_limit/`: rate limiting + backoff
- `webhooks/`: webhook dispatch primitives
- `config/`: configuration loading
- `ports/`: abstract Protocols for DI (ToolRegistryPort)

`tenancy/` MUST contain:
- `tenant_loader/`: multi-tenant routing
- `soul/`: identity verification state
- `pricing/`: tenant pricing logic
- `responses_spec/`: response shape contracts

### Requirement: Dependency Rules

MUST NOT appear in any import statement:
- features → api (zero matches)
- features → other features (zero cross-feature imports)
- shared → features (zero reverse imports)
- shared → api (zero matches)
- tenancy → features (zero reverse imports)

ALLOWED:
- features → shared (explicit imports only)
- features → tenancy (explicit imports only)
- api → features (allowed; api orchestrates)
- api → shared (allowed)
- api → tenancy (allowed)

### Requirement: api/main.py ≤ 150 lines after slice 10

Post-refactor, `api/main.py` MUST be ≤ 150 lines. Routers moved to `api/routers/` with names like `conversations.py`, `webhooks.py`, `chathub.py`.

### Requirement: core/ Fully Dissolved

After all slices, `apps/agent/core/` MUST NOT exist. All modules must be migrated to features, shared, or tenancy.

### Requirement: Git History via git mv

Every file move MUST use `git mv` to preserve blame history. `git log --follow` MUST show pre-move commits.

### Requirement: Slice Order (0-scaffold, 1-shared, 2-tenancy, 3-analytics, 4-comprobantes+delivery, 5-messaging, 6-cobranza, 7-conversation+delete opportunity_detector, 8-rename code+tool no-storage, 9-storage+migration atomic, 10-api thin).

Slices MUST be applied in the stated order. Each slice is one feature move + one commit. Exception: slices 8 and 9 handle the rename + migration (two slices, still follow order).

### Requirement: Characterization Tests Before God-File Splits

Before splitting `api/main.py` (1905 lines) or `tools/cobranza.py` (870 lines), write characterization tests to lock behavior. Only then split.

### Requirement: Per-Slice Rollback Safety

Each slice is one commit on the working branch. `git revert <sha>` MUST restore to the previous green state.

### Requirement: Import Re-Mapping

18 test files have absolute imports referencing `core.*` or `tools.*`. After each structural slice, those imports MUST be re-mapped to the new location. No test assertions or fixtures are modified — only import paths change.

---

## Dimension 2 — Dead Code Removal

### Requirement: opportunity_detector.py Deleted

`apps/agent/core/opportunity_detector.py` MUST be deleted. Lines 138 in `tools/__init__.py` (opportunities field) MUST be removed. Lines 178,184 in `prompts/system.py` (opportunities render) MUST be removed.

#### Scenario: opportunity_detector removed

- GIVEN slice 7 is applied
- WHEN `grep -r "opportunity_detector" apps/agent/` is run
- THEN the only matches are in comments or deleted files
- AND `grep "opportunities:" apps/agent/tools/__init__.py` returns empty

#### Scenario: no behavior change from opportunity removal

- GIVEN a conversation with `api/main.py` loaded
- WHEN the agent responds without opportunities wired
- THEN getattr(response, 'opportunities', []) returns [] (same as before)
- AND no new test fails

---

## Dimension 3 — Full Domain Rename: lead → debtor

### Requirement: Code-Layer Rename (Symbols)

| Old | New |
|---|---|
| `lead_state` | `debtor_state` |
| `class LeadMachine` | `DebtorState` |
| `lead_machine.py` | `debtor_state.py` |
| `prospect_profile.py` / `build_prospect_profile` | `debtor_profile.py` / `build_debtor_profile` |
| `self.lead` | `self.debtor` |
| `lead_data` parameter (code) | `debtor_data` (code; storage is Dim 4) |
| `lead_level` (code constants, params, persistence/state refs) | `debtor_level` |
| `_get_lead_status` / `get_lead_status` | `_get_debtor_status` / `get_debtor_status` |

#### Excluded (NOT debtor-domain — MUST remain verbatim)

- `settings.webhook_lead_url`
- `webhook_config.lead_transition_url`
- `"website_leads_only"` operation mode strings

#### Scenario: lead code symbols renamed

- GIVEN slice 8 is applied
- WHEN `grep -r "lead_machine" apps/agent/` is run (excluding .pyc, __pycache__)
- THEN zero matches (file and symbol both renamed)
- AND `grep -r "debtor_state" apps/agent/ | head -20` shows ≥10 matches

#### Scenario: exclusion list untouched

- GIVEN slice 8 is applied
- WHEN `grep -r "webhook_lead_url\|lead_transition_url\|website_leads_only" apps/agent/` is run
- THEN at least 2 matches per identifier (confirming they were NOT renamed)

### Requirement: Tool Contract Rename (LLM-facing)

`get_lead_status` → `get_debtor_status` in:
- `tools_schema.py`
- `tools/__init__.py`
- `SKILL.md` (public tool description)
- `prompts/` system and examples

New assertion MUST be added to test: `tool.name == "get_debtor_status"`.

#### Scenario: tool name assertion

- GIVEN the test file `tests/test_get_debtor_status_tool.py` exists
- WHEN that test runs
- THEN an assertion confirms `tool['name'] == "get_debtor_status"`

---

## Dimension 4 — Data Migration (Prod-Safe)

### Requirement: sorelia_conversations.lead_data → debtor_data (Additive / Dual-Read)

ADD COLUMN IF NOT EXISTS `debtor_data JSONB`. UPDATE all rows WHERE `lead_data` IS NOT NULL to SET `debtor_data = lead_data` (idempotent). RETAIN `lead_data` column for rollback safety.

Dual-read logic: read `debtor_data`, fallback to `lead_data`, write only to `debtor_data`.

#### Scenario: debtor_data column added and populated

- GIVEN the migration script runs
- WHEN `SELECT COUNT(*) FROM sorelia_conversations WHERE debtor_data IS NOT NULL` is run
- THEN the count ≥ the count WHERE `lead_data IS NOT NULL` (confirming dual-read population)

#### Scenario: rollback restores lead_data fallback

- GIVEN the rollback script is executed (DROP COLUMN debtor_data)
- WHEN the old code tries to read `lead_data` after rollback
- THEN all conversation state is recoverable (zero data loss)

### Requirement: Dashboard Table and Column Rename — Atomic Deploy

NOT dual-read-safe. MUST ship as ONE atomic deploy:
1. Code rename: `upsert_lead` → `upsert_debtor`, all references updated.
2. Migration: ALTER TABLE `sorelia_leads` RENAME TO `sorelia_debtors`. ALTER COLUMN `lead_level` RENAME TO `debtor_level`.
3. Dashboard SQL: All dashboard SQL (if any) updated to reference `sorelia_debtors.debtor_level`.

Brief dashboard-only 500s acceptable (sorelia_debtors is dashboard-only read-path; chat hot-path unaffected).

#### Scenario: external ETL confirmation gate

- GIVEN slice 9 is staged for merge
- WHEN the preflight block is shown
- THEN a human checkbox confirms "No out-of-repo ETL reads from {schema}.sorelia_leads"
- AND migration is gated on that confirmation

#### Scenario: atomic deploy verified by table existence check

- GIVEN the migration runs
- WHEN `SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_name = 'sorelia_debtors')` is checked
- THEN TRUE (table renamed successfully)

### Requirement: debtor_level Enum Value Remap

| Old | New |
|---|---|
| PRE_LEAD | PRE_DEBTOR |
| LEAD | DEBTOR |
| LEAD_ENRICHED | DEBTOR_VERIFIED |
| VISITOR | VISITOR |

`_CONTACT_LEVELS` updated to `{"DEBTOR", "DEBTOR_VERIFIED"}`.

Dashboard filters that reference dead values (CONTACT, QUALIFIED) removed (never emitted by state machine — zero-behavior).

Remap applied to BOTH `sorelia_debtors.debtor_level` and `sorelia_conversations.debtor_level` via idempotent CASE UPDATE.

#### Scenario: enum values remapped

- GIVEN the migration runs
- WHEN `SELECT DISTINCT debtor_level FROM sorelia_debtors` is run
- THEN all values are in {PRE_DEBTOR, DEBTOR, DEBTOR_VERIFIED, VISITOR}
- AND zero PRE_LEAD, LEAD, LEAD_ENRICHED, CONTACT, QUALIFIED values remain

#### Scenario: dead funnel filters absent

- GIVEN the dashboard code is inspected
- WHEN filters referencing CONTACT or QUALIFIED are searched
- THEN zero matches (dead filters removed)

### Requirement: Real-Estate Column Drops

DROP `district_interest`, `purpose`, `budget` from `sorelia_debtors` (verified NOT read by dashboard or any sink). KEEP `project_interest` — LIVE in `/stats top_projects` and `/conversations`.

`pg_dump` of the 3 columns BEFORE DROP (unrecoverable otherwise). Drop gated by human "go".

#### Scenario: project_interest present post-migration

- GIVEN the migration runs
- WHEN `SELECT project_interest FROM sorelia_debtors LIMIT 1` is checked
- THEN the column exists and is NOT NULL for recent records

#### Scenario: district_interest/purpose/budget absent post-migration

- GIVEN the migration runs
- WHEN `SELECT COUNT(*) FROM information_schema.columns WHERE table_name = 'sorelia_debtors' AND column_name IN ('district_interest', 'purpose', 'budget')` is checked
- THEN the count is 0 (columns dropped)

#### Scenario: pg_dump backup available

- GIVEN the migration is about to run
- WHEN the preflight block shows a pg_dump command
- THEN a DBA can execute it manually to capture the dropped columns before DROP executes

### Requirement: Redis Key Self-Expiry (No Migration)

`sorelia:conv:{id}:lead_data` keys have 24h TTL. Dual-read fallback covers overlap. No active migration needed; keys auto-expire.

---

## Cross-Cutting

### Requirement: Test-Green Gate on Every Slice

`uv run pytest tests/ -v` MUST pass after every slice. No merge with failing test.

### Requirement: Slices 9 and 10 in Separate Chained PRs

Slice 9 (migration + storage rename + dashboard SQL) in PR#5 (or subsequent PR after merge). Slice 10 (api/main.py thin) in its own PR#6 chained off prior merge.

### Requirement: Zero Observable Behavior Change (Slices 0–8)

API contracts, response shapes, tool outputs, tenant routing identical. Only import paths change in test files. No signature changes, no protocol changes.

---

## Key Exclusions (MUST NOT be renamed anywhere)

- `settings.webhook_lead_url`
- `webhook_config.lead_transition_url`
- `"website_leads_only"` operation mode strings (settings.py:79-80, main.py:1850,1874)
- `project_interest` column (LIVE — dropping breaks `/stats`)
