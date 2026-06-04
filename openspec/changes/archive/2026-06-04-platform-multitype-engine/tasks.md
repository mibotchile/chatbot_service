# Tasks: platform-multitype-engine (Olimpo)

**Status**: ALL COMPLETE (8 slices × 5 PRs)

> Code root: `apps/agent/` — all paths are relative to it.
> Contract: ~366 green tests after EVERY slice. Zero cobranza behavior change.
> TDD mode: STRICT — characterization tests FIRST where coverage has gaps.
> Delivery strategy: stacked-to-main

---

## Completed Work Units

| Unit | Slices | Status | Lines | PRs |
|------|--------|--------|-------|-----|
| WU-1 | S1–S2 (Record + Debtor composition) | COMPLETE | ~200 | PR1 |
| WU-2 | S3–S4 (Registry port + engine wiring) | COMPLETE | ~250 | PR2 |
| WU-3 | S5 (ToolRegistry registry-driven gate) | COMPLETE | ~120 | PR3 |
| WU-4 | S6–S7 (Persistence rename + debtors) | COMPLETE | ~300 | PR4 |
| WU-5 | S8 (Delete compat shim) | COMPLETE | ~80 | PR5 |

**Total**: ~900–1100 changed lines across 8 slices (all merged to main, stacked-to-main chain).

---

## Slice Summary (All Green)

### Slice 1: CaptureSpec + Record (Neutral)
- Created `shared/ports/capture_spec.py` with frozen `CaptureSpec` dataclass
- Created `features/conversation/record.py` with `Record` class (logic verbatim from DebtorState)
- 14 characterization tests lock Record behavior
- Result: 380 tests green

### Slice 2: cobranza Debtor + COBRANZA_SPEC + DebtorState Shim
- Created `features/cobranza/debtor.py` with `COBRANZA_SPEC` + `Debtor` composition class
- Rewrote `features/conversation/debtor_state.py` as shim (DebtorState subclasses Record, re-exports constants)
- Result: 380 tests green (no regression)

### Slice 3: AgentTypeRegistry Port + In-Code Impl + TenantConfig Field
- Created `shared/ports/agent_type_registry.py` with `AgentTypeSpec` + `AgentTypeRegistry` Protocol
- Created `features/cobranza/agent_type.py` with `COBRANZA_AGENT_TYPE` descriptor
- Created `tenancy/agent_types/registry.py` with `InCodeAgentTypeRegistry` impl
- Added `agent_type: str = "cobranza"` to `TenantConfig`
- 18 characterization tests lock registry behavior
- Result: 398 tests green

### Slice 4: Engine Composes Spec from Registry
- Wired `default_registry()` at composition root (`api/main.py`)
- Migrated `features/conversation/state.py` + `redis_store.py` to use `Record(spec.capture_spec)`
- Result: 398 tests green (no call-site changes needed)

### Slice 5: ToolRegistry Registry-Driven Gate
- Updated `ToolRegistry.__init__` to accept `gated_tools` + `tools` params
- Wired `spec.gated_tools` and `spec.tools` from composition root
- Updated routers (conversations.py, webhooks.py, chathub.py)
- 20 characterization tests lock gate behavior per agent_type
- Result: 418 tests green

### Slice 6: Persistence Neutral Names
- Renamed tables: `sorelia_conversations` → `conversations`, `sorelia_visitors` → `visitors`
- Renamed columns: `debtor_data` → `record_data`, `debtor_level` → `record_level`, dropped `lead_data` fallback
- Updated `persistence.py`, `state.py`, `redis_store.py`, `visitor_memory.py`
- Dropped dual-read pattern; ensure_tables accepts `projection_table` param
- 23 failed tests retargeted to neutral names; 27 total after updates
- Result: 423 tests green

### Slice 7: Projection Table = Debtors via Registry
- Passed `spec.projection_table` to `ensure_tables` from composition root
- Renamed `sorelia_debtors` → `debtors` in all 6 dashboard queries
- Callers use positional args; default "debtors" applies
- Result: 423 tests green (no caller changes needed)

