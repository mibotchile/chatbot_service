# Tasks: platform-multitype-engine (Olimpo)

> Code root: `apps/agent/` — all paths below are relative to it.
> Contract: ~366 green tests after EVERY slice. Zero cobranza behavior change.
> TDD mode: STRICT — characterization tests FIRST where coverage has gaps; marked [CHAR-TEST].
> Delivery strategy: ask-on-risk | Chain strategy: stacked-to-main

---

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~900–1 100 (across 8 slices) |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR 1 (S1–S2) → PR 2 (S3–S4) → PR 3 (S5) → PR 4 (S6–S7) → PR 5 (S8) |
| Delivery strategy | ask-on-risk |
| Chain strategy | stacked-to-main |

Decision needed before apply: Yes
Chained PRs recommended: Yes
Chain strategy: stacked-to-main
400-line budget risk: High

### Suggested Work Units

| Unit | Slices | Goal | Likely PR | Est. lines | Notes |
|------|--------|------|-----------|-----------|-------|
| WU-1 | S1–S2 | Record + Debtor composition | PR 1 → main | ~200 | Pure additive; shim keeps 366 green |
| WU-2 | S3–S4 | Registry port + engine wiring | PR 2 → main | ~250 | Depends on WU-1 (shim still live) |
| WU-3 | S5 | ToolRegistry registry-driven | PR 3 → main | ~120 | Depends on WU-2 |
| WU-4 | S6–S7 | Persistence + projection table | PR 4 → main | ~300 | Deploy unit; needs Ricky go |
| WU-5 | S8 | Delete compat shim | PR 5 → main | ~80 | Depends on WU-4 |

---

## Slice 1 — CaptureSpec + Record (neutral) [PR 1 / WU-1]

Spec requirement: Neutral Record Entity.
Parallel: No (foundation for everything).

- [ ] 1.1 [CHAR-TEST] Write `tests/test_record_char.py`: assert `Record(COBRANZA_SPEC_inline)` produces identical `level`, `get_status`, `to_dict`, `from_dict` output to current `DebtorState` over a fixed input matrix (≥10 cases covering VISITOR / PRE_DEBTOR / DEBTOR / DEBTOR_VERIFIED). Run `uv run pytest tests/test_record_char.py -v` — expected RED (Record does not exist yet).
- [ ] 1.2 Create `shared/ports/capture_spec.py` with frozen `CaptureSpec` dataclass (contact_fields, interest_fields, enrichment_fields, levels, default_level). No imports from `features/` or `api/`.
- [ ] 1.3 Create `features/conversation/record.py` with `Record` class: `__init__(spec, initial_data, on_transition)`, `level` property, `update`, `get_status`, `to_dict`, `from_dict`. Logic moved VERBATIM from `DebtorState`; field sets / level predicates read from injected spec. Import `CaptureSpec` from `shared/ports/capture_spec.py`.
- [ ] 1.4 Turn test 1.1 GREEN: `uv run pytest tests/test_record_char.py -v`. Then run full suite: `uv run pytest tests/ -v` — all 366+ must pass (no call-site changes yet).
- [ ] Rollback note: additive only; `git revert` removes both new files with zero impact.

## Slice 2 — cobranza Debtor + COBRANZA_SPEC + DebtorState shim [PR 1 / WU-1]

Spec requirement: Debtor as Composition over Record.
Parallel: No (depends on S1).

- [ ] 2.1 Create `features/cobranza/debtor.py` with `COBRANZA_SPEC` (CaptureSpec for cobranza: exact field sets + level predicates from current `DebtorState`) and `Debtor` class that wraps `Record` by composition (has-a, not is-a). Delegates `level`, `collected`, `update`, `get_status` to `self._record`.
- [ ] 2.2 Rewrite `features/conversation/debtor_state.py` as a thin shim: `class DebtorState(Record)` subclassing `Record`, passing `COBRANZA_SPEC` in `__init__`. Re-export module-level constants (`CONTACT_FIELDS`, `INTEREST_FIELDS`, `ENRICHMENT_FIELDS`, level names) so existing imports stay green.
- [ ] 2.3 Run `uv run pytest tests/ -v` — all 366+ tests must pass with shim in place.
- [ ] Rollback note: revert 2.1 + 2.2 restores original `DebtorState`; shim keeps callers untouched.

