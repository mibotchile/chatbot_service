# Tasks: Conversation Result Tracking (Layer 3)

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | 650–850 (new files + persistence + wiring + sweep + tests) |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR 1 (catalog+derivation+PG) → PR 2 (terminal hook+Doris) → PR 3 (sweep+integration) |
| Delivery strategy | ask-on-risk |
| Chain strategy | pending |

Decision needed before apply: Yes
Chained PRs recommended: Yes
Chain strategy: pending
400-line budget risk: High

### Suggested Work Units

| Unit | Goal | Likely PR | Notes |
|------|------|-----------|-------|
| 1 | Catalog + pure derivation + PG tables + journal/snapshot fns + tests | PR 1 | Base: main; self-contained; no wiring changes |
| 2 | Terminal hook in wiring + Doris sink + tests | PR 2 | Base: PR 1; requires catalog + PG fns from Unit 1 |
| 3 | Inactivity sweep + startup wiring + integration test | PR 3 | Base: PR 2; completes full end-to-end path |

---

## Phase 1: Outcome Catalog (P1-foundation)

- [x] 1.1 **[RED]** Write `tests/test_gestion_catalog.py`: assert `SCHEMA_VERSION == 1`; assert all 8 outcome constants exist in `Outcome`; assert all 6 `EventType` constants; assert `INTENT_TO_CAPABILITY` has at least one entry; assert `INTENT_TO_REASON` covers the escalation intents from design §4. Verify: `uv run pytest tests/test_gestion_catalog.py -v`
- [x] 1.2 **[GREEN]** Create `apps/agent/features/analytics/gestion_catalog.py`: `SCHEMA_VERSION = 1`; `class Outcome(str, Enum)` with 8 values; `class EventType(str, Enum)` with 6 values; `TERMINAL_SIGNALS: dict`; `INTENT_TO_CAPABILITY: dict`; `INTENT_TO_REASON: dict`. Verify: `uv run pytest tests/test_gestion_catalog.py -v`

---

## Phase 2: Pure Outcome Derivation (P1-logic)

- [x] 2.1 **[RED]** Write `tests/test_gestion_derivation.py`: one test per priority branch (8 branches); precedence test (`identity_failed` beats `was_escalated`); `_reason_for_intent` table-driven over `INTENT_TO_REASON`; unknown intent returns `None`; all use no IO/fixtures. Verify: `uv run pytest tests/test_gestion_derivation.py -v`
- [x] 2.2 **[GREEN]** Create `apps/agent/features/analytics/gestion_derivation.py`: `def derive_outcome(*, session_state, resolved_intent, was_escalated, identity_failed, commitment_registered, proof_submitted, fallback_exhausted, identified, info_provided) -> tuple[str, str | None]`; `def _reason_for_intent(intent: str | None) -> str | None`. Values sourced exclusively from `gestion_catalog`. Verify: `uv run pytest tests/test_gestion_derivation.py -v`

---

## Phase 3: Postgres DDL + Write Functions (P2-persistence)

- [x] 3.1 **[RED]** Write `tests/test_gestion_persistence.py`: fixture creates test schema via `ensure_tables()`; assert `gestiones` table exists with all columns from design §2.1 (including `updated_at`); assert `gestion_events` exists with columns from §2.2; assert indexes exist. Verify: `uv run pytest tests/test_gestion_persistence.py -v -k table`
- [x] 3.2 **[GREEN]** Edit `apps/agent/shared/persistence/persistence.py`: inside `ensure_tables()` after `conversations` block, add `CREATE TABLE IF NOT EXISTS {schema}.gestiones (...)` with `updated_at TIMESTAMPTZ DEFAULT NOW()` and both indexes; add `CREATE TABLE IF NOT EXISTS {schema}.gestion_events (...)` with its index. Verify: `uv run pytest tests/test_gestion_persistence.py -v -k table`
- [x] 3.3 **[RED]** Extend `tests/test_gestion_persistence.py`: test `append_gestion_event` returns dict with `event_id` and `ts`; test `upsert_gestion` inserts on first call with `closed_at=None, outcome=None, capabilities_used=[]`; test capabilities accumulate (no duplicate) on second call; test closing sets `outcome`, `outcome_reason`, `closed_at` in same upsert; test second close call does NOT overwrite first `closed_at`. Verify: `uv run pytest tests/test_gestion_persistence.py -v`
- [x] 3.4 **[GREEN]** Add to `apps/agent/shared/persistence/persistence.py`: `async def append_gestion_event(pool, schema, conversation_id, *, event_type, intent=None, capability=None, payload=None) -> dict` (INSERT RETURNING); `async def upsert_gestion(pool, schema, conversation_id, *, fields: dict) -> None` (INSERT ... ON CONFLICT DO UPDATE; capabilities merged; COALESCE terminal-only fields; closed_at/outcome only set when provided). Verify: `uv run pytest tests/test_gestion_persistence.py -v`

---

## Phase 4: Terminal-Immediate Hook + Doris Sink (P3-wiring + P4-doris)