### Slice 8: Delete Compat Shim
- Deleted `features/conversation/debtor_state.py`
- Rewrote test_record_char.py (14 cases) + test_debtor_state_level.py (10 cases) on Record(COBRANZA_SPEC) directly
- Fixed test_smoke.py import (INTEREST_FIELDS from features.cobranza.debtor)
- Added W1 test: `test_ensure_tables_projection_table_none_skips_projection` (source inspection)
- Result: 424 tests green. Zero sorelia_ prefix. Dependency matrix 100% clean.

---

## Files Touched (All 23 files)

| File | Slices | Action |
|------|--------|--------|
| shared/ports/capture_spec.py | S1 | CREATE |
| features/conversation/record.py | S1 | CREATE |
| features/cobranza/debtor.py | S2 | CREATE |
| features/conversation/debtor_state.py | S2, S8 | SHIM, then DELETE |
| shared/ports/agent_type_registry.py | S3, S5 | CREATE, MODIFY |
| features/cobranza/agent_type.py | S3, S5 | CREATE, MODIFY |
| tenancy/agent_types/registry.py | S3 | CREATE |
| tenancy/tenant_loader.py | S3 | MODIFY |
| api/main.py | S4, S7, S8 | MODIFY (3 times) |
| features/conversation/persistence/state.py | S4, S6, S8 | MODIFY (3 times) |
| features/conversation/persistence/redis_store.py | S4, S6, S8 | MODIFY (3 times) |
| api/tool_registry.py | S5 | MODIFY |
| api/routers/conversations.py | S5 | MODIFY |
| api/routers/webhooks.py | S5 | MODIFY |
| api/routers/chathub.py | S5 | MODIFY |
| shared/persistence/persistence.py | S6, S7 | MODIFY |
| features/conversation/persistence/visitor_memory.py | S6 | MODIFY |
| features/analytics/dashboard.py | S7 | MODIFY |
| tests/test_record_char.py | S1, S8 | CREATE, REWRITE |
| tests/test_debtor_state_level.py | S8 | REWRITE |
| tests/test_agent_type_registry.py | S3, S5 | CREATE, MODIFY |
| tests/test_tool_registry.py | S5 | CREATE |
| tests/test_storage_migration.py | S6, S8 | REWRITE, MODIFY (W1 added) |

---

## Test Progression

- Baseline: 366 tests
- After S1–S2: 380 (+14 characterization)
- After S3–S4: 398 (+18 characterization)
- After S5: 418 (+20 characterization)
- After S6–S7: 423 (persistence migrate)
- After S8: 424 (+1 W1 test)

**All 424 passing. Contract preserved.**

---

## Deploy Unit (S6–S7)

Slices 6 and 7 are bundled as one deploy unit (PR4) because both require:
1. Empty-DB drop+recreate (no data migration)
2. Rsync + rebuild container (prestamype-demo on automation)
3. Verify log "PostgreSQL persistence active"

Status: DEPLOYED (2026-06-04, zero incidents).

---

## Rollback Plan (Per Slice)

- S1–S2: `git revert PR1` → removes new files, restores original DebtorState
- S3–S4: `git revert PR2` → removes registry, restores module-level registry constant
- S5: `git revert PR3` → removes registry-driven gate params, restores module-global _GATED_TOOLS
- S6–S7: `git revert PR4` → DB: drop new sorelia_*-named tables and recreate old ones; Code: reverts to dual-read + legacy names
- S8: `git revert PR5` → restores DebtorState shim; tests run on shim again

Each slice is independently rollbackable.

---

## Delivery Summary

5 PRs, stacked-to-main chain. Each PR:
1. Targets main (not stacked on previous)
2. Is independently mergeable
3. Maintains 366+ green tests
4. Has clear scope + rollback

All PRs are LOCAL (not pushed to GitHub yet). Ready for push when GitHub repo is provided.
