# Proposal: PrestamYpe — Correct Balance, Enrich "Tu Crédito", Lighter Comprobante

## Intent

Three fronts, led by a **CRITICAL production data bug**:

1. **Balance is wrong (CRITICAL, live)**: `MAX(saldo_por_cancelar)` returns the first installment's value = the ORIGINAL loan amount. The bot quotes the full original loan as "Saldo pendiente" to every debtor. Ships now.
2. **"Tu crédito" is incomplete**: missing `cuenta_bancaria` and `inversionista`; balance shown is the wrong (original) figure.
3. **Comprobante asks technical friction fields**: identity + credit are known server-side (DNI → account → credit → CCI + inversionista in Doris), so requiring user-typed CCI / nro_operacion is needless friction.

## Scope

### In Scope
- Fix balance to the **first unpaid installment** `saldo_por_cancelar` (Bug 2).
- Filter assignment table to the **latest batch** per credit (Bug 1).
- Resolve the correct "próxima cuota" column when `cuota_esperada_actualizada` is NULL (Bug 3 — column TBD in design).
- Show corrected saldo, add `cuenta_bancaria` + `inversionista` to "tu crédito" (capital stays omitted).
- "Comprobante Liviano": ask foto + monto + inversionista + id_crédito (OPTIONAL), flexible; validate server-side vs known credit's CCI + inversionista; classify pago/abono/cancelación; keep anti-duplicate; stays "EN REVISIÓN"; attach inversionista to audit.

### Out of Scope
- OCR / field extraction from the image.
- Changing `data_source=doris` or the identity gate.
- prestaunion tenant (untouched).
- Multi-batch payments logic ("payments >= file load date" — future, pagos has 1 batch today).

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- `cobranza-identity`: comprobante capture requirements change (lighter, server-side validated set); credit-status response gains inversionista + cuenta bancaria and corrected balance semantics.

## Approach

- **Doris SQL redesign** (`doris_debt_source.py::_build_sql`): the flat `agg: MAX` config concept cannot express "first unpaid installment" or "latest batch". Design picks: new `strategy` concept in `doris_schema` vs a prestamype-specific SQL path.
- **Config**: add `cuenta_bancaria` → `numero_de_cuenta` to `column_map`.
- **Tools**: `consultar_deuda` returns inversionista + cuenta_bancaria; `validar_comprobante` validates against Doris-known CCI + inversionista.
- **Frontend/templates**: debt card + responses.json show new fields; guardrails.md, faq.json, prompt ask the lighter set.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `apps/agent/features/cobranza/doris_debt_source.py` | Modified | `_build_sql` first-unpaid + latest-batch |
| `tenants/prestamype/tenant.config.json` | Modified | column_map + doris_schema strategy |
| `apps/agent/features/cobranza/tools.py` | Modified | consultar_deuda fields, validar_comprobante server-side |
| `apps/agent/features/comprobantes/validator.py` | Modified | audit + inversionista |
| `frontend/widget.js` | Modified | debt card rows |
| `tenants/prestamype/responses.json` | Modified | templates |
| `tenants/prestamype/guardrails.md`, `faq.json`, prompt | Modified | lighter ask set |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Doris SQL redesign blast radius | Med | Isolate via strategy/prestamype path; full suite + new tests |
| Bug 3 cuota column undetermined | Med | Resolve in design against live Doris before apply |
| Multicrédito edge cases | Med | Test multi-credit DNIs; first-unpaid per credit |
| Live wrong balance keeps misinforming debtors | High | Ship promptly; prioritize Bug 2 |

## Rollback Plan

`git revert` the change. Each front is config/SQL/template scoped — revert restores `agg: MAX` behavior (known-wrong but current prod state). No data migration.

## Dependencies

- Live Doris access (`10.110.0.15:9030`) to confirm Bug 3 cuota column during design.

## Success Criteria

- [ ] Balance shows first-unpaid-installment saldo, NOT the original loan amount.
- [ ] Only the latest assignment batch contributes (dias_mora deterministic).
- [ ] "Próxima cuota" populated even when `cuota_esperada_actualizada` is NULL.
- [ ] inversionista + cuenta bancaria shown in "tu crédito".
- [ ] Comprobante asks the lighter flexible set; validates server-side vs Doris CCI + inversionista; classifies pago/abono/cancelación; stays EN REVISIÓN.
- [ ] Full test suite green + new tests for balance, batch filter, comprobante validation.
