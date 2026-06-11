# Design: Bind Tracking Capability Catalog to Real Tenant Intents

## Technical Approach

DRY split, one source per concept: `gestion_catalog.py` holds **generic vocabulary** (enums), `responses.json` holds the **per-tenant binding** (each intent declares its own `capability` / `terminal_signal` / `escalation_reason`). A single accessor `intent_binding(intent, spec)` reads the binding from the resolved intent's `ResponsesSpec`; both `derive_outcome` and `_emit_gestion` consume it. Adding/renaming an intent carries its mapping with it — no hardcoded dicts to drift.

## Architecture Decisions

### Decision: Hook access path — resolve spec from tenant dir (Option a) [CRITICAL]

| Option | In-scope evidence | Tradeoff | Decision |
|---|---|---|---|
| (a) Hook loads `ResponsesSpec.from_dir(_tenant_dir(conv.tenant_id))` | `_emit_gestion` has `conv.tenant_id` (set at `conversations.py:337`); module already loads tenant dir via `_tenant_project_uid` and `_delivery_for` (`wiring.py:130-135`) | One file read per terminal turn (acceptable, fire-and-forget, infrequent) | **CHOSEN** |
| (b) Pass already-resolved binding from the turn | `resolved_intent` is in scope (`conversations.py:313`) but the `ResponsesSpec` is built **inside** `SoreliaAgent` and is NOT in scope at the spawn call-site (`conversations.py:339`) | Requires threading the spec out of the agent through `result` → invasive, touches agent contract | Rejected |

**Rationale**: `_emit_gestion(conv, result, tool_pairs)` already self-resolves tenant data from `conv.tenant_id` using the same `_tenant_dir` loaders. Option (a) keeps the call-site signature unchanged and is consistent with the existing pattern. Option (b) would leak the spec across the agent boundary for no gain. The spec is cheap to load and the hook is already off the request path.

### Decision: Validate bindings in the accessor (not at load time)

| Option | Tradeoff | Decision |
|---|---|---|
| Validate at `ResponsesSpec.from_dir` | Couples tenancy loader to tracking enums; raises on bad config → breaks chat path | Rejected |
| Validate (coerce) in `intent_binding` accessor | Unknown value → `None` (treated as unannotated); never raises; fail-safe matches "unannotated → unresolved" | **CHOSEN** |

**Rationale**: The hook is never-raise. The accessor coerces each field against its enum; any value not in `Capability`/`TerminalSignal`/`OutcomeReason` returns `None` for that field, degrading to current behavior. No config error can reach the conversation path.

### Decision: Accessor lives in `gestion_catalog.py`

It depends only on the enums it defines; `gestion_derivation.py` and `wiring.py` already import from the catalog. No new module needed.

## Data Flow

    conversations.py: resolved_intent (313) ──► _spawn_gestion(conv, result, tool_pairs) (339)
                                                      │
    wiring._emit_gestion:  conv.tenant_id ──► ResponsesSpec.from_dir(_tenant_dir(tenant_id))
                           result.metadata.intent ──┐
                                                     ▼
                  intent_binding(intent, spec) → (capability, terminal_signal, reason)
                                                     │
              ┌──────────────────────────────────────┼───────────────────────────┐
              ▼                                       ▼                           ▼
     capabilities_used = [capability]      derive_outcome(terminal_signal=…)   escalation reason
              └──────────────► upsert_gestion / append_gestion_event / Doris ◄──┘

`identity_failed` stays a session-state signal (gate flag), independent of intent binding.

## File Changes

| File | Action | Description |
|---|---|---|
| `apps/agent/features/analytics/gestion_catalog.py` | Modify | Remove `INTENT_TO_CAPABILITY`, `TERMINAL_SIGNALS`, `INTENT_TO_REASON`. Add `Capability`, `OutcomeReason`, `TerminalSignal` enums + `intent_binding()`. Keep `Outcome`, `EventType`, `SCHEMA_VERSION`. |
| `apps/agent/features/analytics/gestion_derivation.py` | Modify | `derive_outcome` consumes `terminal_signal` (one param) instead of 6 boolean flags. Keep pure. |
| `apps/agent/api/wiring.py` | Modify | `_emit_gestion` loads spec from tenant dir, calls `intent_binding`, maps signal → `derive_outcome`. |
| `tenants/prestamype/responses.json` | Modify | Annotate 16 intents with binding fields (block below). |
| `tenants/_template/responses.json` | New | Document the 3 binding fields as convention. |
| `tests/test_gestion_catalog.py`, `_derivation.py`, `_wiring.py` | Modify | Assert real intents + vocabulary via a fixture spec; drop invented-dict assertions. |

## Interfaces / Contracts

### Catalog enums (gestion_catalog.py, after)

```python
class Capability(str, Enum):
    identificacion = "identificacion"; consulta_deuda = "consulta_deuda"
    cuentas_bancarias = "cuentas_bancarias"; estado_cuenta = "estado_cuenta"
    constancia = "constancia"; politica_pago = "politica_pago"
    comprobante = "comprobante"; multicredito = "multicredito"
    # reserved for flujos change:
    cronograma = "cronograma"; cuotas = "cuotas"; fecha_vencimiento = "fecha_vencimiento"
    compromiso = "compromiso"; pago = "pago"; deuda_total = "deuda_total"
    horario_feriado = "horario_feriado"

class TerminalSignal(str, Enum):
    info_provided = "info_provided"; proof = "proof"; commitment = "commitment"
    escalation = "escalation"; fallback = "fallback"; identity_failed = "identity_failed"

class OutcomeReason(str, Enum):
    explicit_agent_request = "explicit_agent_request"; fallback_exhausted = "fallback_exhausted"
    cannot_pay = "cannot_pay"; requested_alternatives = "requested_alternatives"
    commitment_beyond_window = "commitment_beyond_window"; wants_full_payment = "wants_full_payment"
    pay_installment = "pay_installment"; proof_other_installment = "proof_other_installment"
    out_of_hours = "out_of_hours"; max_identification_retries = "max_identification_retries"
```