---

## Slice 3 — AgentTypeRegistry port + in-code impl + TenantConfig field [PR 2 / WU-2]

Spec requirement: Type Registry with Swappable Source + agent_type as Tenancy Config Dimension.
Parallel: No (depends on S2 for COBRANZA_SPEC).

- [ ] 3.1 [CHAR-TEST] Write `tests/test_agent_type_registry.py`: assert `default_registry().get("cobranza")` returns an `AgentTypeSpec` with non-empty tools/gated_tools; assert `get("unknown_type")` raises a typed error; assert `has("cobranza")` is True and `has("other")` is False. Run RED.
- [ ] 3.2 Create `shared/ports/agent_type_registry.py` with `AgentTypeSpec` (frozen dataclass: agent_type, capture_spec, tools, gated_tools, skills, gate_model, projection_table) and `AgentTypeRegistry` Protocol. No imports from `features/` or `api/`.
- [ ] 3.3 Create `features/cobranza/agent_type.py` with `COBRANZA_AGENT_TYPE` (`AgentTypeSpec` instance): tools = current cobranza tool list, gated_tools = current `_GATED_TOOLS` set, gate_model = `"hard_dni"`, projection_table = `"debtors"`, capture_spec = `COBRANZA_SPEC`.
- [ ] 3.4 Create `tenancy/agent_types/__init__.py` (empty) and `tenancy/agent_types/registry.py` with `InCodeAgentTypeRegistry` impl and `default_registry()` factory. Wiring of `COBRANZA_AGENT_TYPE` happens at composition root (api/), NOT inside `tenancy/`. Raise `AgentTypeNotFoundError` (typed) for unknown types.
- [ ] 3.5 Add `agent_type: str = "cobranza"` field to `TenantConfig` in `tenancy/tenant_loader.py`. Verify prestamype/prestaunion configs parse without error (absent field defaults to `"cobranza"`).
- [ ] 3.6 Turn test 3.1 GREEN: `uv run pytest tests/test_agent_type_registry.py -v`. Full suite: `uv run pytest tests/ -v` — 366+ pass.
- [ ] Rollback note: additive (new files + one field); revert removes them cleanly.

## Slice 4 — Engine composes spec from registry [PR 2 / WU-2]

Spec requirement: ToolRegistry Composed per agent_type (dependency path); Record embedded via registry.
Parallel: No (depends on S3).

- [ ] 4.1 In `api/main.py` (or equivalent composition root): wire `default_registry()` into app startup; resolve `spec = registry.get(cfg.agent_type)`; build `Record(spec.capture_spec)` where the engine currently builds `DebtorState()`.
- [ ] 4.2 Migrate all call sites in `features/conversation/state.py` and `features/conversation/redis_store.py` that instantiate `DebtorState()` to use `Record(spec.capture_spec)` (inject spec from engine). The shim still exists — replace call sites, not the class.
- [ ] 4.3 Migrate `features/conversation/hooks.py` (if applicable) and any other caller discovered by `grep -r "DebtorState" apps/agent/` that is NOT a test.
- [ ] 4.4 Run `uv run pytest tests/ -v` — 366+ pass. Verify no non-test import of `DebtorState` remains outside `debtor_state.py` shim itself.
- [ ] Rollback note: revert composition-root wiring + call-site changes; shim path automatically restores behavior.

---

## Slice 5 — ToolRegistry registry-driven gate [PR 3 / WU-3]

Spec requirement: ToolRegistry Composed per agent_type + Per-domain gate applied.
Parallel: No (depends on S4).

