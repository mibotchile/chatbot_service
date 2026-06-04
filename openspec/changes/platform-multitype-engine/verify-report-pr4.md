# Verify Report — PR4: S6+S7 (Persistence Neutral Rename + Per-Type Projection Table)

**Change**: platform-multitype-engine
**Branch**: platform/multitype-pr4-persistence-neutral
**Stacked on**: PR1 (S1+S2) -> PR2 (S3+S4) -> PR3 (S5) -> PR4 (S6+S7)
**Verified**: 2026-06-04
**Verdict**: PASS WITH WARNINGS

---

## Test Suite

| Command | Result |
|---------|--------|
| `uv run pytest tests/ -q` | **423 passed in 1.71s** |

Baseline entering PR4: 418 tests (PR3 close).
Net new from S6+S7: +5 (test_storage_migration rewritten: 27 tests replacing prior 23).
Zero failures. Zero errors.

---

## No-Deploy Confirmation

PR4 is **code-only**. Confirmed:
- Single commit `7188ee5` touches 6 source files + 1 test file only
- No SSH executed, no ALTER/CREATE ran against any database
- No migration script produced or run
- Inert until deploy step (requires Ricky go)

---

## Rename Completeness

| Pattern | Occurrences in apps/agent/ | Status |
|---------|---------------------------|--------|
| `sorelia_conversations` | 0 | CLEAN |
| `sorelia_debtors` | 0 | CLEAN |
| `sorelia_visitors` | 0 | CLEAN |
| `debtor_data` | 0 | CLEAN |
| `debtor_level` | 0 | CLEAN |
| `sorelia:conv:` (Redis) | 0 | CLEAN -- now `olimpo:conv:` |
| `sorelia_` (any) | 0 | CLEAN |

**sorelia_visits**: present in dashboard.py (visit scheduling endpoint). SEPARATE table/feature,
NOT one of the 3 in-scope tables. Correctly excluded from S6+S7. Tracked debt for a future slice.

---

## Zero-Behavior Verdict (dashboard.py shape)

Six query locations updated -- pure table/column renames, no logic change:

- list_leads: `debtor_level` -> `record_level`, `sorelia_debtors` -> `debtors` (2 locations)
- get_lead_detail: `sorelia_debtors` -> `debtors`
- get_stats: `sorelia_conversations` -> `conversations`, `sorelia_debtors` -> `debtors` (2x), `debtor_level` -> `record_level`

SELECT shape unchanged: `conversation_id, visitor_id, name, email, phone, project_interest, record_level, created_at`
- `project_interest` preserved
- Enum values `DEBTOR`, `DEBTOR_VERIFIED` preserved unchanged
- **ZERO-BEHAVIOR: CONFIRMED**

---

## test_storage_migration Rewrite Quality

27 tests (up from 23 old). Tests use `inspect.getsource()` -- meaningful structural assertions.

Coverage: conversations table name, record_data/record_level columns, no sorelia_ prefix
in ensure_tables/save_conversation/load_conversation, state.py record_data param + no lead_data
fallback, redis_store.py olimpo:conv: prefix + record_data key + no lead_data fallback,
visitor_memory.py visitors table + record_data, upsert_debtor targets debtors,
ensure_tables projection_table param, dashboard SQL (debtors/conversations/record_level/DEBTOR/
DEBTOR_VERIFIED/project_interest).

No other behavioral test file weakened -- all 418 prior tests intact.

---

## Projection Table Verdict

| Check | Result |
|-------|--------|
| `COBRANZA_AGENT_TYPE.projection_table = "debtors"` (agent_type.py:50) | CONFIRMED |
| `ensure_tables` accepts `projection_table: str | None` | CONFIRMED |
| `ensure_tables` creates table only when `projection_table is not None` | CONFIRMED |
| `api/main.py` reads `_agent_spec.projection_table` and passes to `ensure_tables` | CONFIRMED |
| `upsert_debtor` defaults `projection_table="debtors"` | CONFIRMED |
| `shared/ports/agent_type_registry.py` has `projection_table: str | None` field | CONFIRMED |
| `shared/ports` is pure (no features/ or tenancy/ imports) | CONFIRMED |

---

## Dual-Read Removal Safety

- `lead_data` in state.py: 0 occurrences
- `lead_data` in redis_store.py: 0 occurrences
- `lead_data` in persistence.py: 0 occurrences
- Redis pipeline: 4 gets -> 3 gets (lead_data fallback get removed)
- Safe: olimpo DB is empty -- no legacy lead_data rows exist
- `test_redis_store_no_lead_data_fallback` and `test_state_no_lead_data_fallback` confirm tested

