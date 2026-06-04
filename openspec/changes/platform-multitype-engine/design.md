# Technical Design: platform-multitype-engine (Olimpo)

> Phase: DESIGN (the HOW at architectural level). Tasks live in `tasks.md`.
> Authoritative vision: `reports/olimpo-platform-arquitectura-2026-06-03.md`.
> Contract: ~366 green tests. ZERO behavior change for cobranza. STRICT TDD.
> Code root is `apps/agent/` (import roots: `features.`, `shared.`, `tenancy.`, `api.`).

## Decision summary (lead with the answer)

| # | Decision | Verdict |
|---|----------|---------|
| A | Neutral `Record` entity + `Debtor` by composition | `Record` in `features/conversation/record.py`; `Debtor` in `features/cobranza/debtor.py` wraps a `Record`. `DebtorState` becomes a thin shim re-exporting cobranza's spec — no behavior change. |
| B | Persistence neutral names | `conversations` (cols `record_data`, `record_level`), `visitors` (fix `lead_data`→`record_data`). Empty-DB drop+recreate, no migration. Redis prefix `sorelia:`→`olimpo:`. |
| B2 | cobranza `debtors` projection: keep or drop | **KEEP** — rename `sorelia_debtors`→`debtors`. Dashboard has 6 queries that need the denormalized columnar/JOIN shape. Evidence below. |
| C | Type registry | `AgentTypeRegistry` protocol in `shared/ports/`; in-code impl in `tenancy/agent_types/`. `agent_type` declared in `tenant.config.json`. One entry: `cobranza`. DB-swappable later. |
| D | ToolRegistry per type + per-domain gate | Extend the PR8 DI port. A `ToolSetBuilder` per agent_type assembles tools + gate. Cobranza keeps its hard DNI gate, now declared by the registry. |
| E | Slice ordering | 8 slices, each green + rollback. Resolves proposal open-decision #3. |
| F | Dependency rule | Registry port in `shared/ports/`, impls in `tenancy/`. No `shared→features`. Verified clean today. |

---

## A. Domain model: Record (neutral) + Debtor (composition)

### A.1 The neutral capture machine

Today `features/conversation/debtor_state.py` hardcodes cobranza field sets
(`CONTACT_FIELDS`, `INTEREST_FIELDS`, `ENRICHMENT_FIELDS`) and level names
(`VISITOR`/`PRE_DEBTOR`/`DEBTOR`/`DEBTOR_VERIFIED`) directly into the class.
That class IS the generic progressive-capture machine — it was the sorelia
`lead_machine` specialized to cobranza. We separate the *machine* (neutral) from
the *spec* (per-type).

**New neutral entity** — `features/conversation/record.py`:

```python
# Record = the neutral case/contact of the interlocutor:
#   identity + contact + progressive-capture state. Contact-center term.
# It is a DOMAIN ENTITY, not a generic DB row.

@dataclass(frozen=True)
class CaptureSpec:
    """Per-type description of the progressive-capture funnel."""
    contact_fields: frozenset[str]
    interest_fields: frozenset[str]
    enrichment_fields: frozenset[str]
    # ordered (predicate, level_name); first satisfied wins; last = default.
    levels: tuple[tuple[Callable[["Record"], bool], str], ...]
    default_level: str

class Record:
    """Neutral progressive-capture state machine, parametrized by a CaptureSpec."""
    def __init__(self, spec: CaptureSpec, initial_data=None, on_transition=None):
        self.spec = spec
        self.collected: dict = dict(initial_data or {})
        self._on_transition = on_transition

    @property
    def level(self) -> str: ...        # evaluates spec.levels in order
    def update(self, data: dict) -> None: ...   # identical merge+transition semantics
    def get_status(self) -> dict: ...
    def to_dict(self) -> dict: ...
    @classmethod
    def from_dict(cls, spec, data) -> "Record": ...
```

The machine logic (`update`, transition callback firing, `get_status` missing-fields,
`to_dict`/`from_dict`) is moved VERBATIM from `DebtorState` — only the field sets and
level computation are read from the injected `CaptureSpec` instead of module globals.

