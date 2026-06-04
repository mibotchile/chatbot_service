# Proposal: Generalize the engine into a multi-type platform (Olimpo)

## Intent

The engine is generic but hardcoded to cobranza. Generalize its core so `agent_type` is a first-class config dimension and domains plug in as `features/`, while **cobranza stays the only implemented type**. This is a structural/naming generalization with **ZERO intended behavior change** for cobranza. Sets the foundation for future creditos/inmobiliario without building them now. Authoritative design: `reports/olimpo-platform-arquitectura-2026-06-03.md`.

## Scope

### In Scope
- Neutral domain concept `Record` (identity + contact + progressive-capture state) in `features/conversation/`, generalizing today's `DebtorState` (`features/conversation/debtor_state.py`).
- `Debtor` as a projection over `Record` by **composition** (not inheritance) in `features/cobranza/`.
- `agent_type` as a config dimension in `tenancy/` + a **type registry** (`agent_type → {features, tools, skills, gate_model, state_spec}`).
- Composable `ToolRegistry` keyed by `agent_type` (built on the `shared/ports/tool_registry.py` DI port). Gate becomes **per-domain**, not global.
- Persistence cleanup: drop legacy `sorelia_` prefix → common `conversations`/`visitors` (+ `records` if design decides), per-type `debtors` created by `ensure_tables` per `agent_type`; fix `sorelia_visitors.lead_data` → neutral naming.
- Migrate cobranza to the new platform model.

### Out of Scope
- `features/creditos` and `features/inmobiliario` (deferred to real business case).
- Data migration (olimpo DB is fresh/empty → drop+recreate tables).
- Renaming `chatbot-cobranza` repo → `olimpo` (conceptual, later).
- `debtor` naming stays — the prior `lead→debtor` rename is correct and kept.

## Capabilities

### New Capabilities
- `record-model`: neutral interlocutor entity (identity + contact + progressive-capture state) in conversation/.
- `agent-type-registry`: maps `agent_type` to its features/tools/skills/gate/state composition.
- `composable-tool-registry`: ToolRegistry assembled per `agent_type` with per-domain gate.

### Modified Capabilities
- `cobranza-state`: `Debtor` re-expressed as composition over `Record` (behavior unchanged).
- `persistence`: table names de-`sorelia_`-fied; per-type table creation by `agent_type`.

## Approach

Extract the neutral capture machine from `DebtorState` into a `Record` domain entity in `features/conversation/`. `features/cobranza/` composes a `Debtor` (Record + debt). Introduce a type registry resolved from tenancy that the `ToolRegistry` and `ensure_tables` consume to assemble tools, gate, and per-type tables. Cobranza is the single registered type; the registry has exactly one entry. Tests are the contract — the bot must behave identically.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `features/conversation/debtor_state.py` | Modified | Generalize into neutral `Record`/capture state |
| `features/conversation/` | New | `Record` domain entity |
| `features/cobranza/` | Modified | `Debtor` as composition over `Record` |
| `tenancy/` | New/Modified | `agent_type` dimension + type registry |
| `shared/ports/tool_registry.py` + `api/tool_registry.py` | Modified | Composable per-type, per-domain gate |
| `shared/persistence/persistence.py` | Modified | `conversations`/`visitors`/`debtors`, per-type `ensure_tables` |
| `features/conversation/persistence/visitor_memory.py` | Modified | `lead_data` → neutral naming |

## Open Decisions (for Ricky — NOT decided here)

1. Neutral table shape: separate `records` + `conversations`, or `conversations` with `Record` embedded (today state lives in `sorelia_conversations`)?
2. Type registry as code (dict/factory) vs config-driven (`tenant.config.json`)?
3. Slice ordering for the migration.

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Behavior drift in cobranza | Med | STRICT TDD; ~366 green baseline is the contract; no behavior change intended |
| Over-abstraction with only 1 type | Med | Registry has exactly one entry; no empty features dirs; YAGNI enforced |
| Persistence rename breaks prod | Low | DB empty → drop+recreate; backup exists |
| DI port not flexible enough for per-domain gate | Low | Port from PR8 is the foundation; extend, don't rewrite |

## Rollback Plan

- Code: `git revert` the change branch; rsync prior code + rebuild prestamype container.
- DB: tables are recreated, not migrated. To roll back, drop new tables and recreate legacy `sorelia_*` (or restore `automation:/home/onbot/olimpo_predeploy_backup_*.tgz`). Olimpo is empty → no data loss.

## Dependencies

- Builds on the archived screaming-architecture refactor (DI `ToolRegistry` port).
- Olimpo Postgres (schema `project_quidi0iwqy0l3pjwrklb`, user `olimpo_prestamype`).

## Deploy Steps

1. Code change → rsync branch → rebuild prestamype container (`docker compose up -d --build prestamype-demo`).
2. DB change → since olimpo is empty, **drop legacy `sorelia_*` tables and let `ensure_tables` recreate** new-named tables on boot. No data migration.
3. Verify log `PostgreSQL persistence active`, container healthy, bot behaves identically.

## Success Criteria

- [ ] All tests green (~366 baseline maintained or grown); cobranza behaves identically.
- [ ] `agent_type` resolved from tenancy; type registry drives tool/gate/table composition.
- [ ] `Record` exists in conversation/; `Debtor` composes it in cobranza/ (no inheritance).
- [ ] No `sorelia_` prefix and no `lead_data` remain in persistence.
- [ ] Adding a 2nd type later requires only a new registry entry + `features/<type>/` (no engine edits).
