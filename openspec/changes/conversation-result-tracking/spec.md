# Conversation Result Tracking — Specification

## Purpose

Define the behavioral contract for Layer-3 of the cobranza chatbot data model:
a bot-owned, tenant-agnostic, versioned record of each conversation's terminal
outcome, stored in Postgres (`gestiones` + `gestion_events`) and replicated to
Doris (`bot_gestiones` + `bot_gestion_events`), joinable to Layers 1 and 2 by
`conversation_id` / `session_id`.

---

## Requirements

### Requirement R1: Outcome Derivation at Conversation Close

The system MUST derive a single `outcome` value for every closed conversation
using the following priority rule (first match wins):

| Priority | Condition | outcome | outcome_reason |
|----------|-----------|---------|----------------|
| 1 | Identity gate failed (max retries) | `identification_failed` | `max_identification_retries` |
| 2 | Payment commitment registered | `payment_commitment_registered` | `null` |
| 3 | Payment proof submitted | `payment_proof_submitted` | `null` |
| 4 | `was_escalated()` is true | `escalated_to_agent` | reason from triggering intent |
| 5 | Second-strike fallback exhausted | `not_understood` | `fallback_exhausted` |
| 6 | Info delivered and conversation closed | `info_provided` | `null` |
| 7 | Identity verified, no terminal action | `identified` | `null` |
| 8 | None of the above | `unresolved` | `null` |

The `outcome` field MUST be non-null on every row in `gestiones` where
`closed_at` is non-null. `outcome_reason` MAY be null when the outcome is
self-explanatory.

#### Scenario R1-a: Commitment registered — outcome wins over info_provided

- GIVEN a conversation where the debtor queried their balance AND registered a payment commitment
- WHEN the conversation is closed (terminal intent detected)
- THEN `outcome = payment_commitment_registered` (priority 2 beats priority 6)
- AND `outcome_reason = null`

#### Scenario R1-b: Escalation with reason

- GIVEN a debtor whose commitment date exceeded the 2-day window
- WHEN `was_escalated()` returns true with triggering intent `commitment_beyond_window`
- THEN `outcome = escalated_to_agent`
- AND `outcome_reason = commitment_beyond_window`

#### Scenario R1-c: Identification failure

- GIVEN a debtor who exhausted the maximum DNI verification retries
- WHEN the identity gate closes the conversation
- THEN `outcome = identification_failed`
- AND `outcome_reason = max_identification_retries`
- AND no later priority rule can override this outcome

#### Scenario R1-d: Inactivity sweep default

- GIVEN a conversation that received no user message for longer than the tenant TTL
- WHEN the inactivity sweep job closes the conversation
- THEN `outcome = unresolved` AND `closed_at` is set
- AND no higher-priority outcome was recorded in `gestion_events`

---

### Requirement R2: Append-Only Gestion Events Journal

The system MUST maintain an append-only `gestion_events` table in Postgres
(schema `{COBRANZA_DATABASE_SCHEMA}`) with columns: `event_id`, `conversation_id`,
`ts`, `event_type`, `intent`, `capability`, `payload`.

Allowed `event_type` values: `capability_used`, `credit_state_set`, `terminal`,
`escalation`, `commitment`, `proof`.

One event MUST be appended per distinct user action. Events MUST NOT be updated
or deleted. The journal MUST be sufficient to derive any past snapshot state.

#### Scenario R2-a: Capability event on each distinct capability exercise

- GIVEN a user message that triggers the `consulta_deuda` capability
- WHEN the turn completes
- THEN a `capability_used` event is appended with `capability = consulta_deuda`
- AND `conversation_id` matches the active conversation

#### Scenario R2-b: Terminal event on conversation close

- GIVEN a conversation reaching a terminal intent
- WHEN the outcome is derived
- THEN a `terminal` event is appended with `payload` containing `{outcome, outcome_reason, closed_at}`

#### Scenario R2-c: Journal is immutable