### A.2 cobranza's spec + Debtor projection

`features/cobranza/debtor.py`:

```python
COBRANZA_SPEC = CaptureSpec(
    contact_fields=frozenset({"name", "phone", "email"}),
    interest_fields=frozenset({"debt_amount","days_overdue","account_id",
                               "payment_intent","dispute_reason"}),
    enrichment_fields=frozenset({"income","document_number","document_type","employer"}),
    levels=(
        (lambda r: _has_contact(r) and _has_enrichment(r), "DEBTOR_VERIFIED"),
        (lambda r: _has_contact(r),                         "DEBTOR"),
        (lambda r: _has_interest(r),                        "PRE_DEBTOR"),
    ),
    default_level="VISITOR",
)

class Debtor:
    """Cobranza projection OVER a Record by COMPOSITION (not inheritance)."""
    def __init__(self, record: Record):
        self._record = record           # has-a, not is-a
    # delegates level/collected/update/get_status to self._record
    # adds cobranza-specific accessors (debt view) when needed
```

The level thresholds (`>=2` interest, `>=2` enrichment) and exact names are preserved
in `_has_*` helpers, so `level` returns identical strings for identical input.

### A.3 No-behavior-change migration of DebtorState

`features/conversation/debtor_state.py` becomes a **compat shim** for one slice
window, so the ~366 tests and all callers (`state.py`, `redis_store.py`,
`hooks`) keep working while we move call sites:

```python
# debtor_state.py (shim)
from features.cobranza.debtor import COBRANZA_SPEC
from features.conversation.record import Record

class DebtorState(Record):           # subclass ONLY to preserve the name/ctor
    def __init__(self, initial_data=None, on_transition=None):
        super().__init__(COBRANZA_SPEC, initial_data, on_transition)
```

Then call sites migrate `DebtorState()` → `Record(COBRANZA_SPEC)` (or the engine
resolves the spec from the registry — see C). The shim is deleted in the final
slice once no caller references it. The module-level field-set constants
(`CONTACT_FIELDS` etc.) that tests may import are re-exported from the shim until
deletion. **Characterization gate**: a test asserts `Record(COBRANZA_SPEC)` and the
old `DebtorState` produce identical `level`/`get_status` across a fixed input matrix
BEFORE the shim is removed.

> Dependency note: `features/conversation/` (neutral) must NOT import
> `features/cobranza/`. The shim above is the ONE temporary exception and lives in
> the conversation package only during migration; the clean end state is that the
> engine injects `COBRANZA_SPEC` from the registry, so conversation never names
> cobranza. See F.

---

## B. Persistence design

### B1 (B2 in summary). cobranza `debtors`: KEEP — evidence

`features/analytics/dashboard.py` reads the denormalized table in **6 places**:

| Line | Query | Needs projection table because |
|------|-------|-------------------------------|
| 132/141 | paginated `SELECT name,email,phone,project_interest,debtor_level` + COUNT | flat columns + WHERE filters on `debtor_level`; JSONB-extraction rewrite = behavior risk |
| 160 | `SELECT * WHERE conversation_id` (lead detail) | one row per debtor, not per conversation |
| 201/206/211 | `COUNT WHERE debtor_level IN (...)` / `= 'DEBTOR_VERIFIED'` / `created_at >=` | level-segmented funnel counts |
| 219 | `GROUP BY project_interest` top-10 | grouped aggregate |
| 277 | `LEFT JOIN debtors d ON lm.conversation_id` | join shape for conversation list |

`conversations` stores `record_data` as a JSONB blob keyed by `conversation_id`
(not by debtor, and not column-flattened). Serving these 6 queries from
`conversations.record_data` means rewriting every dashboard query to JSONB
operators + per-conversation→per-debtor reshaping. That is non-trivial new logic
under a "zero behavior change" constraint → **unjustified risk**.

**Recommendation: KEEP.** Rename `sorelia_debtors`→`debtors`. It is cobranza's
*declared projection table* in the registry (`projection_table="debtors"`). Ventas
later declares `leads`. Types with no dashboard declare `projection_table=None` and
`ensure_tables` skips it. This also realizes Ricky's "optional per agent_type"
intent cleanly — optionality lives in the registry, cobranza opts in.

