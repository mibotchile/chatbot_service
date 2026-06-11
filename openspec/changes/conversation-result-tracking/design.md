# Design: Conversation Result Tracking (Layer 3 — bot-owned, tenant-agnostic)

Reference input: `docs/specs-input/prestamype/diseno-registro-resultado-conversacion.md`.
Proposal: `openspec/changes/conversation-result-tracking/proposal.md`.

This design specifies HOW to add Layer 3 (gestiones snapshot + gestion_events
journal) to the cobranza bot, persisting to Postgres (source of truth) and
replicating to Doris (analytics), reusing the existing per-turn analytics path.

All decisions from the proposal are fixed: generic versioned catalog, journal +
snapshot (NOT SCD2), dual trigger (terminal-immediate + inactivity sweep), reuse
Layers 1 & 2. This document does not re-open them.

---

## 1. Architecture overview

```
                 per turn (conversations.py:333 _spawn_analytics)
chat request ──────────────────────────────────────────────────────┐
                                                                     ▼
                                          ┌──────────────────────────────────┐
                                          │  gestion service (NEW, pure +     │
                                          │  thin IO)                         │
                                          │  - derive_outcome() (pure)        │
                                          │  - append_event()  (PG + Doris)   │
                                          │  - upsert_snapshot() (PG + Doris) │
                                          └──────────────┬───────────────────┘
                          Postgres (truth) ◀────────────┤────────────▶ Doris (replica)
            {schema}.gestiones (snapshot, upsert)        │   cobranza_analytics.bot_gestiones
            {schema}.gestion_events (journal, append)    │   cobranza_analytics.bot_gestion_events
                                                         │
inactivity sweep (NEW periodic worker) ──────────────────┘
  closes stale open rows as `unresolved`
```

Layer boundaries:

| Layer | Store | Built |
|---|---|---|
| 1 ping-pong | PG `conversations.history` + Doris `bot_interactions` | REUSE |
| 2 LLM cost | Doris `bot_llm_usage` | REUSE |
| 3 gestion | PG `gestiones`/`gestion_events` + Doris replicas | **NEW** |

Join key across all three layers: `conversation_id` (== Doris `session_id`).
The per-turn path already passes `session_id=conv.conversation_id`
(wiring.py:170), so the join is free.

Module placement (tenant-agnostic, lives under the analytics feature):

- `apps/agent/features/analytics/gestion_catalog.py` — versioned enums + `SCHEMA_VERSION = 1`.
- `apps/agent/features/analytics/gestion_derivation.py` — pure `derive_outcome()`.
- `apps/agent/features/analytics/gestion_sink.py` — Postgres + Doris write helpers (reuses `analytics_sink._async_write`).
- `apps/agent/features/analytics/gestion_sweep.py` — inactivity sweep worker.

---

## 2. Postgres DDL (source of truth)

Created additively inside `ensure_tables()` (persistence.py:29). No migration:
`CREATE TABLE IF NOT EXISTS`, the DB is empty on first deploy for new tables, and
existing tables are untouched. Both tables are created unconditionally (they are
core, not projection-gated), right after the `conversations` block.

### 2.1 `{schema}.gestiones` — current snapshot (1 row / conversation)

```sql
CREATE TABLE IF NOT EXISTS {schema}.gestiones (
    conversation_id    TEXT PRIMARY KEY,
    tenant_id          TEXT,
    project_uid        TEXT,
    channel            TEXT,
    document           TEXT,          -- debt_context.dni
    account_id         TEXT,
    credit_state       TEXT,          -- al_dia / por_vencer / vencido
    outcome            TEXT,          -- enum (nullable until closed)
    outcome_reason     TEXT,          -- enum, nullable
    capabilities_used  JSONB DEFAULT '[]'::jsonb,   -- accumulated array
    escalated          BOOLEAN DEFAULT FALSE,
    commitment_date    DATE,
    commitment_amount  NUMERIC,
    selected_credit_id TEXT,
    schema_version     SMALLINT DEFAULT 1,
    created_at         TIMESTAMPTZ DEFAULT NOW(),
    closed_at          TIMESTAMPTZ                   -- null = open
);
CREATE INDEX IF NOT EXISTS idx_gestiones_open
    ON {schema}.gestiones(updated_at) WHERE closed_at IS NULL;  -- sweep scan
CREATE INDEX IF NOT EXISTS idx_gestiones_tenant
    ON {schema}.gestiones(tenant_id);
```

