# Spec: platform-multitype-engine

> Change name: `platform-multitype-engine`
> Artifact store: hybrid
> Ricky's refinements applied: per-type projection table is OPTIONAL per agent_type; type registry is code-now + swappable abstraction.

---

## New Capability: record-model

### Requirement: Neutral Record Entity

The system MUST provide a `Record` domain entity in `features/conversation/` that represents the common interlocutor state across all agent types: identity fields, contact fields, and progressive-capture level. `Record` MUST be typed as an explicit domain entity, not a generic data container.

#### Scenario: Record created for new conversation

- GIVEN a new inbound conversation for any `agent_type`
- WHEN the conversation session initializes
- THEN a `Record` is created with identity, contact, and capture-level fields at their default (empty/unknown) state
- AND no domain-specific projection is required at this point

#### Scenario: Record embedded in conversations table

- GIVEN the persistence layer boots and calls `ensure_tables`
- WHEN the `conversations` table is inspected
- THEN it contains `record_data` (JSONB) and `record_level` (text/int) columns
- AND no `debtor_data` or `debtor_level` columns exist

#### Scenario: Record fields are NOT cobranza-specific

- GIVEN any `agent_type` other than `cobranza` is configured
- WHEN a conversation is started and a `Record` is created
- THEN the `Record` contains only neutral identity/contact/capture fields
- AND no debt-specific field is present in the `Record`

---

### Requirement: Debtor as Composition over Record

`Debtor` in `features/cobranza/` MUST be expressed as a composition over `Record` (holds a `Record` instance as an attribute). Inheritance from `Record` is PROHIBITED. `Debtor` MUST add debt-specific fields on top of `Record`; it MUST NOT duplicate the neutral fields.

#### Scenario: Debtor contains a Record

- GIVEN a cobranza conversation with an identified debtor
- WHEN `Debtor` state is accessed
- THEN `debtor.record` exposes the underlying `Record` (identity + contact + capture level)
- AND `debtor` additionally exposes debt-domain fields (e.g. debt amount, commitment)

#### Scenario: Cobranza behavior is unchanged

- GIVEN the full test suite (`uv run pytest tests/ -v`, ~366 tests baseline)
- WHEN run after the Record/Debtor composition refactor
- THEN all tests pass (zero regressions)
- AND no cobranza tool, skill, gate, or LLM response changes

---

## New Capability: agent-type-registry

### Requirement: Type Registry with Swappable Source

The system MUST provide an `AgentTypeRegistry` that maps `agent_type` (string key) to a descriptor `{features, tools, skills, gate_model, state_spec}`. The registry MUST be implemented as a code-based structure (dict/factory) for the initial release. The registry source MUST be accessed through an abstraction (interface/protocol) so it can be swapped to a DB-backed implementation without rewriting consumers.

#### Scenario: Registry resolves cobranza type

- GIVEN the type registry is initialized at boot
- WHEN `agent_type = "cobranza"` is looked up
- THEN the registry returns a descriptor containing the cobranza features, tools, skills, gate model, and state spec
- AND no other type entry exists in the registry (registry has exactly one entry)

#### Scenario: Registry source is swappable without consumer change

- GIVEN a code-based registry source is active
- WHEN a new DB-backed registry source is provided implementing the same interface/protocol
- THEN all consumers (ToolRegistry, ensure_tables, gate) MUST function without modification

#### Scenario: Unknown agent_type raises a clear error

- GIVEN the registry is consulted
- WHEN an `agent_type` not present in the registry is requested
- THEN the system raises a well-typed error (not a silent KeyError/None)
- AND the error message identifies the missing type

---

### Requirement: agent_type as Tenancy Config Dimension

The system MUST resolve `agent_type` from the tenant configuration (tenancy layer). Every tenant MUST declare exactly one `agent_type`. The resolved `agent_type` MUST be used by downstream components (ToolRegistry, gate, ensure_tables) to compose domain behavior.

#### Scenario: agent_type resolved from tenant config

- GIVEN a tenant configuration with `agent_type = "cobranza"`
- WHEN the engine initializes for that tenant
- THEN `agent_type` is available to ToolRegistry, gate, and persistence without re-reading the config

#### Scenario: Missing agent_type in tenant config is rejected

- GIVEN a tenant configuration that omits `agent_type`
- WHEN the engine initializes
- THEN initialization fails fast with a descriptive error
- AND no conversation is started

---

## New Capability: composable-tool-registry

### Requirement: ToolRegistry Composed per agent_type

The `ToolRegistry` MUST assemble its tool set by consulting the type registry for the resolved `agent_type`. The gate MUST be per-domain (declared in the type descriptor), not global. Only tools declared for the active `agent_type` MUST be registered; tools from other domains MUST NOT be reachable.

#### Scenario: Cobranza tools are registered for cobranza agent_type

- GIVEN `agent_type = "cobranza"` resolved from tenancy
- WHEN `ToolRegistry` is assembled
- THEN cobranza-domain tools (consultar_deuda, validar_comprobante, etc.) are present
- AND the gate model is the cobranza hard-DNI gate