- GIVEN any `gestion_events` row
- WHEN the system processes subsequent turns
- THEN no UPDATE or DELETE is issued against existing journal rows

#### Scenario R2-d: Snapshot derivable from journal

- GIVEN the full set of `gestion_events` rows for a `conversation_id`
- WHEN a consumer replays the journal in `ts` order
- THEN it can reconstruct the final `gestiones` snapshot state including all `capabilities_used`

---

### Requirement R3: Gestiones Snapshot (1 row per conversation)

The system MUST maintain a `gestiones` table with exactly one row per
`conversation_id`. The row MUST be upserted (not inserted) on each relevant
turn. Columns MUST include all fields in the design doc structure: `conversation_id`,
`tenant_id`, `project_uid`, `channel`, `document`, `account_id`, `credit_state`,
`outcome`, `outcome_reason`, `capabilities_used` (JSONB array), `escalated`,
`commitment_date`, `commitment_amount`, `selected_credit_id`, `schema_version`,
`created_at`, `closed_at`.

`closed_at` MUST be null until the conversation closes. `outcome` MUST be null
until `closed_at` is set.

#### Scenario R3-a: Snapshot created on first turn

- GIVEN a new `conversation_id` not yet in `gestiones`
- WHEN the first analytics event is emitted
- THEN a row is inserted with `closed_at = null`, `outcome = null`, `capabilities_used = []`

#### Scenario R3-b: capabilities_used accumulates across turns

- GIVEN an existing snapshot with `capabilities_used = ['consulta_deuda']`
- WHEN the user exercises `comprobante` in a later turn
- THEN the snapshot row is upserted with `capabilities_used = ['consulta_deuda', 'comprobante']`
- AND no duplicate capability value is added if the capability was already present

#### Scenario R3-c: Snapshot closed on terminal event

- GIVEN an open snapshot (`closed_at = null`)
- WHEN a terminal event is processed
- THEN `outcome`, `outcome_reason`, and `closed_at` are set in the same upsert
- AND the row is not duplicated

---

### Requirement R4: Terminal-Immediate Trigger

The system MUST detect terminal intents at the turn call-site
(`wiring.py:_spawn_analytics`) and close the conversation immediately by
deriving the outcome, setting `closed_at`, appending the terminal journal event,
and upserting the snapshot — all within the same fire-and-forget analytics
coroutine.

Terminal intents include: payment commitment registered, payment proof submitted,
escalation triggered (`was_escalated()`), second-strike fallback, identity gate
failure.

The close operation MUST NOT block the HTTP response to the caller.

#### Scenario R4-a: Commitment intent closes immediately

- GIVEN `resolved_intent = payment_commitment` at turn end
- WHEN `_spawn_analytics` fires
- THEN outcome derivation runs, `closed_at` is set, terminal event is appended
- AND the HTTP response to the user is not delayed by this operation

#### Scenario R4-b: Non-terminal intent does not close

- GIVEN `resolved_intent = consulta_deuda`
- WHEN `_spawn_analytics` fires
- THEN `closed_at` remains null and no terminal event is appended
- AND a `capability_used` event IS appended

---

### Requirement R5: Inactivity Sweep for Unresolved Conversations

The system MUST provide a background job that closes all `gestiones` rows where
`closed_at IS NULL` and the last activity timestamp is older than the configured
TTL. The TTL MUST be configurable per tenant (default: 30 minutes). Closed rows
MUST receive `outcome = unresolved` and `closed_at` set to the sweep execution
time.

The sweep MUST NOT close rows that already have a non-null `closed_at` (race
safety).

#### Scenario R5-a: Sweep closes idle conversation as unresolved

- GIVEN a `gestiones` row with `closed_at = null` and last activity 35 minutes ago
- AND tenant TTL is 30 minutes
- WHEN the sweep job runs
- THEN `outcome = unresolved`, `closed_at` is set, terminal journal event appended

#### Scenario R5-b: Sweep skips already-closed rows