`updated_at TIMESTAMPTZ DEFAULT NOW()` is also added (column referenced by the
sweep partial index and bumped on every upsert). Columns map 1:1 to the design
doc "Estructura del registro" table.

### 2.2 `{schema}.gestion_events` — append-only journal

```sql
CREATE TABLE IF NOT EXISTS {schema}.gestion_events (
    event_id        BIGSERIAL PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    ts              TIMESTAMPTZ DEFAULT NOW(),
    event_type      TEXT NOT NULL,   -- capability_used|credit_state_set|terminal|escalation|commitment|proof
    intent          TEXT,
    capability      TEXT,            -- from CAPABILITIES_USED axis
    payload         JSONB DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_gestion_events_conv
    ON {schema}.gestion_events(conversation_id, ts);
```

`BIGSERIAL` (not UUID) — the journal is local-truth; ordering by `event_id` is
cheap and monotonic. Doris uses a UUID label per Stream Load for its own
idempotency, so PG identity does not need to be portable.

### 2.3 Write functions (in persistence.py, alongside `save_conversation`)

```python
async def append_gestion_event(pool, schema, conversation_id, *,
    event_type, intent=None, capability=None, payload=None) -> dict
    # INSERT ... RETURNING event_id, ts  → returns the row dict for Doris replication

async def upsert_gestion(pool, schema, conversation_id, *, fields: dict) -> None
    # INSERT ... ON CONFLICT (conversation_id) DO UPDATE
    # capabilities_used merged (array union), COALESCE for terminal-only fields,
    # updated_at = NOW(). closed_at/outcome only set when provided (terminal).
```

`capabilities_used` accumulation uses jsonb array union in SQL:
`gestiones.capabilities_used || $new` then de-duplicated app-side before write
(small arrays, ≤12 values).

---

## 3. Doris DDL (analytics replica)

Written via `analytics_sink` Stream Load (DUPLICATE KEY, like
`bot_interactions`/`bot_llm_usage`). DDL applied out-of-band against
`cobranza_analytics` (same as the existing two tables — they are pre-created in
Doris, not by the app). Documented here so the ops DDL exists.

### 3.1 `cobranza_analytics.bot_gestiones` (snapshot replica — append, latest wins by datetime_utc)

```sql
CREATE TABLE cobranza_analytics.bot_gestiones (
    datetime_utc       DATETIME,
    conversation_id    VARCHAR(128),   -- == session_id (join key)
    tenant_id          VARCHAR(64),
    project_uid        VARCHAR(64),
    channel            VARCHAR(32),
    document           VARCHAR(64),
    account_id         VARCHAR(64),
    credit_state       VARCHAR(32),
    outcome            VARCHAR(48),
    outcome_reason     VARCHAR(64),
    capabilities_used  VARCHAR(512),   -- JSON-encoded array string
    escalated          BOOLEAN,
    commitment_date    DATE,
    commitment_amount  DECIMAL(18,2),
    selected_credit_id VARCHAR(64),
    schema_version     SMALLINT,
    closed_at          DATETIME
)
DUPLICATE KEY(datetime_utc, conversation_id)
DISTRIBUTED BY HASH(conversation_id) BUCKETS 4;
```

Because Doris uses DUPLICATE KEY (append-only, like the existing tables), each
snapshot upsert emits a NEW Doris row stamped with `datetime_utc`. BI reads the
latest by `MAX(datetime_utc)` per `conversation_id` (or argmax). This matches the
existing analytics pattern — no UNIQUE/aggregate model, no in-place update.

### 3.2 `cobranza_analytics.bot_gestion_events` (journal replica — append)

```sql
CREATE TABLE cobranza_analytics.bot_gestion_events (
    datetime_utc    DATETIME,      -- == event ts (UTC)
    event_id        BIGINT,
    conversation_id VARCHAR(128),  -- join key
    event_type      VARCHAR(48),
    intent          VARCHAR(64),
    capability      VARCHAR(48),
    payload         VARCHAR(2048)  -- JSON-encoded
)
DUPLICATE KEY(datetime_utc, event_id)
DISTRIBUTED BY HASH(conversation_id) BUCKETS 4;
```

Join recipe for BI (the whole point of Layer 3):
`bot_gestiones g JOIN bot_interactions i ON g.conversation_id = i.session_id
LEFT JOIN bot_llm_usage u ON u.session_id = g.conversation_id` → ping-pong +
cost + outcome in one query.

