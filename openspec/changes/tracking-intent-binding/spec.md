# Delta Spec: tracking-intent-binding
# Capability: conversation-result-tracking (Modified)

## Purpose

Reconcile the Layer-3 tracking capability catalog to real tenant intents via a DRY,
`responses.json`-driven binding. Catalog defines vocabulary only; tenant config supplies
the intent-to-capability/signal mapping.

---

## Requirement Coverage Map

| Req | Work Items | Description |
|-----|-----------|-------------|
| B1  | WI-1      | Catalog = vocabulary only, no tenant intent names |
| B2  | WI-2      | `responses.json` carries binding fields per intent |
| B3  | WI-3      | Single `intent_binding` accessor (DRY) |
| B4  | WI-3, WI-4 | Unannotated intent → `unresolved`, no crash |
| B5  | WI-4      | Derivation driven by `terminal_signal`, not flags |
| B6  | WI-5      | `capabilities_used` accumulates from accessor |
| B7  | WI-2      | 16 prestamype intents annotated per mapping table |
| B8  | WI-7      | Template tenant documents binding convention |
| B9  | WI-6      | Tests use real intents; no regressions |

---

## MODIFIED Requirements

### Requirement: Catalog Vocabulary Scope

The catalog (`gestion_catalog.py`) MUST contain ONLY tenant-agnostic vocabulary:
`Outcome` enum, `EventType` enum, `Capability` enum, `OutcomeReason` enum,
`SCHEMA_VERSION` constant, and the `intent_binding` accessor function.

The catalog MUST NOT contain any dict mapping intent names, tool names, or
tenant-specific strings. `INTENT_TO_CAPABILITY`, `TERMINAL_SIGNALS`, and
`INTENT_TO_REASON` dicts MUST be removed.

(Previously: catalog contained `INTENT_TO_CAPABILITY`, `TERMINAL_SIGNALS`,
`INTENT_TO_REASON` dicts keyed on invented, non-real intent/tool names.)

#### Scenario: B1-1 — Catalog contains zero tenant intent names

- GIVEN the file `apps/agent/features/analytics/gestion_catalog.py`
- WHEN its source is searched for any of the 16 prestamype intent names
  (`identificar`, `comprobante_reportar`, `derivar_asesor`, `elegir_credito`,
  `donde_pagar`, `politica_pago`, `no_entendido`, `consulta_deuda`,
  `enviar_estado`, `enviar_datos_pago`, `enviar_constancia`,
  `comprobante_resultado`, `elegir_canal`, `saludo`, `despedida`,
  `identidad_requerida`)
- THEN zero matches are found outside of comments

#### Scenario: B1-2 — Catalog contains zero invented non-real names

- GIVEN the file `gestion_catalog.py`
- WHEN its source is searched for `INTENT_TO_CAPABILITY`, `TERMINAL_SIGNALS`, `INTENT_TO_REASON`,
  `upload_comprobante`, `register_payment_commitment`, `multicredito` (as a string literal)
- THEN zero matches are found

#### Scenario: B1-3 — Enums present with correct members

- GIVEN the catalog module is imported
- WHEN `Capability` and `OutcomeReason` are enumerated
- THEN `Capability` contains at minimum: `identificacion`, `consulta_deuda`, `cuentas_bancarias`,
  `estado_cuenta`, `constancia`, `politica_pago`, `comprobante`, `multicredito`
- AND `OutcomeReason` contains at minimum: `explicit_agent_request`, `fallback_exhausted`

---

### Requirement: Per-Intent Binding in responses.json

Each intent entry in `tenants/{tenant}/responses.json` MAY declare three optional binding fields:

- `capability` — string; MUST be a member of `Capability` enum value set
- `terminal_signal` — string; MUST be one of: `info_provided`, `proof`, `commitment`,
  `escalation`, `fallback`, `identity_failed`
- `escalation_reason` — string; MUST be a member of `OutcomeReason` enum value set;
  only meaningful when `terminal_signal = escalation`

Intents without these fields MUST be treated as unannotated (no binding).

#### Scenario: B2-1 — Annotated intent resolves binding

- GIVEN `tenants/prestamype/responses.json` has `consulta_deuda` with
  `"capability": "consulta_deuda"` and `"terminal_signal": "info_provided"`
- WHEN `intent_binding("consulta_deuda", responses_cfg)` is called
- THEN it returns `(Capability.consulta_deuda, "info_provided", None)`

#### Scenario: B2-2 — Invalid capability value is flagged

- GIVEN a `responses.json` entry with `"capability": "invented_name_not_in_enum"`
- WHEN the binding is validated (at accessor call or startup)
- THEN a `ValueError` (or equivalent) is raised indicating the invalid value

#### Scenario: B2-3 — Escalation intent resolves with reason

- GIVEN `derivar_asesor` is annotated with `terminal_signal: escalation`,
  `escalation_reason: explicit_agent_request`