#### Scenario: Per-domain gate applied

- GIVEN a conversation with `agent_type = "cobranza"`
- WHEN a tool call is attempted without passing the cobranza gate
- THEN the tool call is blocked by the domain gate
- AND behavior is identical to today's gate behavior (zero regression)

---

## Modified Capability: cobranza-state

### Requirement: Debtor Refactored to Compose Record

`DebtorState` / `Debtor` in `features/cobranza/` MUST be refactored to hold a `Record` instance instead of duplicating identity/contact fields. All public behavior (fields accessed by tools, LLM prompt builders, and persistence) MUST remain identical to the current implementation.

(Previously: DebtorState in features/conversation/debtor_state.py was a flat struct with both identity/contact and debt fields; no Record concept existed.)

#### Scenario: Debtor public API unchanged

- GIVEN existing cobranza tools access `debtor.name`, `debtor.phone`, `debtor.debt_amount`, etc.
- WHEN `Debtor` is refactored to compose `Record`
- THEN all attribute accesses by existing code resolve to the same values
- AND no tool or prompt builder requires modification

#### Scenario: All cobranza tests remain green

- GIVEN `uv run pytest tests/ -v` with the ~366 baseline
- WHEN cobranza-state refactor is applied
- THEN all tests pass with zero regressions

---

## Modified Capability: persistence

### Requirement: Drop sorelia_ Prefix — Table Rename

The persistence layer MUST use `conversations` and `visitors` as table names. The legacy `sorelia_` prefix MUST NOT appear in any table name, column name, or SQL in production code. The column `lead_data` in `visitors` MUST be renamed to a neutral name (e.g. `visitor_data`).

#### Scenario: ensure_tables creates correctly named tables

- GIVEN the database is empty (olimpo DB fresh/empty state)
- WHEN `ensure_tables` runs on boot
- THEN tables named `conversations` and `visitors` are created
- AND no `sorelia_*` table is created

#### Scenario: conversations table embeds Record fields

- GIVEN `ensure_tables` runs
- WHEN the `conversations` table schema is inspected
- THEN it contains `record_data` and `record_level` columns
- AND does NOT contain `debtor_data` or `debtor_level` columns

#### Scenario: visitors table has no lead_data column

- GIVEN `ensure_tables` runs
- WHEN the `visitors` table schema is inspected
- THEN the column formerly named `lead_data` does NOT exist
- AND a neutral-named column (e.g. `visitor_data`) is present in its place

---

### Requirement: Per-Type Projection Table is Optional per agent_type

The type descriptor in the registry MAY declare a `projection_table` (e.g. `debtors` for cobranza). When declared, `ensure_tables` MUST create that table. When NOT declared, no per-type table is created. The system MUST function correctly when no projection table is declared.

#### Scenario: cobranza declares debtors table and it is created

- GIVEN `agent_type = "cobranza"` with `projection_table = "debtors"` in its descriptor
- WHEN `ensure_tables` runs
- THEN a `debtors` table is created
- AND it is scoped to the cobranza projection (debt-specific columns)

#### Scenario: agent_type without projection_table skips per-type table

- GIVEN a future `agent_type` whose descriptor has no `projection_table`
- WHEN `ensure_tables` runs
- THEN only the common `conversations` and `visitors` tables are created
- AND no error occurs

---

### Requirement: Empty-DB Recreate (No Migration)

Because the olimpo database is empty, the deployment procedure MUST drop any legacy `sorelia_*` tables and allow `ensure_tables` on boot to recreate all tables with new names. No data migration script is required or permitted.

#### Scenario: Boot after table recreate succeeds

- GIVEN legacy `sorelia_*` tables have been dropped
- WHEN the application boots and `ensure_tables` runs
- THEN the log contains `PostgreSQL persistence active`
- AND the application reaches a healthy state
- AND a cobranza conversation can be started end-to-end

#### Scenario: No sorelia_ tables exist after boot

- GIVEN `ensure_tables` has run on a fresh DB
- WHEN the DB schema is inspected
- THEN zero tables with the `sorelia_` prefix exist

---

## Cross-Cutting: Zero Behavior Change for Cobranza

### Requirement: Cobranza Test Suite as Contract

The ~366 passing tests in `uv run pytest tests/ -v` at the time this change is applied MUST all pass after every phase of this change is merged. Any new test added during this change MUST also pass. A red test introduced by this change is a blocker — it MUST be fixed before the implementation is considered complete.

#### Scenario: Full test suite passes after each slice

- GIVEN each implementation slice is applied (Record extraction, registry, ToolRegistry, persistence rename)
- WHEN `uv run pytest tests/ -v` is run after each slice
- THEN all baseline tests pass
- AND no cobranza behavior (tool responses, gate, LLM prompts) is altered

---

## Non-Requirements (Explicit Out of Scope)

- `features/creditos` and `features/inmobiliario` are NOT created.
- No data migration: olimpo DB is empty, recreate only.
- Repo rename `chatbot-cobranza → olimpo` is deferred.
- The `debtor` naming stays; no further rename at this time.