---

## 4. Outcome derivation (pure function)

`gestion_derivation.py`. Pure — no IO, fully unit-testable (TDD anchor). Signature:

```python
def derive_outcome(
    *,
    session_state: dict,        # credit_state, identity flags, flow flags
    resolved_intent: str | None,
    was_escalated: bool,
    identity_failed: bool = False,
    commitment_registered: bool = False,
    proof_submitted: bool = False,
    fallback_exhausted: bool = False,
    identified: bool = False,
    info_provided: bool = False,
) -> tuple[str, str | None]:
    """Return (outcome, outcome_reason). Priority-ordered; first match wins."""
```

Priority (mirrors design doc §"Derivación del outcome"):

1. `identity_failed` → `("identification_failed", "max_identification_retries")`
2. `commitment_registered` → `("payment_commitment_registered", None)`
3. `proof_submitted` → `("payment_proof_submitted", None)`
4. `was_escalated` → `("escalated_to_agent", _reason_for_intent(resolved_intent))`
5. `fallback_exhausted` → `("not_understood", "fallback_exhausted")`
6. `info_provided` → `("info_provided", None)`
7. `identified` → `("identified", None)`
8. default → `("unresolved", None)`

`_reason_for_intent(intent)` maps the escalation-triggering intent to an
`outcome_reason` enum (cannot_pay, requested_alternatives,
commitment_beyond_window, wants_full_payment, pay_installment,
proof_other_installment, explicit_agent_request, out_of_hours,
fallback_exhausted). Unknown intent → `None`. The mapping table is a dict in
`gestion_catalog.py` so it grows with `SCHEMA_VERSION` without touching logic.

Flag sourcing (verified anchors, all read from the per-turn `result`/`conv`):

| Flag | Source |
|---|---|
| `session_state` | `conv.session_state` (agent.py:294 sets `credit_state`) |
| `resolved_intent` | `(result.get("metadata") or {}).get("intent")` (conversations.py:311) |
| `was_escalated` | `was_escalated(tool_pairs)` (chathub_adapter.py:183) |
| `commitment_registered`/`proof_submitted`/`identity_failed`/`fallback_exhausted` | derived from `tool_pairs` names + `session_state` flags (see §5.2) |

Note: the doc cites `chathub_adapter.py` for `was_escalated` — the real path is
`apps/agent/features/messaging/chathub_adapter.py:183`.

---

## 5. Terminal-immediate trigger

### 5.1 Injection point

`apps/agent/api/routers/conversations.py`, right after the existing
`m._spawn_analytics(...)` call (conversations.py:327-333) and after
`await conv.add_assistant_message_async(content)` (conversations.py:349). The
terminal hook runs in the SAME fire-and-forget style — a new
`m._spawn_gestion(...)` background task, never raising into the request.

We add `_emit_gestion` + `_spawn_gestion` to `wiring.py` next to
`_emit_analytics`/`_spawn_analytics` (wiring.py:148-189), reusing
`_tenant_project_uid` (wiring.py:92) and the `_analytics_tasks` ref-keeping set.

Per turn `_emit_gestion` does TWO things:

1. Append the turn's journal events (always — see §6).
2. Run `derive_outcome(...)`; if a terminal outcome is produced (anything other
   than `unresolved`), set `outcome`/`outcome_reason`/`closed_at` on the snapshot
   AND emit a `terminal` journal event. Non-terminal turns upsert the snapshot
   with `closed_at=NULL` (open) and refreshed `capabilities_used`/`credit_state`.

### 5.2 Terminal-intent detection

A turn is terminal when any of these hold (checked inside `_emit_gestion`):

- `was_escalated(tool_pairs)` is True.
- `tool_pairs` contains a commitment tool result → `commitment_registered`.
- `tool_pairs` contains a comprobante/proof tool result → `proof_submitted`.
- `session_state` shows identity gate exhausted → `identity_failed`.
- `session_state`/result shows 2nd-strike fallback → `fallback_exhausted`.
- `resolved_intent` is a terminal info/identify intent that closes the flow
  (`info_provided` / `identified`).

The exact tool-name set lives in `gestion_catalog.TERMINAL_SIGNALS` (a dict of
`signal → tool_names/state_keys`) so detection is data-driven and tenant-agnostic.
Idempotency: if the snapshot already has `closed_at IS NOT NULL`, terminal
re-derivation is skipped (first terminal wins; sweep also respects this).

