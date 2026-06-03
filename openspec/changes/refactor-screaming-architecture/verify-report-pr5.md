# Verify Report — PR5 (Slice 9: Storage + Dashboard Migration)

**Change**: refactor-screaming-architecture  
**Branch**: refactor/screaming-arch-pr5-migration  
**Date**: 2026-06-03  
**Verdict**: FAIL — 1 CRITICAL (enum vocabulary mismatch; webhook never fires)

---

## Test Results

```
333 passed in 1.46s
```

All tests green. But see CRITICAL-1 — green tests mask the runtime bug.

---

## THE ENUM-CONSISTENCY VERDICT

**The webhook is broken. This is a real runtime bug, not a deploy-ordering gap.**

### Full chain traced:

| Step | Location | Value emitted |
|------|----------|---------------|
| DebtorState.level property | `apps/agent/features/conversation/debtor_state.py:36-47` | `"LEAD"` / `"LEAD_ENRICHED"` / `"PRE_LEAD"` / `"VISITOR"` |
| Stored to DB via upsert_debtor | `api/main.py:421-426` | `conv.debtor.level` → OLD vocabulary stored |
| _CONTACT_LEVELS gate | `api/main.py:445` and `api/main.py:1191` | `{"DEBTOR", "DEBTOR_VERIFIED"}` |
| Membership test | `api/main.py:447, 1193` | `"LEAD" in {"DEBTOR","DEBTOR_VERIFIED"}` → **always False** |
| Webhook on_lead_captured | `shared/webhooks.py` | **NEVER fires** |

**Root cause**: The `_CONTACT_LEVELS` set was updated to new enum values but `DebtorState.level` was never updated to emit those values. The state machine still returns `"LEAD"` and `"LEAD_ENRICHED"`.

**Why tests don't catch it**: The characterization test `test_contact_levels_uses_new_enum_values` uses `inspect.getsource()` to verify the string `"DEBTOR"` appears in main.py source. It does — but it cannot verify that `DebtorState.level` *returns* that string at runtime. Zero tests exercise the path: trigger transition → check _CONTACT_LEVELS → fire webhook.

**Secondary consequence**: The value stored to `debtor_level` column via `upsert_debtor(... conv.debtor.level)` will also be old vocabulary (`"LEAD"`, `"LEAD_ENRICHED"`). The migration script's Section 1d/1e enum remaps those in DB as a one-time operation — but going forward, new rows will land with old vocabulary until debtor_state.py is fixed.

**Fix**: Update `DebtorState.level` in `debtor_state.py:41-47` to return `"DEBTOR_VERIFIED"`, `"DEBTOR"`, `"PRE_DEBTOR"`, `"VISITOR"`. Add a behavioral test that instantiates DebtorState with contact+enrichment fields and asserts `state.level == "DEBTOR_VERIFIED"` and that `state.level in {"DEBTOR","DEBTOR_VERIFIED"}` is True.

---

## CRITICAL Findings

### CRITICAL-1: debtor_state.py level property emits old enum vocabulary
- **File**: `apps/agent/features/conversation/debtor_state.py:41-47`
- **Impact**: Webhook `on_lead_captured` NEVER fires on this branch. Also, `upsert_debtor` stores old enum values (`"LEAD"`) into `debtor_level` column — requires migration's CASE UPDATE to fix historical rows but new rows post-deploy will also have old values until the state machine is fixed.
- **Evidence**: Property returns literal strings `"LEAD_ENRICHED"`, `"LEAD"`, `"PRE_LEAD"` — none of which are in `_CONTACT_LEVELS = {"DEBTOR", "DEBTOR_VERIFIED"}`.
- **Fix**: Update the four return strings in `debtor_state.py:41-47`.

---

## WARNING Findings

### WARNING-1: ensure_tables docstring is stale
- **File**: `apps/agent/shared/persistence/persistence.py:31`
- `"""Create sorelia_conversations and sorelia_leads tables if they don't exist."""`
- The function creates `sorelia_debtors`. No runtime impact, but misleading for operators.

### WARNING-2: save_lead_data / get_lead_data are dead no-op exports
- **File**: `apps/agent/shared/persistence/persistence.py:227-240`
- No callers remain. They are stale surface area. Remove in PR6.

### WARNING-3: Falsy-fallback edge case in dual-read
- **File**: `apps/agent/features/conversation/persistence/state.py:151`
- `debtor_data = row.get("debtor_data") or row.get("lead_data") or {}`
- If `debtor_data = {}` (column default) and `lead_data` is non-empty, falls through to `lead_data`. Benign given migration backfill logic (Section 0 only copies non-empty lead_data), but not obvious to future maintainers. Document the intentional behavior.