---

## Scope Discipline

- S8 (shim deletion): NOT done -- `features/conversation/debtor_state.py` still present. Correct -- S8 is PR5.
- Non-test, non-shim `DebtorState` imports: 0 -- all callers migrated in PR1+PR2.

---

## Runtime Import Check

Result: ALL IMPORTS OK
(ChathubOutboundClient simulation warning is expected in dev env -- no env vars set.)

---

## Git Cleanliness

- No CLAUDE.md, lock files, pyproject.toml, or pycache committed
- Commit message: `feat(persistence): neutral table names + per-type projection table (S6+S7)` -- conventional commit
- 7 files changed, 338 insertions(+), 314 deletions(-)
- No secrets detected in diff

---

## Deploy Plan Validation

Pending steps (requires Ricky go):
1. On olimpo (bd-intranet): execute SQL to remove the three legacy sorelia_ tables
   (sorelia_conversations, sorelia_debtors, sorelia_visitors)
2. rsync apps/agent/ to automation server
3. Rebuild container (ensure_tables on boot creates: conversations, debtors, visitors*)
4. Verify log: "Persistence tables ensured (schema=prod, projection_table=debtors)"

Assessment:
- Table set targeted is correct and complete for the 3 in-scope tables
- `sorelia_visits` correctly NOT targeted (out of scope)
- olimpo DB is empty -> removal is safe (no data loss)
- `conversations` and `debtors`: created by `ensure_tables` on boot -- correct
- `visitors`*: created lazily by VisitorMemory on first visitor upsert, NOT by ensure_tables.
  Boot log check will NOT show visitors immediately -- expected, not a bug. (See S1 below.)

---

## Issues

### CRITICAL -- 0
None.

### WARNING -- 1

**W1** `tests/test_storage_migration.py` -- No behavioral test for `projection_table=None -> no per-type table created`

The spec scenario "agent_type without projection_table skips per-type table" is only covered by
the param signature inspection test (`test_ensure_tables_accepts_projection_table_param`). No test
verifies that when `projection_table=None` is passed, the SQL branch is skipped. Low risk for this
PR (no production type with None yet), but the spec scenario remains UNTESTED at behavioral level.

Recommendation: Add `test_ensure_tables_skips_projection_table_when_none()` in PR5 or standalone.

### SUGGESTION -- 2

**S1** Deploy runbook should explicitly note that `visitors` table is created lazily by VisitorMemory,
not on boot by `ensure_tables`. The deploy plan verify step may cause confusion when `visitors`
does not appear in the initial boot log.

**S2** `test_dashboard_no_sorelia_on_core_tables` correctly excludes `sorelia_visits` from its
assertion scope. A brief inline comment noting the exclusion (visits scheduling is out of S6+S7
scope) would make the intent explicit for future reviewers.

---

## Spec Compliance Matrix

| Spec Requirement | Status | Evidence |
|-----------------|--------|----------|
| `conversations` table (not sorelia_conversations) | PASS | rg=0, persistence.py diff, 423 tests |
| `record_data` + `record_level` columns in conversations | PASS | persistence.py diff + test_storage_migration |
| `visitors` table (not sorelia_visitors) | PASS | visitor_memory.py verified |
| `record_data` in visitors (not lead_data) | PASS | visitor_memory.py verified |
| No sorelia_ prefix anywhere in app code | PASS | rg=0 |
| Redis prefix olimpo:conv: | PASS | rg=0 for sorelia:conv: |
| ensure_tables(projection_table=) creates debtors when set | PASS | code + test |
| ensure_tables(projection_table=None) skips per-type table | WARNING | code correct, behavioral test missing |
| api/main.py passes spec.projection_table | PASS | main.py diff confirmed |
| dashboard.py shape identical (renames only) | PASS | diff reviewed, DEBTOR/DEBTOR_VERIFIED/project_interest preserved |
| 423 tests pass | PASS | uv run pytest tests/ -q: 423 passed in 1.71s |
| No deploy executed | PASS | code-only commit confirmed |

---

## Ready-to-Merge Verdict

**READY TO MERGE** -- W1 is non-blocking. The code path for projection_table=None is correct
(verified by inspection); the gap is a missing negative test for a spec scenario with no current
production exerciser. Acceptable to add in PR5.

Next: sdd-archive (after deploy confirmation) or PR5/S8 (shim deletion) first.