---

## 6. Per-turn journal append path

Every turn (terminal or not) appends 0..N events:

- For each capability exercised this turn → one `capability_used` event
  (`capability` set from `CAPABILITIES_USED` axis, mapped from `resolved_intent`
  / tool names via `gestion_catalog.INTENT_TO_CAPABILITY`).
- When `session_state['credit_state']` is (re)set → one `credit_state_set` event.
- On escalation → `escalation` event; on commitment → `commitment`; on proof →
  `proof`; on terminal close → `terminal`.

Each append:
1. `append_gestion_event(pool, schema, conversation_id, ...)` → PG INSERT
   RETURNING the row.
2. Replicate that row to Doris via `gestion_sink.record_gestion_event(row)`
   (wraps `analytics_sink._async_write("bot_gestion_events", [doris_row])`).

The snapshot upsert (`gestiones`) happens once per turn after events, carrying
the merged `capabilities_used` and current `credit_state`, and is replicated via
`gestion_sink.record_gestion(snapshot_row)` →
`analytics_sink._async_write("bot_gestiones", [row])`.

---

## 7. Doris replication — reuse, do NOT build a new client

`gestion_sink.py` adds exactly two thin functions that mirror
`record_interaction`/`record_llm_usage` (analytics_sink.py:94, 140):

```python
async def record_gestion(*, snapshot: dict) -> None:
    row = _to_doris_gestion(snapshot)          # map PG row → Doris shape, JSON-encode arrays
    await analytics_sink._async_write("bot_gestiones", [row])

async def record_gestion_event(*, event: dict) -> None:
    row = _to_doris_event(event)
    await analytics_sink._async_write("bot_gestion_events", [row])
```

- Uses the EXISTING `_async_write` (analytics_sink.py:84): fire-and-forget,
  `asyncio.to_thread`, never raises, no-op when `analytics_enabled()` is False.
- Uses the EXISTING `_client()` / `_write_rows` Stream Load path. No new
  `DorisClient`, no new transport.
- `datetime_utc`/`closed_at` formatted with `_now_utc()` (analytics_sink.py:40)
  / the event `ts` as `%Y-%m-%d %H:%M:%S` UTC. `capabilities_used` and `payload`
  are JSON-encoded to strings (Doris columns are VARCHAR).

The same fire-and-forget guarantee as Layers 1 & 2: a gestion write failure logs
a warning and is swallowed — chat is never affected.

---

## 8. Inactivity sweep

`gestion_sweep.py` — a periodic asyncio task started at app startup (next to the
WhatsApp webhook registration in wiring startup helpers). Closes abandoned
conversations the terminal trigger never caught.

- **Where it runs**: a single background `asyncio.Task` loop
  (`while True: await asyncio.sleep(interval); await _sweep_once()`), launched in
  the lifespan/startup path, ref-kept like `_analytics_tasks`. Single-process; if
  the bot scales horizontally later, a `SELECT ... FOR UPDATE SKIP LOCKED` guard
  (below) keeps it safe.
- **Stale query** (per tenant TTL):

```sql
UPDATE {schema}.gestiones
   SET outcome = 'unresolved',
       closed_at = NOW(),
       updated_at = NOW()
 WHERE closed_at IS NULL
   AND updated_at < NOW() - ($1 * INTERVAL '1 minute')
RETURNING conversation_id, tenant_id, project_uid, ...;
```

  `$1` = TTL minutes resolved per tenant. Uses the
  `idx_gestiones_open` partial index. For each row closed, emit a `terminal`
  journal event (`outcome=unresolved`, reason `null`) and replicate both the
  event and the updated snapshot to Doris via `gestion_sink`.
- **TTL config per tenant**: `tenant.config.json` →
  `cobranza.gestion_inactivity_ttl_minutes` (default e.g. 30). Resolved via the
  existing `_load_tenant_config` (wiring.py:83). The sweep groups open rows by
  `tenant_id`, applies each tenant's TTL. Sweep interval (how often the loop
  runs) is a global setting, e.g. `settings.gestion_sweep_interval_seconds`
  (default 300).
- **Race with terminal trigger**: the sweep `UPDATE` filters `closed_at IS NULL`,
  so a row already closed by the terminal hook is never overwritten. Both writers
  are last-writer-safe on Postgres because the terminal hook also gates on
  `closed_at IS NULL` for the close transition.