---

## SUGGESTION Findings

### SUGGESTION-1: Characterization tests are static source-inspection — cannot catch runtime vocabulary mismatch
All 22 tests in `tests/test_storage_migration.py` use `inspect.getsource()` / `Path.read_text()`. They check string presence, not runtime behavior. This is what allowed CRITICAL-1 to slip through green. Add at minimum one behavioral test for the `level` property and the `_CONTACT_LEVELS` gate.

### SUGGESTION-2: Migration script section numbering: 0, 1, 2, 4 (Section 3 missing)
- **File**: `migrations/20260603_refactor_debtor_rename.sql`
- Cosmetic — no functional gap — but confusing for operators. Add a comment or renumber.

---

## Spec Compliance Matrix

| Requirement | Status | Notes |
|-------------|--------|-------|
| debtor_data additive dual-read (state.py) | PASS | Correct `or` chain |
| debtor_data dual-read (redis_store.py) | PASS | Both keys read |
| upsert_debtor / sorelia_debtors in persistence | PASS | Verified |
| _CONTACT_LEVELS = {DEBTOR, DEBTOR_VERIFIED} | PARTIAL | Set updated but state machine emits old values — CRITICAL |
| dashboard sorelia_debtors + debtor_level | PASS | All 9 SQL hits updated |
| dead CONTACT/QUALIFIED filters removed | PASS | Confirmed absent |
| project_interest preserved | PASS | dashboard.py + persistence.py |
| district_interest/purpose/budget dropped from ensure_tables | PASS | Not in CREATE TABLE |
| Migration preflight block | PASS | All 5 checkboxes present |
| pg_dump documented | PASS | Command present |
| Human ETL confirmation gate | PASS | Checkbox present |
| Idempotency guards | PASS | IF EXISTS throughout |
| Rollback SQL | PASS | Section 4 complete |
| No DB execution at test/import time | PASS | .sql file is inert |
| EXCLUDE list untouched | PASS | webhook_lead_url, lead_transition_url, website_leads_only unchanged |

---

## Dual-Read Correctness Assessment

- state.py: CORRECT pattern. `debtor_data if debtor_data is not None else lead_data` in constructor. `row.get("debtor_data") or row.get("lead_data") or {}` in get_or_create. See WARNING-3 for the falsy-edge.
- redis_store.py: CORRECT. Reads `:debtor_data` key first, falls back to `:lead_data`.
- Old-key read covered by test? Partially — tests check source string presence only.

---

## Migration Script Quality

PRODUCTION-READY as ops artifact. Preflight present. ETL confirmation gate present. pg_dump command present. Idempotency guards throughout. Rollback SQL complete. Deploy runbook ordered correctly (additive first, atomic second, drops last). Section 3 numbering gap is cosmetic only.

---

## No-DB-Execution Confirmation

CONFIRMED. The `.sql` file is inert — no Python path runs it. `ensure_tables()` creates `sorelia_debtors` via `CREATE TABLE IF NOT EXISTS` only; no ALTER/RENAME/DROP at import or test time.

---

## Runtime Import

CLEAN. All modules import without errors:
- `api.main`, `features.analytics.dashboard`, `shared.persistence.persistence`
- `features.conversation.persistence.state`, `features.conversation.debtor_state`

---

## Git Hygiene

- No CLAUDE.md, lockfiles, pycache, or .env committed.
- No secrets in `.sql` (placeholders `<DB_HOST>` etc.).
- 7 clean commits with conventional commit messages.

---

## Final Verdict: FAIL — DO NOT MERGE

**Blocking issue**: CRITICAL-1 must be fixed first. The fix is mechanical (5 lines in debtor_state.py) but has downstream implications that must be verified:
1. Update `DebtorState.level` return values to `"DEBTOR_VERIFIED"`, `"DEBTOR"`, `"PRE_DEBTOR"`, `"VISITOR"`.
2. Verify `upsert_debtor` callers pass `conv.debtor.level` — they do, which means post-fix the DB will also receive new vocabulary (good — migration CASE UPDATE becomes a no-op for new rows).
3. Add a behavioral test: `state.level in _CONTACT_LEVELS` is True after collecting contact+enrichment data.
4. Re-run 333 tests — they should stay green.

After that fix, re-run sdd-verify. The 3 WARNINGs are merge-blocking only if you decide stale docstrings/dead code matter (they don't for correctness). The fix is small — this should be a patch commit on the same branch before merge.
