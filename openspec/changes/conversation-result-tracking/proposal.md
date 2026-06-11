# Proposal: Conversation Result Tracking (bot-owned, tenant-agnostic)

## Intent

Today the cobranza bot persists **per-turn** signals only: Layer-1 ping-pong
(Postgres `conversations.history` + Doris `bot_interactions`) and Layer-2 LLM
cost (Doris `bot_llm_usage`). It NEVER records the **terminal outcome** of a
conversation. There is no gestión/result row in Postgres or Doris, and no
conversation-end hook — abandoned chats simply stop. BI cannot answer "how did
this conversation end?" without re-reading raw history.

This change adds Layer-3: a bot-owned, tenant-agnostic, versioned record of each
conversation's final result. Reference: `docs/specs-input/prestamype/diseno-registro-resultado-conversacion.md`.

## Scope

### In Scope
- **Standard outcome catalog** (versioned, `schema_version=1`): 8 outcomes,
  `outcome_reason`, and the `capabilities_used` axis — generic cobranza vocabulary.
- **Journal + snapshot data model** (NOT SCD2): append-only `gestion_events`
  journal + current `gestiones` snapshot (upsert, 1 row/conversation) in Postgres,
  replicated to Doris `cobranza_analytics` via the existing `analytics_sink`.
- **Outcome derivation** at close (priority-ordered rule from the design doc).
- **Dual trigger**: terminal-immediate (terminal intent) + inactivity sweep
  (job closes stale conversations as `unresolved`, TTL configurable per tenant).
- **Reuse** of Layers 1 & 2, joinable by `conversation_id` / `session_id`.

### Out of Scope
- Homologation `(outcome, outcome_reason) → client n1/n2/n3` — external, per-tenant.
- Writing to `GENERAL.mibotair_results` (ETL-fed, owned by another app).
- The external apichatbot/gateway and any client-specific tipificación schema.

## Capabilities

### New Capabilities
- `conversation-result-tracking`: bot-owned terminal outcome record — generic
  versioned catalog, journal+snapshot persistence (Postgres+Doris), derivation,
  terminal-immediate trigger, inactivity sweep.

### Modified Capabilities
- None.

## Approach

1. **DDL**: extend `ensure_tables()` (persistence.py:29) to create `gestiones`
   + `gestion_events`. Add matching Doris tables (`bot_gestiones`, `bot_gestion_events`).
2. **Emit**: at the per-turn call-site (wiring.py:156 `_spawn_analytics`), append
   `capability_used` / `credit_state_set` events and upsert the snapshot.
3. **Terminal**: derive `outcome` + `outcome_reason` + `closed_at` on terminal
   intent (`resolved_intent` at conversations.py:311, `was_escalated()`); emit
   terminal event; replicate via `analytics_sink._async_write()` (analytics_sink.py:84).
4. **Sweep**: background job closes idle conversations as `unresolved`.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `shared/persistence/persistence.py` | Modified | DDL for `gestiones` + `gestion_events` |
| `features/analytics/analytics_sink.py` | Modified | New `record_gestion` / `record_gestion_event` reusing `_async_write` |
| `features/analytics/` (new) | New | Outcome catalog (versioned) + derivation rule |
| `api/wiring.py` | Modified | Emit events + terminal close at analytics call-site |
| (new) inactivity sweep job | New | Closes stale conversations as `unresolved` |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Tracking writes break chat flow | Low | Fire-and-forget, never-raise pattern (matches existing sink) |
| Snapshot/journal divergence | Med | Snapshot is derivable from journal; journal is source of truth |
| Sweep races terminal trigger | Med | Sweep only closes rows with null `closed_at` |
| Catalog churn breaks consumers | Low | `schema_version` enum versioning |

## Rollback Plan

Revert the DDL/code commits; new tables are additive and isolated (drop
`gestiones`, `gestion_events` in Postgres and their Doris replicas). Layers 1 & 2
are untouched, so chat and existing analytics keep working.

## Dependencies

- Existing Doris `cobranza_analytics` DB + `analytics_sink` Stream Load path.
- Postgres pool (`COBRANZA_DATABASE_URL` / `COBRANZA_DATABASE_SCHEMA`).

## Success Criteria

- [ ] Every closed conversation has exactly one `gestiones` row with a non-null `outcome`.
- [ ] `gestion_events` journal captures the action sequence; snapshot is derivable from it.
- [ ] Postgres and Doris replicas agree (joinable to Layers 1 & 2 by `conversation_id`).
- [ ] Terminal-immediate and inactivity-sweep triggers both produce correct outcomes.
- [ ] Catalog is tenant-agnostic and versioned; no client n1/n2/n3 leakage.
