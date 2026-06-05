# Proposal: PrestamYpe — Correct Balance, Enrich "Tu Crédito", Lighter Comprobante

## Intent
Three fronts, led by a CRITICAL live data bug:
1. Balance wrong (CRITICAL, live): MAX(saldo_por_cancelar) returns first installment = ORIGINAL loan amount. Bot quotes full original loan as "Saldo pendiente". Ships now.
2. "Tu crédito" incomplete: missing cuenta_bancaria + inversionista; balance is wrong figure.
3. Comprobante asks technical friction fields (CCI, nro_operacion) though identity+credit known server-side (DNI->account->credit->CCI+inversionista in Doris).

## Scope
IN: fix balance to first-unpaid-installment saldo_por_cancelar (Bug 2, ORDER BY fecha_de_pago_esperada_original ASC WHERE fecha_de_pago_del_cliente IS NULL LIMIT 1); filter assignment to latest batch per credit (Bug 1, MAX(creado_el)/latest archivo); resolve correct "próxima cuota" when cuota_esperada_actualizada NULL (Bug 3, column TBD in design); show corrected saldo + add cuenta_bancaria + inversionista to tu crédito (capital omitted); Comprobante Liviano (foto+monto+inversionista+id_crédito OPTIONAL, flexible; validate server-side vs known credit CCI+inversionista; classify pago/abono/cancelación; anti-duplicate; stays EN REVISIÓN; inversionista in audit).
OUT: OCR; changing data_source=doris or identity gate; prestaunion; multi-batch payments (future, pagos has 1 batch).

## Capabilities
New: None.
Modified: cobranza-identity (comprobante capture requirements lighter+server-validated; credit-status response gains inversionista+cuenta bancaria + corrected balance semantics).

## Approach
Doris SQL redesign (doris_debt_source.py::_build_sql): flat agg:MAX cannot express first-unpaid or latest-batch. Design picks new `strategy` concept in doris_schema vs prestamype-specific SQL path. Config: add cuenta_bancaria->numero_de_cuenta to column_map. Tools: consultar_deuda returns inversionista+cuenta_bancaria; validar_comprobante validates vs Doris CCI+inversionista. Frontend/templates: debt card + responses.json show new fields; guardrails.md, faq.json, prompt ask lighter set.

## Affected Areas
- apps/agent/features/cobranza/doris_debt_source.py (_build_sql first-unpaid + latest-batch)
- tenants/prestamype/tenant.config.json (column_map + doris_schema strategy)
- apps/agent/features/cobranza/tools.py (consultar_deuda fields, validar_comprobante server-side)
- apps/agent/features/comprobantes/validator.py (audit + inversionista)
- frontend/widget.js (debt card rows)
- tenants/prestamype/responses.json (templates)
- tenants/prestamype/guardrails.md, faq.json, prompt (lighter ask set)

## Risks
- Doris SQL redesign blast radius (Med) -> isolate via strategy/prestamype path; full suite + new tests.
- Bug 3 cuota column undetermined (Med) -> resolve in design vs live Doris before apply.
- Multicrédito edge cases (Med) -> test multi-credit DNIs; first-unpaid per credit.
- Live wrong balance keeps misinforming debtors (High) -> ship promptly, prioritize Bug 2.

## Rollback
git revert; config/SQL/template scoped; restores agg:MAX (known-wrong current prod). No data migration.

## Dependencies
Live Doris access (10.110.0.15:9030) to confirm Bug 3 cuota column during design.

## Success Criteria
- Balance = first-unpaid-installment saldo, NOT original loan amount.
- Only latest assignment batch contributes (dias_mora deterministic).
- "Próxima cuota" populated even when cuota_esperada_actualizada NULL.
- inversionista + cuenta bancaria shown in tu crédito.
- Comprobante asks lighter flexible set; validates server-side vs Doris CCI+inversionista; classifies pago/abono/cancelación; stays EN REVISIÓN.
- Full test suite green + new tests for balance, batch filter, comprobante validation.