- [ ] 5.1 [CHAR-TEST] Write / extend `tests/test_tool_registry.py`: assert cobranza gate blocks tool call without verified identity; assert tool list contains exactly the expected cobranza tools; assert excluded_tools subtraction still works. Run RED where new assertions don't pass yet.
- [ ] 5.2 Modify `api/tool_registry.py` `ToolRegistry.__init__` to accept `gated_tools: frozenset[str]` constructor param (default = current `_GATED_TOOLS` set for back-compat). Remove module-global `_GATED_TOOLS`.
- [ ] 5.3 Modify `ToolRegistry.__init__` to accept `tools: tuple[str, ...]` param; register only tools in that list (plus always-on generics). Default = full current cobranza set.
- [ ] 5.4 In composition root: pass `spec.gated_tools` and `spec.tools` when constructing `ToolRegistry`. Apply `excluded_tools` subtraction AFTER.
- [ ] 5.5 Turn test 5.1 GREEN. Full suite: `uv run pytest tests/ -v` — 366+ pass.
- [ ] Rollback note: restore module-global `_GATED_TOOLS`; defaults ensure cobranza behavior unchanged.

---

## Slice 6 — Persistence neutral names [PR 4 / WU-4]

Spec requirements: Drop sorelia_ Prefix, Empty-DB Recreate.
Parallel: No (depends on S5; forms deploy unit with S7).
**DEPLOY COORDINATION — needs Ricky go before apply.**

- [ ] 6.1 **[test_storage_migration.py REWRITE]** Rewrite `tests/test_storage_migration.py` assertions to target neutral names: `conversations`, `visitors`, `record_data`, `record_level`, `record_data` (visitors). Remove assertions for `sorelia_*` table names, `lead_data` dual-read, and migration-script existence. New assertions verify `ensure_tables` creates `conversations` and `visitors` with neutral column names; no `sorelia_*` tables exist. Run RED before persistence changes.
- [ ] 6.2 Update `features/conversation/persistence/persistence.py`: rename table strings `sorelia_conversations`→`conversations`, columns `debtor_data`→`record_data`, `debtor_level`→`record_level`. Signature: `ensure_tables(pool, schema, *, projection_table: str | None)` — creates projection table only when not None. Drop dual-read of `lead_data` from `load_conversation`.
- [ ] 6.3 Update `features/conversation/persistence/visitor_memory.py`: table `visitors`; `lead_data`→`record_data` in DDL, INSERT, UPDATE-merge, `_row_to_dict` decode list.
- [ ] 6.4 Update `features/conversation/persistence/state.py`: drop `lead_data` dual-read fallback; call `save_conversation(record_data=, record_level=)`. Remove `ConversationState` ctor param `lead_data=` if present.
- [ ] 6.5 Update `features/conversation/persistence/redis_store.py`: `_key()` prefix `sorelia:conv:`→`olimpo:conv:`; suffix `lead_data`→`record_data`. Drop `lead_data` fallback `get`.
- [ ] 6.6 Turn test 6.1 GREEN. Full suite: `uv run pytest tests/ -v` — 366+ pass (adjusted for new names).
- [ ] 6.7 **[DEPLOY STEP — Ricky go required]** On `bd-intranet`: `DROP TABLE IF EXISTS sorelia_conversations, sorelia_debtors, sorelia_visitors CASCADE;`. Then: rsync `apps/agent/` to `automation`; `docker compose up -d --build prestamype-demo`. Verify log `PostgreSQL persistence active`. Rollback: `git revert` + rsync prior code + rebuild.
- [ ] Rollback note: code revert restores table/column names; DB rollback is `git revert` + rsync + rebuild (DB is empty, no data loss).

## Slice 7 — Projection table = debtors via registry [PR 4 / WU-4]

Spec requirement: Per-Type Projection Table is Optional per agent_type + cobranza declares debtors.
Parallel: No (depends on S6; same deploy unit).