- WHEN `intent_binding("derivar_asesor", responses_cfg)` is called
- THEN it returns `(None, "escalation", OutcomeReason.explicit_agent_request)`

---

### Requirement: Single intent_binding Accessor (DRY)

The system MUST expose exactly one function `intent_binding(intent_name, responses_cfg)`
that reads `capability`, `terminal_signal`, and `escalation_reason` from the resolved
intent's config entry in `responses_cfg`.

Both `derive_outcome` and `_emit_gestion` MUST call this accessor and MUST NOT
duplicate binding-resolution logic.

#### Scenario: B3-1 — Accessor returns binding for real annotated intent

- GIVEN `responses_cfg` contains `comprobante_resultado` with
  `capability=comprobante`, `terminal_signal=proof`
- WHEN `intent_binding("comprobante_resultado", responses_cfg)` is called
- THEN it returns `(Capability.comprobante, "proof", None)`

#### Scenario: B3-2 — Accessor returns (None, None, None) for unannotated intent

- GIVEN `responses_cfg` contains `saludo` with no binding fields
- WHEN `intent_binding("saludo", responses_cfg)` is called
- THEN it returns `(None, None, None)`

#### Scenario: B3-3 — Accessor returns (None, None, None) for unknown intent

- GIVEN `responses_cfg` does not contain `"phantom_intent"`
- WHEN `intent_binding("phantom_intent", responses_cfg)` is called
- THEN it returns `(None, None, None)` without raising

---

### Requirement: Non-Breaking Default for Unannotated Intents

An unannotated intent (no `capability`, no `terminal_signal`) MUST produce
`capability=None`, `terminal_signal=None`, and outcome `Outcome.unresolved`.
This MUST be identical to pre-change behavior.

#### Scenario: B4-1 — Bare intent yields unresolved, no crash

- GIVEN a conversation turn whose resolved intent has no binding fields
- WHEN `derive_outcome` processes that turn
- THEN outcome is `Outcome.unresolved`
- AND no exception is raised

#### Scenario: B4-2 — saludo/despedida do not corrupt outcome

- GIVEN `saludo` and `despedida` are unannotated
- WHEN a conversation consists of only those intents
- THEN the tracked outcome is `Outcome.unresolved`

---

### Requirement: Signal-Driven Outcome Derivation

`derive_outcome` MUST derive the final outcome from the resolved intent's
`terminal_signal` according to the following mapping (in priority order):

| terminal_signal   | Outcome                           | Notes                        |
|-------------------|-----------------------------------|------------------------------|
| `identity_failed` | `identification_failed`           | session gate, checked first  |
| `proof`           | `payment_proof_submitted`         |                              |
| `commitment`      | `payment_commitment_registered`   |                              |
| `escalation`      | `escalated_to_agent` + reason     |                              |
| `fallback`        | `not_understood`                  |                              |
| `info_provided`   | `info_provided`                   |                              |
| (none)            | `unresolved`                      |                              |

`derive_outcome` MUST NOT read `TERMINAL_SIGNALS`, `INTENT_TO_CAPABILITY`,
or `INTENT_TO_REASON` dicts.

#### Scenario: B5-1 — info_provided signal → info_provided outcome

- GIVEN `consulta_deuda` annotated with `terminal_signal: info_provided`
- WHEN `derive_outcome` runs for that turn
- THEN outcome is `Outcome.info_provided`

#### Scenario: B5-2 — proof signal → payment_proof_submitted

- GIVEN `comprobante_resultado` annotated with `terminal_signal: proof`
- WHEN `derive_outcome` runs for that turn
- THEN outcome is `Outcome.payment_proof_submitted`

#### Scenario: B5-3 — escalation signal → escalated_to_agent with reason

- GIVEN `derivar_asesor` annotated with `terminal_signal: escalation`,
  `escalation_reason: explicit_agent_request`
- WHEN `derive_outcome` runs for that turn
- THEN outcome is `Outcome.escalated_to_agent`
- AND `outcome_reason` is `OutcomeReason.explicit_agent_request`

#### Scenario: B5-4 — fallback signal → not_understood

- GIVEN `no_entendido` annotated with `terminal_signal: fallback`
- WHEN `derive_outcome` runs for that turn
- THEN outcome is `Outcome.not_understood`

#### Scenario: B5-5 — identity_failed takes priority over other signals

- GIVEN a turn where the identity gate is exhausted (session flag set)
  AND the intent also has `terminal_signal: info_provided`
- WHEN `derive_outcome` runs
- THEN outcome is `Outcome.identification_failed`

---

### Requirement: capabilities_used Accumulation via Accessor

`_emit_gestion` MUST accumulate `capabilities_used` by calling `intent_binding`
on each turn's resolved intent and appending the returned `capability` value
(when not None) to the list. The final list MUST be deduplicated.

#### Scenario: B6-1 — Multi-turn conversation accumulates capabilities

- GIVEN a conversation with turns: `consulta_deuda` (capability=`consulta_deuda`),
  `donde_pagar` (capability=`cuentas_bancarias`), `consulta_deuda` again