- GIVEN a `gestiones` row with `closed_at` already set
- WHEN the sweep job runs
- THEN the row is not modified

#### Scenario R5-c: Sweep skips recently active conversations

- GIVEN a `gestiones` row with `closed_at = null` and last activity 10 minutes ago
- AND tenant TTL is 30 minutes
- WHEN the sweep job runs
- THEN the row is not modified

---

### Requirement R6: Doris Replication via Existing Analytics Sink

The system MUST replicate every `gestiones` upsert and every `gestion_events`
append to Doris tables `bot_gestiones` and `bot_gestion_events` in the
`cobranza_analytics` database, using the existing `analytics_sink._async_write()`
(fire-and-forget, never-raise) path.

Both Doris tables MUST include `conversation_id` so they are joinable to
`bot_interactions` (Layer 1) and `bot_llm_usage` (Layer 2) by `conversation_id`
/ `session_id`.

The Postgres write MUST be the source of truth; Doris replication failure MUST
NOT prevent the Postgres write.

#### Scenario R6-a: Snapshot upsert replicates to Doris

- GIVEN a `gestiones` row is upserted in Postgres
- WHEN `analytics_sink._async_write('bot_gestiones', rows)` is called
- THEN the row is stream-loaded to Doris `cobranza_analytics.bot_gestiones`
- AND a Doris failure does not raise or affect the Postgres write

#### Scenario R6-b: Doris rows joinable to Layer 1

- GIVEN `bot_gestiones.conversation_id = X` and `bot_interactions.session_id = X`
- WHEN a BI query joins the two tables on that key
- THEN the join succeeds and returns the combined ping-pong + outcome data

---

### Requirement R7: Versioned and Extensible Outcome Catalog

The system MUST include a `schema_version` field (SMALLINT, constant `= 1` for
this release) in every `gestiones` row. The outcome and outcome_reason enumerations
MUST be defined in a versioned catalog module.

Adding a new outcome value in a future version MUST NOT require changes to
existing consumers that ignore unknown values. The catalog MUST be the single
source of truth — outcome strings MUST NOT be hardcoded outside the catalog
module.

#### Scenario R7-a: schema_version present on every row

- GIVEN any `gestiones` row written by this system
- THEN `schema_version = 1`

#### Scenario R7-b: Unknown outcome value ignored by consumer

- GIVEN a consumer that reads `gestiones` and handles only known v1 outcomes
- WHEN a v2 row with a new outcome value is inserted (future)
- THEN the consumer processes known outcomes normally and skips/defaults unknown ones
- AND no exception is raised

#### Scenario R7-c: Outcome string sourced from catalog

- GIVEN the outcome derivation logic
- WHEN any outcome value is assigned
- THEN the value is read from the versioned catalog constants, not from a string literal in business logic

---

### Requirement R8: Tenant-Agnostic — No Client Tipification Leakage

The system MUST NOT write to `GENERAL.mibotair_results`. The system MUST NOT
include any n1/n2/n3 tipification logic, Prestamype-specific field, or
client-specific outcome mapping. The outcome catalog and `gestiones` schema MUST
use generic cobranza vocabulary only.

The `tenant_id` field in `gestiones` identifies the tenant for operational
purposes only (routing, TTL config); it MUST NOT control outcome derivation
logic.

#### Scenario R8-a: mibotair_results not written

- GIVEN any conversation close event (terminal or sweep)
- WHEN outcome derivation and persistence execute
- THEN no write is issued to `GENERAL.mibotair_results`
- AND no import of or reference to `mibotair_results` exists in the new code

#### Scenario R8-b: Same outcome derivation for all tenants

- GIVEN two conversations from different tenants with identical event sequences
- WHEN outcome is derived for both
- THEN both receive the same `outcome` value
- AND `tenant_id` does not influence the derivation result

#### Scenario R8-c: Homologation mapping absent from this layer

- GIVEN the `gestiones` table schema
- THEN no column named `n1`, `n2`, `n3`, `tipificacion`, or equivalent client-specific field exists