- [ ] 7.1 Pass `spec.projection_table` from composition root to `ensure_tables`. Verify `ensure_tables` creates `debtors` table for cobranza (from registry) and skips for `projection_table=None`.
- [ ] 7.2 Update `persistence.py` `upsert_debtor` to target `debtors` (renamed from `sorelia_debtors`).
- [ ] 7.3 Update `api/routers/webhooks.py` (line ~357) and `conversations.py` (line ~356) call sites for `upsert_debtor`/`upsert_projection` — rename table reference, no logic change.
- [ ] 7.4 Update `features/analytics/dashboard.py`: rename `sorelia_debtors`→`debtors` in all 6 query locations (lines ~132, ~141, ~160, ~201, ~206, ~211, ~219, ~277). No query logic change.
- [ ] 7.5 Run `uv run pytest tests/ -v` — 366+ pass. Verify dashboard queries reference `debtors` only.
- [ ] Rollback note: revert renames; DB already recreated in S6 deploy (empty, no data loss).

---

## Slice 8 — Delete compat shim [PR 5 / WU-5]

Spec requirement: cobranza behavior unchanged; dependency rule clean end state.
Parallel: No (depends on S7; shim must have zero callers first).
Prerequisite: `grep -r "DebtorState\|debtor_state" apps/agent/ --include="*.py" | grep -v test | grep -v "debtor_state.py"` returns empty.

- [ ] 8.1 Confirm zero non-test, non-shim imports of `DebtorState` or `debtor_state` module: run `grep -r "DebtorState\|from features.conversation.debtor_state" apps/agent/ --include="*.py" | grep -v test`. Must be empty.
- [ ] 8.2 Delete `features/conversation/debtor_state.py`.
- [ ] 8.3 Update any test that imports `DebtorState` to import `Record` + `COBRANZA_SPEC` instead (or the shim's re-exported constants). Do NOT weaken behavioral test assertions.
- [ ] 8.4 Run `uv run pytest tests/ -v` — 366+ pass. No `sorelia_` prefix anywhere in production code.
- [ ] Rollback note: `git revert` restores shim; one commit boundary.

---

## Dependency Graph

```
S1 → S2 → S3 → S4 → S5 → S6 → S7 → S8
         (WU-1) (WU-2)  (WU-3)  (WU-4)  (WU-5)
PR 1     PR 2   PR 3   PR 4    PR 5
```

Sequential throughout. No parallel slices (each slice's output is the next's input).

## Files Touched (summary)

| File | Slices | Action |
|------|--------|--------|
| `shared/ports/capture_spec.py` | S1 | CREATE |
| `features/conversation/record.py` | S1 | CREATE |
| `features/cobranza/debtor.py` | S2 | CREATE |
| `features/conversation/debtor_state.py` | S2, S8 | SHIM → DELETE |
| `shared/ports/agent_type_registry.py` | S3 | CREATE |
| `features/cobranza/agent_type.py` | S3 | CREATE |
| `tenancy/agent_types/registry.py` | S3 | CREATE |
| `tenancy/tenant_loader.py` | S3 | MODIFY (add field) |
| `api/main.py` (composition root) | S4 | MODIFY (wiring) |
| `features/conversation/state.py` | S4 | MODIFY |
| `features/conversation/redis_store.py` | S4, S6 | MODIFY |
| `api/tool_registry.py` | S5 | MODIFY |
| `features/conversation/persistence/persistence.py` | S6, S7 | MODIFY |
| `features/conversation/persistence/visitor_memory.py` | S6 | MODIFY |
| `features/conversation/persistence/state.py` | S6 | MODIFY |
| `features/analytics/dashboard.py` | S7 | MODIFY (rename SQL strings) |
| `api/routers/webhooks.py` | S7 | MODIFY |
| `tests/test_record_char.py` | S1 | CREATE |
| `tests/test_agent_type_registry.py` | S3 | CREATE |
| `tests/test_tool_registry.py` | S5 | MODIFY/EXTEND |
| `tests/test_storage_migration.py` | S6 | REWRITE assertions |