- WHEN `_emit_gestion` processes the full conversation
- THEN `capabilities_used` contains `["consulta_deuda", "cuentas_bancarias"]` (deduped, ordered by first appearance)

#### Scenario: B6-2 — Unannotated turns do not corrupt capabilities_used

- GIVEN a conversation with `saludo` (unannotated) followed by `donde_pagar`
  (capability=`cuentas_bancarias`)
- WHEN `_emit_gestion` runs
- THEN `capabilities_used` is `["cuentas_bancarias"]` (saludo produces no entry)

---

### Requirement: Prestamype 16-Intent Annotation

All 16 intents in `tenants/prestamype/responses.json` MUST be annotated according
to the canonical mapping. Intents without a meaningful signal MAY omit `terminal_signal`.

| intent                | capability         | terminal_signal | escalation_reason       |
|-----------------------|--------------------|-----------------|-------------------------|
| `saludo`              | —                  | —               | —                       |
| `despedida`           | —                  | —               | —                       |
| `identidad_requerida` | `identificacion`   | —               | —                       |
| `identificar`         | `identificacion`   | —               | —                       |
| `consulta_deuda`      | `consulta_deuda`   | `info_provided` | —                       |
| `elegir_credito`      | `multicredito`     | —               | —                       |
| `politica_pago`       | `politica_pago`    | `info_provided` | —                       |
| `donde_pagar`         | `cuentas_bancarias`| `info_provided` | —                       |
| `enviar_estado`       | `estado_cuenta`    | `info_provided` | —                       |
| `enviar_datos_pago`   | `cuentas_bancarias`| `info_provided` | —                       |
| `enviar_constancia`   | `constancia`       | `info_provided` | —                       |
| `comprobante_reportar`| `comprobante`      | —               | —                       |
| `comprobante_resultado`| `comprobante`     | `proof`         | —                       |
| `derivar_asesor`      | —                  | `escalation`    | `explicit_agent_request`|
| `no_entendido`        | —                  | `fallback`      | `fallback_exhausted`    |
| `elegir_canal`        | —                  | —               | —                       |

#### Scenario: B7-1 — consulta_deuda end-to-end produces info_provided

- GIVEN a complete conversation where the terminal resolved intent is `consulta_deuda`
- WHEN the gestion hook fires
- THEN the persisted gestiones record has `outcome = "info_provided"`
- AND `capabilities_used` contains `"consulta_deuda"`

#### Scenario: B7-2 — comprobante_resultado end-to-end produces payment_proof_submitted

- GIVEN the terminal resolved intent is `comprobante_resultado`
- WHEN the gestion hook fires
- THEN outcome is `"payment_proof_submitted"` and capability `"comprobante"` is in `capabilities_used`

#### Scenario: B7-3 — derivar_asesor end-to-end produces escalated_to_agent

- GIVEN the terminal resolved intent is `derivar_asesor`
- WHEN the gestion hook fires
- THEN outcome is `"escalated_to_agent"` and `outcome_reason` is `"explicit_agent_request"`

#### Scenario: B7-4 — no_entendido end-to-end produces not_understood

- GIVEN the terminal resolved intent is `no_entendido`
- WHEN the gestion hook fires
- THEN outcome is `"not_understood"`

---

### Requirement: Tenant Template Binding Convention

`tenants/_template/responses.json` MUST exist and MUST document the three
binding fields (`capability`, `terminal_signal`, `escalation_reason`) as
optional intent-level keys with inline comments or example values showing
valid catalog vocabulary usage.

#### Scenario: B8-1 — Template file present with annotated example

- GIVEN the file `tenants/_template/responses.json`
- WHEN it is read
- THEN it contains at least one intent entry that declares `capability` and
  `terminal_signal` with valid catalog-vocabulary values as documentation

---

### Requirement: Tenant-Agnostic, No Regression

The change MUST NOT write to `mibotair_results`, reference `n1`/`n2`/`n3` columns,
or alter any non-gestion test. All 648 existing test assertions MUST remain green.
Tests for catalog/derivation/wiring MUST be rewritten to assert real intents and
catalog vocabulary (not the invented mapping).

#### Scenario: B9-1 — No writes outside gestion scope

- GIVEN the full change is applied
- WHEN tests run with `GESTION_TEST_PG_DSN` set
- THEN no INSERT or UPDATE touches `mibotair_results`
- AND no column named `n1`, `n2`, or `n3` is referenced in any executed SQL

#### Scenario: B9-2 — Full test suite green

- GIVEN the change is applied
- WHEN `pytest tests/ -v` runs with `GESTION_TEST_PG_DSN`
- THEN all tests pass (zero failures, zero errors)

#### Scenario: B9-3 — Non-gestion tests unaffected

- GIVEN a test file not in `tests/test_gestion_*.py`
- WHEN the change is applied and tests run
- THEN that file's test count and pass rate are identical to pre-change baseline