> Note: `upsert_debtor` (persistence.py) + 2 call sites
> (`api/routers/webhooks.py:357`, `conversations.py:356`) write this table; they
> rename to the neutral table but keep the cobranza-named helper, or become a
> registry-driven `upsert_projection`. Minimal path: rename table string only.

### B2. Table + column changes (empty-DB drop+recreate, NO migration)

| Old | New | Columns changed |
|-----|-----|-----------------|
| `sorelia_conversations` | `conversations` | `debtor_data`→`record_data`, `debtor_level`→`record_level`. Drop the `lead_data` dual-read column + fallback. |
| `sorelia_debtors` | `debtors` | keep cols; `debtor_level` stays (cobranza domain term, dashboard reads it). |
| `sorelia_visitors` | `visitors` | `lead_data`→`record_data` (the leftover debt). |

`persistence.py`:
- `ensure_tables(pool, schema, *, projection_table: str | None)` — creates
  `conversations`; creates the projection table only when declared. The conversation
  table uses neutral `record_data`/`record_level`.
- `save_conversation(... record_data=, record_level=)`; `load_conversation` drops the
  `lead_data` key from the JSON-decode list. Dual-read deleted (DB is empty).
- `upsert_debtor` → target `debtors`.

`visitor_memory.py`: table `visitors`; `lead_data`→`record_data` in DDL, INSERT,
UPDATE-merge, and `_row_to_dict` decode list.

`state.py` / `redis_store.py`: drop the `debtor_data`-vs-`lead_data` dual-read
(empty DB). `ConversationState` ctor param `lead_data=` removed;
`save_conversation` called with `record_data=`. Redis suffix `lead_data`→`record_data`.

### B3. Empty-DB recreate plan (deploy)

Olimpo is empty (deploy `cobranza/olimpo-deploy-done`). No data migration:

1. On `bd-intranet` (as `cobranza_svc` admin): `DROP TABLE IF EXISTS
   project_<uid>.sorelia_conversations, sorelia_debtors, sorelia_visitors CASCADE;`
2. Code rsync to `automation` + `docker compose up -d --build prestamype-demo`.
3. Boot runs `ensure_tables` → creates `conversations`, `debtors`, `visitors` with
   neutral names. Verify log `PostgreSQL persistence active`.
4. Rollback: `git revert` + rsync prior code + rebuild; recreate `sorelia_*` (empty)
   or restore `automation:/home/onbot/olimpo_predeploy_backup_*.tgz`. No data loss.

### B4. Redis key renames

`_key()` prefix `sorelia:conv:`→`olimpo:conv:`; suffix `lead_data`→`record_data`.
Existing keys expire by 24h TTL — no migration. Drop the `lead_data` fallback `get`.

---

## C. Type registry (in-code now, DB-swappable later)

### C1. Port (interface) — `shared/ports/agent_type_registry.py`

```python
@dataclass(frozen=True)
class AgentTypeSpec:
    agent_type: str
    capture_spec: CaptureSpec          # neutral state spec (A)
    tools: tuple[str, ...]             # tool names this type composes
    gated_tools: frozenset[str]        # per-domain gate set (D)
    skills: tuple[str, ...]            # knowledge packs
    gate_model: str                    # e.g. "hard_dni" | "open" | "identity"
    projection_table: str | None       # "debtors" for cobranza; None = skip

@runtime_checkable
class AgentTypeRegistry(Protocol):
    def get(self, agent_type: str) -> AgentTypeSpec: ...
    def has(self, agent_type: str) -> bool: ...
```

`shared/ports/` is import-pure (imports nothing from `features`/`api`). `CaptureSpec`
is the one type the port references — to keep purity it is defined in
`shared/ports/capture_spec.py` (a pure dataclass) and `features/conversation/record.py`
imports it from there. This keeps `features→shared` direction intact.

### C2. In-code impl — `tenancy/agent_types/registry.py`