---

## 9. Migration / back-compat

- **Additive only.** New tables via `CREATE TABLE IF NOT EXISTS` inside
  `ensure_tables()`. Existing `conversations`/projection tables untouched. No
  ALTER on existing tables, no data backfill.
- **Doris**: two new DUPLICATE-KEY tables in `cobranza_analytics`, pre-created
  out-of-band (same operational model as `bot_interactions`/`bot_llm_usage`).
  Until they exist, `_async_write` failures are swallowed — chat still works.
- **Feature gating**: gestion writes are guarded by the same
  `analytics_enabled()` check for the Doris leg; the Postgres leg is guarded by
  `m.store.db_pool is not None` (matching the existing `upsert_debtor` guard,
  conversations.py:358). When neither is configured, the whole Layer 3 is a
  no-op.
- **Rollback**: revert code commits; `DROP TABLE {schema}.gestiones,
  {schema}.gestion_events` and the two Doris tables. Layers 1 & 2 untouched.

---

## 10. ADR-style decisions

### ADR-1: Journal + snapshot, NOT SCD2
**Decision**: append-only `gestion_events` (truth) + upsert `gestiones`
(convenience). **Rationale**: the journal is inherently historical and captures
exact action sequence; any point-in-time snapshot is derivable from it. Less
complexity than SCD2 valid_from/valid_to/is_current, and a natural fit for an
append-only Doris fact. **Rejected**: SCD2 (row versioning) — more write
complexity, harder Doris mapping, no extra analytical value here.

### ADR-2: Reuse `analytics_sink._async_write`, no new Doris client
**Decision**: gestion Doris writes go through the existing Stream Load path.
**Rationale**: identical fire-and-forget/never-raise contract; one cached
`DorisClient`; less code, consistent ops. **Rejected**: a dedicated gestion
client — duplicate transport, two failure models, more surface.

### ADR-3: Dual trigger (terminal-immediate + inactivity sweep)
**Decision**: close on terminal intent immediately; a periodic worker closes
stale-open rows as `unresolved`. **Rationale**: terminal intents give precise,
fast outcomes; the sweep guarantees EVERY conversation eventually closes
(abandonment is the common case in chat). **Rejected**: sweep-only (loses
precision/timeliness) and terminal-only (abandoned chats never close).

### ADR-4: Doris DUPLICATE KEY with datetime_utc, latest-wins read
**Decision**: snapshot replica is append-with-timestamp; BI reads argmax by
`datetime_utc`. **Rationale**: matches the existing analytics tables exactly; no
new Doris data model. **Rejected**: UNIQUE/aggregate Doris model for in-place
update — diverges from the established pattern, more ops complexity.

### ADR-5: Pure derivation function isolated from IO
**Decision**: `derive_outcome()` is pure `(flags) -> (outcome, reason)`.
**Rationale**: priority rule is the riskiest logic; pure = fully unit-testable
under Strict TDD, reusable by both terminal hook and sweep. **Rejected**:
embedding derivation inside the write path — untestable, duplicated.

### ADR-6: Tenant-agnostic catalog, versioned via `schema_version`
**Decision**: enums + intent→capability + intent→reason maps live in
`gestion_catalog.py` with `SCHEMA_VERSION=1`; no client n1/n2/n3 anywhere.
**Rationale**: one catalog for all tenants; adding a value bumps the version
without breaking consumers; client homologation is an external per-tenant layer
(out of scope). **Rejected**: per-tenant outcome catalogs — leaks client
vocabulary into the core, defeats the agnostic goal.

---

## 11. Test anchors (Strict TDD)

- `derive_outcome` — one test per priority branch + precedence (identity_failed
  beats escalation, etc.) + unknown-intent reason → None. Pure, no fixtures.
- `_reason_for_intent` — table-driven over the intent→reason map.
- `append_gestion_event` / `upsert_gestion` — against a test Postgres schema:
  insert returns event_id; upsert merges capabilities, COALESCE terminal fields,
  idempotent close (second terminal does not overwrite first).
- sweep query — closes only `closed_at IS NULL` rows past TTL; respects per-tenant
  TTL; emits unresolved terminal event.
- `gestion_sink` — asserts it calls `analytics_sink._async_write` with the right
  table + mapped row shape (mock `_async_write`); never raises when Doris down.