### Single accessor (gestion_catalog.py)

```python
def intent_binding(
    intent_name: str | None, responses_cfg: "ResponsesSpec | None"
) -> tuple[str | None, str | None, str | None]:
    """Return (capability, terminal_signal, escalation_reason) for a resolved
    intent, each coerced against the catalog enums. Unknown/absent → None.
    Never raises (hook is never-raise)."""
    if not intent_name or responses_cfg is None:
        return (None, None, None)
    cfg = (responses_cfg.intents.get(intent_name) or {})
    def _ok(val, enum):  # coerce or None
        try: return enum(val).value if val is not None else None
        except ValueError: return None
    return (
        _ok(cfg.get("capability"), Capability),
        _ok(cfg.get("terminal_signal"), TerminalSignal),
        _ok(cfg.get("escalation_reason"), OutcomeReason),
    )
```

`responses_cfg` is a `ResponsesSpec` (`tenancy/responses_spec.py`); accessor only touches `.intents`, so it imports the type lazily / under `TYPE_CHECKING` to avoid a tenancy→analytics cycle.

### responses.json binding schema (per intent, all optional)

```jsonc
"consulta_deuda": { /* …existing… */, "capability": "consulta_deuda", "terminal_signal": "info_provided" }
"derivar_asesor": { /* … */, "terminal_signal": "escalation", "escalation_reason": "explicit_agent_request" }
```

### derive_outcome — before / after

```python
# BEFORE
def derive_outcome(*, session_state, resolved_intent, was_escalated,
    identity_failed=False, commitment_registered=False, proof_submitted=False,
    fallback_exhausted=False, identified=False, info_provided=False) -> tuple[str, str|None]

# AFTER (pure; signal-driven)
def derive_outcome(*, session_state: dict, resolved_intent: str | None,
    terminal_signal: str | None, was_escalated: bool, identity_failed: bool = False,
    escalation_reason: str | None = None) -> tuple[str, str | None]
```

Mapping (priority order preserved): `identity_failed` → `identification_failed`/`max_identification_retries`; `terminal_signal==commitment` → `payment_commitment_registered`; `==proof` → `payment_proof_submitted`; `was_escalated or ==escalation` → `escalated_to_agent`/`escalation_reason`; `==fallback` → `not_understood`/`fallback_exhausted`; `==info_provided` → `info_provided`; identity capability seen → `identified`; else `unresolved`. `_emit_gestion` derives `is_terminal = identity_failed or escalated or terminal_signal is not None`.

## Testing Strategy

| Layer | What to Test | Approach |
|---|---|---|
| Unit | `intent_binding` coercion: valid → enum value; bad value → None; missing intent → `(None,None,None)` | Fixture `ResponsesSpec(intents={...})` with real keys |
| Unit | `derive_outcome` per `terminal_signal` + identity_failed precedence | Parametrize over `TerminalSignal` values |
| Unit | Catalog has zero tenant intent names; enums contain plan values | Introspect enum members |
| Integration | `_emit_gestion` end-to-end for an annotated intent (`consulta_deuda` → `info_provided`) | Build `ResponsesSpec` from a fixture tenant dir; assert gestion row outcome with `GESTION_TEST_PG_DSN` |

Tests use a **fixture `responses.json`** keyed by REAL intents (not invented names). The catalog tests that asserted `INTENT_TO_CAPABILITY`/`TERMINAL_SIGNALS`/`INTENT_TO_REASON` are rewritten to assert vocabulary + binding-via-spec — they validated a false self-referential mapping.

## Migration / Rollout

Additive, non-breaking. New `responses.json` fields are optional; unannotated intent → `(None,None,None)` → `unresolved` (identical to today). Hook stays fire-and-forget / never-raise. Rollback = `git revert`; older code ignores the new fields. No data migration.

## Prestamype 16-intent annotation block

Drop these fields into each existing intent object in `tenants/prestamype/responses.json`:

```jsonc
"saludo":               { /* no binding */ },
"despedida":            { /* no binding */ },
"identidad_requerida":  { "capability": "identificacion" },
"identificar":          { "capability": "identificacion" },
"consulta_deuda":       { "capability": "consulta_deuda",    "terminal_signal": "info_provided" },
"elegir_credito":       { "capability": "multicredito" },
"politica_pago":        { "capability": "politica_pago",     "terminal_signal": "info_provided" },
"donde_pagar":          { "capability": "cuentas_bancarias", "terminal_signal": "info_provided" },
"enviar_estado":        { "capability": "estado_cuenta",     "terminal_signal": "info_provided" },
"enviar_datos_pago":    { "capability": "cuentas_bancarias", "terminal_signal": "info_provided" },
"enviar_constancia":    { "capability": "constancia",        "terminal_signal": "info_provided" },
"comprobante_reportar": { "capability": "comprobante" },
"comprobante_resultado":{ "capability": "comprobante",       "terminal_signal": "proof" },
"derivar_asesor":       { "terminal_signal": "escalation",   "escalation_reason": "explicit_agent_request" },
"no_entendido":         { "terminal_signal": "fallback",     "escalation_reason": "fallback_exhausted" },
"elegir_canal":         { /* no binding */ }
```

## Open Questions

- [ ] `no_entendido` carries `escalation_reason: fallback_exhausted` per the plan; confirm the reason belongs on the `not_understood` outcome (vs only on escalation). Non-blocking — accessor passes it through; `derive_outcome` already maps `fallback` → `fallback_exhausted` regardless.