```python
class InCodeAgentTypeRegistry:
    def __init__(self, specs: dict[str, AgentTypeSpec]):
        self._specs = specs
    def get(self, t): return self._specs[t]
    def has(self, t): return t in self._specs

def default_registry() -> InCodeAgentTypeRegistry:
    return InCodeAgentTypeRegistry({"cobranza": COBRANZA_AGENT_TYPE})
```

`COBRANZA_AGENT_TYPE` is assembled in `tenancy/agent_types/cobranza_entry.py`,
which imports `COBRANZA_SPEC` from `features/cobranza/`. **Allowed**: `tenancy` may
import `features` (same direction as `api→features`)? — NO: today `features→tenancy`.
To avoid inverting it, the cobranza entry lives in `features/cobranza/agent_type.py`
(it is cobranza-specific config) and is *registered* into the registry at composition
time in `api/` (the composition root), where importing both `features` and `tenancy`
is allowed. The registry object itself holds only neutral `AgentTypeSpec` data.

> The single registry entry = cobranza. No empty `features/creditos` dir. YAGNI honored.

### C3. DB-swappable later

A future `DbAgentTypeRegistry(AgentTypeRegistry)` reads specs from an olimpo table.
Because consumers depend on the `AgentTypeRegistry` **port**, swapping the impl at the
composition root needs zero changes downstream. The in-code `default_registry()` is
the only call site to replace.

### C4. How the engine composes from it

`agent_type` is read from `tenant.config.json` (`"agent_type": "cobranza"`,
defaulting to `"cobranza"` when absent → zero change for prestamype/prestaunion).
`TenantConfig` gains an `agent_type: str = "cobranza"` field. At the composition root
(`api/main.py` wiring), the engine: resolves `spec = registry.get(cfg.agent_type)`,
builds `Record(spec.capture_spec)`, builds the ToolRegistry from `spec.tools` +
`spec.gated_tools` (D), loads `spec.skills`, and passes `spec.projection_table` to
`ensure_tables`.

---

## D. ToolRegistry per type + per-domain gate

Today `api/tool_registry.py` hardcodes the cobranza tool dict AND the module-level
`_GATED_TOOLS` set. We make the gate set + tool selection **registry-driven** without
rewriting the concrete registry.

- Keep the concrete `ToolRegistry` (it owns the tool implementations and the gate
  *mechanism* — the `execute()` short-circuit). It stays in `api/` (api→features OK).
- Replace the module-global `_GATED_TOOLS` with a constructor param
  `gated_tools: frozenset[str]`, defaulting to the current cobranza set for back-compat.
  The composition root passes `spec.gated_tools`.
- The *gate model* (`spec.gate_model == "hard_dni"`) selects which gate behavior the
  registry applies. Cobranza = hard DNI (unchanged): unverified identity →
  `{"blocked": "identity_required"}`. A future `"open"` type passes an empty gated set.
- Tool *availability* per type: the registry only registers tools named in
  `spec.tools` (plus always-on generic engine tools). Cobranza's `spec.tools` lists
  exactly today's set, so the live tool dict is identical → zero behavior change.
- `tenant.config.json` `excluded_tools` continues to subtract from the composed set
  (prestamype excludes 3 tools today) — applied AFTER registry composition.

Port (`shared/ports/tool_registry.py`) is unchanged (`has_tool`/`execute`); we only
extend the concrete constructor. The `NullToolRegistry` default stays.

---

## E. Per-slice execution plan (resolves proposal open-decision #3)

Each slice: TDD (`uv run pytest tests/ -v`), ends green, independently revertible.
Ordering minimizes the window where the compat shim exists.

