# Proposal: Bind Tracking Capability Catalog to Real Tenant Intents

## Intent

The Layer-3 tracking catalog (`gestion_catalog.py`, shipped by `conversation-result-tracking`) keys on **invented** intent/tool names (`identificacion`, `upload_comprobante`, `register_payment_commitment`, `multicredito`, `cronograma`…). These match **zero** real tenant intents except `consulta_deuda`. Real intents are the 16 keys of `tenants/prestamype/responses.json` (`identificar`, `comprobante_reportar`, `derivar_asesor`, `elegir_credito`, `donde_pagar`, `politica_pago`, `no_entendido`, `enviar_*`…).

The 648 tests pass only because they validate the catalog against itself, not against real bot output — masking the disconnect. In production today, the tracking layer would record almost every conversation as `unresolved`. This change reconciles the catalog to real intents via a DRY, `responses.json`-driven binding.

## Scope

### In Scope
- Strip `gestion_catalog.py` to generic tenant-agnostic vocabulary only (`Outcome`, `EventType`, `Capability` enum, `OutcomeReason` enum, `SCHEMA_VERSION`).
- Move intent→capability/terminal binding into `responses.json` per intent (optional fields: `capability`, `terminal_signal`, `escalation_reason`); annotate prestamype's 16 intents per the plan mapping.
- Single accessor reads the binding from the resolved intent's config; consumed by derivation + `_emit_gestion`.
- Rewrite the catalog/derivation/wiring tests that asserted the invented mapping.
- Add `tenants/_template/responses.json` documenting the binding convention.

### Out of Scope
- Implementing the `prestamype-cobranza-flujos` intents (separate change).
- Renaming flujos-spec intents to match real ones (flagged as a future decision, not resolved here).
- Per-tenant config indirection beyond `responses.json`.

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- `conversation-result-tracking`: the capability/terminal binding requirement changes from hardcoded catalog dicts to a `responses.json`-driven, tenant-agnostic accessor. Catalog defines vocabulary; tenant config supplies the binding.

## Approach

Two responsibilities, one source each (DRY):
- **Catalog = vocabulary.** Generic enums (`Capability`, `OutcomeReason`) + `Outcome`/`EventType`/`SCHEMA_VERSION`. No tenant intent names.
- **`responses.json` = binding.** Each intent declares its own `capability`/`terminal_signal`/`escalation_reason` using catalog vocabulary. Per-tenant mapping solved by construction.
- **Single accessor** `intent_binding(intent_name, responses_cfg) -> (capability, terminal_signal, reason)` read by both `derive_outcome` and `_emit_gestion`. Adding/renaming an intent carries its mapping with it.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `apps/agent/features/analytics/gestion_catalog.py` | Modified | Remove invented dicts; add `Capability`/`OutcomeReason` enums |
| `apps/agent/features/analytics/gestion_derivation.py` | Modified | `derive_outcome` consumes intent's `terminal_signal`; add accessor |
| `apps/agent/features/.../wiring.py` | Modified | `_emit_gestion` resolves binding from resolved intent config |
| `tenants/prestamype/responses.json` | Modified | Annotate 16 intents with binding fields |
| `tenants/_template/responses.json` | New | Document binding convention |
| `tests/test_gestion_*.py` | Modified | Assert real intents + vocabulary, not invented mapping |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Hook can't reach resolved intent's config | Med | Pass already-resolved binding from the turn (engine resolved the intent); verify in wiring WI |
| `outcome_reason` sub-motives (`cannot_pay`, etc.) not yet modeled | Low | Single default reason now; sub-motives arrive with flujos change |
| Unannotated intent silently `unresolved` | Low | Intentional, non-breaking — matches current behavior |

## Rollback Plan

`git revert` the change. New `responses.json` fields are optional and ignored by older code; removing them restores prior config. Hook stays fire-and-forget / never-raise throughout, so revert cannot affect the conversation path.

## Dependencies

- Requires `conversation-result-tracking` to be in place (this is a delta to it; it touches its files).

## Success Criteria

- [ ] `gestion_catalog.py` contains zero tenant intent names (vocabulary only).
- [ ] All 16 prestamype intents carry their binding in `responses.json`; an annotated intent produces its expected capability/outcome end-to-end.
- [ ] Tests assert against real intents; full suite green with `GESTION_TEST_PG_DSN`.
- [ ] `tenants/_template/responses.json` documents the binding convention.