- [ ] 4.1 **[RED]** Write `tests/test_gestion_sink.py`: mock `analytics_sink._async_write`; assert `record_gestion(snapshot=row)` calls `_async_write("bot_gestiones", [...])` with `datetime_utc`, `capabilities_used` as JSON string; assert `record_gestion_event(event=row)` calls `_async_write("bot_gestion_events", [...])` with `datetime_utc`; assert neither raises when `_async_write` raises `Exception`. Verify: `uv run pytest tests/test_gestion_sink.py -v`
- [ ] 4.2 **[GREEN]** Create `apps/agent/features/analytics/gestion_sink.py`: `async def record_gestion(*, snapshot: dict) -> None` (`_to_doris_gestion` maps PG row → Doris shape, JSON-encodes arrays, formats `datetime_utc`); `async def record_gestion_event(*, event: dict) -> None` (`_to_doris_event` maps row, JSON-encodes `payload`). Both call `analytics_sink._async_write`. Verify: `uv run pytest tests/test_gestion_sink.py -v`
- [ ] 4.3 **[RED]** Write `tests/test_gestion_wiring.py`: mock `upsert_gestion`, `append_gestion_event`, `record_gestion`, `record_gestion_event`, `was_escalated`; test terminal intent (`payment_commitment`) → `derive_outcome` produces non-unresolved outcome → `closed_at` set on snapshot + terminal event appended + both Doris fns called; test non-terminal intent (`consulta_deuda`) → `closed_at` null + no terminal event + `capability_used` event appended; test already-closed snapshot (`closed_at IS NOT NULL`) → no second close. Verify: `uv run pytest tests/test_gestion_wiring.py -v`
- [ ] 4.4 **[GREEN]** Edit `apps/agent/api/wiring.py`: add `async def _emit_gestion(m, conv, result, tool_pairs)` (derives flags from `tool_pairs`/`session_state`, calls `append_gestion_event` for each capability event, then `upsert_gestion`; if terminal: derives outcome, appends `terminal` event, sets `closed_at`/`outcome` on snapshot; guards on `m.store.db_pool is not None`); add `def _spawn_gestion(m, conv, result, tool_pairs)` (fire-and-forget via `asyncio.create_task`, like `_spawn_analytics`, ref-kept in `_analytics_tasks`). Verify: `uv run pytest tests/test_gestion_wiring.py -v`
- [ ] 4.5 **[GREEN]** Edit `apps/agent/api/routers/conversations.py`: after the existing `m._spawn_analytics(...)` call (~line 333), add `m._spawn_gestion(conv, result, tool_pairs)`. Verify: `uv run pytest tests/test_gestion_wiring.py -v`

---

## Phase 5: Inactivity Sweep Worker (P5-sweep)

- [ ] 5.1 **[RED]** Write `tests/test_gestion_sweep.py`: test `_sweep_once` closes row with `closed_at IS NULL` and `updated_at` older than TTL → sets `outcome=unresolved`, `closed_at` set, terminal journal event appended, Doris fns called; test skips row with `closed_at` already set; test skips row with `updated_at` within TTL; test per-tenant TTL (two tenants, different TTLs, only stale-enough rows closed). Verify: `uv run pytest tests/test_gestion_sweep.py -v`
- [ ] 5.2 **[GREEN]** Create `apps/agent/features/analytics/gestion_sweep.py`: `async def _sweep_once(pool, schema, tenant_ttl_map: dict[str, int])` (runs UPDATE ... RETURNING query for stale open rows, grouped by tenant TTL; for each closed row calls `append_gestion_event` terminal + `upsert_gestion` closed state + `record_gestion` + `record_gestion_event` Doris); `async def start_sweep_loop(pool, schema, tenant_ttl_map, interval_seconds)` (while-True asyncio loop). Verify: `uv run pytest tests/test_gestion_sweep.py -v`
- [ ] 5.3 **[GREEN]** Edit `apps/agent/api/wiring.py`: in the app startup/lifespan path, resolve `gestion_inactivity_ttl_minutes` per tenant from `_load_tenant_config`, build `tenant_ttl_map`; launch `gestion_sweep.start_sweep_loop(...)` as background task ref-kept in `_analytics_tasks`; also add `settings.gestion_sweep_interval_seconds` (default `300`) to `apps/agent/shared/settings.py` or equivalent config. Verify: `uv run pytest tests/test_gestion_sweep.py -v`

---

## Phase 6: Integration Test (P6-integration)

- [ ] 6.1 **[RED]** Write `tests/test_gestion_integration.py`: full async test using a real test Postgres schema; simulate a conversation: first turn (non-terminal) → assert `gestiones` row created open, `capability_used` event in journal; second turn (terminal `payment_commitment`) → assert `gestiones.closed_at` set, `outcome = payment_commitment_registered`, terminal event in journal; assert journal fully derivable (replay events → matches snapshot); assert `schema_version = 1` on snapshot; assert no write to `GENERAL.mibotair_results`. Verify: `uv run pytest tests/test_gestion_integration.py -v`
- [ ] 6.2 **[GREEN]** Fix any gaps exposed by the integration test (adjust wiring, persistence, or catalog as needed). Verify: `uv run pytest tests/ -v`
- [ ] 6.3 **[VERIFY]** Full test suite green, no regression. Verify: `uv run pytest tests/ -v`