| # | Slice | Scope | Green gate | Rollback |
|---|-------|-------|-----------|----------|
| 1 | **CaptureSpec + Record (neutral)** | Add `shared/ports/capture_spec.py`, `features/conversation/record.py`. New char-test: `Record(COBRANZA_SPEC_inline)` ≡ `DebtorState` over input matrix. No call-site change yet. | new tests + all 366 | git revert (additive) |
| 2 | **cobranza Debtor + spec** | `features/cobranza/debtor.py` (`COBRANZA_SPEC`, `Debtor` composition). `DebtorState`→shim subclass of `Record`. | 366 unchanged | revert; shim restores |
| 3 | **AgentTypeRegistry port + in-code impl** | `shared/ports/agent_type_registry.py`, `tenancy/agent_types/registry.py`, cobranza entry. `agent_type` field on `TenantConfig` (default cobranza). | new registry tests + 366 | revert (additive) |
| 4 | **Engine composes spec from registry** | Composition root resolves spec; conversation built with `Record(spec.capture_spec)`. Migrate call sites off `DebtorState`. | 366 | revert to shim path |
| 5 | **ToolRegistry registry-driven gate** | `gated_tools`/`tools` params; composition passes `spec.*`. Defaults = cobranza set. | tool/gate tests + 366 | revert; defaults restore |
| 6 | **Persistence neutral names (conversations/visitors)** | Rename tables + `record_data`/`record_level`; drop dual-read; Redis prefix/suffix. Update `test_storage_migration.py` to new names. | 366 (adjusted) | revert + DROP/recreate |
| 7 | **Projection table = `debtors` via registry** | `ensure_tables(projection_table=)`; `upsert_debtor`→`debtors`; dashboard SQL `sorelia_debtors`→`debtors`. | dashboard + 366 | revert |
| 8 | **Delete compat shim** | Remove `debtor_state.py` shim + re-exported constants once no caller references. | 366 | revert |

**Deploy coordination** (after slice 6/7 land): code rsync+rebuild on `automation`
AND `DROP` legacy `sorelia_*` in olimpo (empty) so `ensure_tables` recreates neutral
names. Slices 1-5 are code-only (no DB), deployable independently. Slices 6-8 form
the persistence/deploy unit.

> Review-load note: slices are small and mostly mechanical; 1-5 are pure code, 6-7
> touch persistence+dashboard+tests together (one work unit). No single slice should
> exceed the 400-line review budget; if 6 does, split table-rename from dual-read-removal.

---

## F. Dependency-rule / circular-dep check

Rule: `features → shared`/`tenancy` only; `api`/composition root → anything;
`shared/ports` imports nothing from `features`/`api`. **Verified today**: zero real
`shared→features` imports (the two grep hits in `ports/tool_registry.py` and
`templates.py` are docstring prose, confirmed by `^(from|import) features` = no match);
zero `shared→tenancy`.

Design preserves it:
- `CaptureSpec` placed in `shared/ports/` (pure) so `features/conversation/record.py`
  imports DOWNWARD (`features→shared`). ✅
- `AgentTypeRegistry` port in `shared/ports/` (pure data Protocol). ✅
- `Record` (neutral) must not import `features/cobranza`. The cobranza spec is injected
  by the composition root, never imported by conversation — except the temporary shim
  in slice 2-7, isolated and deleted in slice 8. ✅
- Registry concrete impl in `tenancy/`; cobranza entry data in `features/cobranza/`;
  **wiring** of the two happens in `api/` (composition root), the only place allowed to
  import both `features` and `tenancy`. This avoids a `tenancy→features` or
  `features→tenancy` inversion. ✅
- Concrete `ToolRegistry` stays in `api/` (api→features OK); port stays pure. ✅

No new cycles. The only transient violation (conversation shim referencing cobranza)
is bounded to the migration window and removed in the last slice.

---

## Open items flagged

- **Slice-6 test churn**: `tests/test_storage_migration.py` asserts the OLD names
  (`sorelia_debtors`, dual-read, migration-script existence). These are
  characterization tests for the *previous* migration; slice 6 must rewrite them to the
  neutral names (they are not behavioral contract for the bot — they assert storage
  identifiers). Confirm with Ricky that this test file is migration-scaffolding and may
  be retargeted, not a frozen contract.
- **`agent_type` absence default**: prestamype/prestaunion configs have no
  `agent_type` today → default `"cobranza"`. Verify no other tenant relies on a
  different implicit type (only cobranza exists, so safe).
