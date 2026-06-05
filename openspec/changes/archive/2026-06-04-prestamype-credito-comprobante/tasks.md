# Tasks: prestamype-credito-comprobante

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | 550–700 (10 files, window SQL + schema + frontend + prompt copy) |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | Slice A (SQL+config) → Slice B+C (display+comprobante) → Slice D (verify) → Slice E (monto_vencido) |
| Delivery strategy | ask-on-risk |
| Chain strategy | pending (user ships as one deploy; internal review slices recommended) |

Decision needed before apply: Yes
Chained PRs recommended: Yes
Chain strategy: pending
400-line budget risk: High

## Phase 1: Foundation — Config + SQL Skeleton (Slice A, RED→GREEN)

- [x] 1.1 [TEST-RED] `tests/test_doris_sql_firstunpaid.py` — assert `_build_sql` output contains `ROW_NUMBER() OVER (PARTITION BY codigo_contrato` and `PARTITION BY id_credito`; assert it does NOT contain `MAX(saldo_por_cancelar)`.
- [x] 1.2 [TEST-RED] Same file — mock cursor returning P04197 fixture rows (3 batches, first-unpaid saldo=81510.15); assert `consultar_deuda` returns `balance=81510.15`, `next_installment_amount=7031.91`, `next_due_date="2026-03-02"`.
- [x] 1.3 [TEST-RED] Assert cuota COALESCE: when `cuota_esperada_actualizada=NULL` fixture row, result equals `cuota_esperada_mensual`; when both present, uses `cuota_esperada_actualizada`.
- [x] 1.4 [TEST-RED] Assert `dias_mora` comes from DATEDIFF(CURDATE(), fecha_de_pago_esperada_original) — deterministic, not stale batch column.
- [x] 1.5 Add `pagos_selection` + `batch_selection` blocks to `tenants/prestamype/tenant.config.json`; add `cuenta_bancaria→numero_de_cuenta` to `column_map`; mark `saldo_por_cancelar`, `cuota_esperada_mensual`, `fecha_de_pago_esperada_original` as `from_selected_row: true`; remove `agg: MAX`.
- [x] 1.6 [GREEN] Redesign `_build_sql` in `apps/agent/features/cobranza/doris_debt_source.py` — emit `pagos_sel` CTE + `asig_sel` CTE with ROW_NUMBER windows; JOIN on `p.rn=1 AND a.rn=1`; embed `COALESCE(cuota_esperada_actualizada, cuota_esperada_mensual)`.
- [x] 1.7 [GREEN] `dias_overdue` = `GREATEST(DATEDIFF(CURDATE(), fecha_de_pago_esperada_original), 0)` — live-derived, not stale batch `dias_de_atraso_de_pago=44`. Documented in `_build_sql_window` comment.
- [x] 1.8 [REFACTOR] `_build_sql_window()` + `_build_sql_legacy()` — legacy path preserved for tenants without window config blocks.
- [x] 1.9 Run `uv run pytest tests/test_doris_sql_firstunpaid.py tests/test_doris_schema.py -v` — GREEN (13 new + updated tests).

## Phase 2: Core Display — Debt Card + Templates (Slice B, RED→GREEN)

- [x] 2.1 [TEST-RED] `tests/test_debt_card_display.py` — assert card HTML contains inversionista + cuenta_bancaria; assert "capital"/"saldo_capital" NOT in HTML.
- [x] 2.2 [TEST-RED] Assert graceful omission: `cuenta_bancaria=None` → bank row absent, no literal "None".
- [x] 2.3 [GREEN] `apps/agent/features/cobranza/tools.py` — `consultar_deuda` return dict adds `inversionista`, `cuenta_bancaria`; omits `principal_original`.
- [x] 2.4 [GREEN] `frontend/widget.js` — `_debtCardHtml`: add Inversionista + Cuenta Bancaria rows; null-guard both.
- [x] 2.5 [GREEN] `tenants/prestamype/responses.json` — add `{inversionista}` + `{cuenta_bancaria}` to donde_pagar, datos-de-pago, consulta_deuda templates.
- [x] 2.6 Run `uv run pytest tests/ -v` — Phase 1+2 GREEN.

## Phase 3: Comprobante Liviano (Slice C, RED→GREEN — ATOMIC)

- [x] 3.1 [TEST-RED] `tests/test_comprobante_liviano.py` — happy path: monto=7031.91, inversionista match → tipo="pago_cuota", estado="en_revision", audit captures inversionista.
- [x] 3.2 [TEST-RED] Mismatch: inversionista="Fondo B" → inversionista_match=False, estado="en_revision" (not rejected).
- [x] 3.3 [TEST-RED] Anti-dup: second identical (credito, monto) submission → dedup_ok=False.
- [x] 3.4 [TEST-RED] Classification: monto=81510.15 → "cancelacion"; monto=50000 → "abono".
- [x] 3.5 [TEST-RED] `test_comprobante_liviano.py` C.1 schema tests — validar_comprobante schema: monto required; inversionista+id_credito optional; cci+nro_operacion NOT required.
- [x] 3.6 [GREEN] `apps/agent/shared/config/tools_schema.py` — drop cci+nro_operacion from required; add optional inversionista+id_credito.
- [x] 3.7 [GREEN] `apps/agent/api/tool_registry.py` — `_validar_comprobante(monto, *, inversionista=None, id_credito=None)`; CCI from profile, never user args.
- [x] 3.8 [GREEN] `apps/agent/features/comprobantes/validator.py` — `validar_comprobante(profile, monto, *, inversionista=None, id_credito=None)` + `_classify_payment` + server-side CCI.
- [x] 3.9 [GREEN] `tenants/prestamype/guardrails.md` — lighter comprobante section: foto+monto+inversionista+ID(opcional); no CCI/nro_operacion demands.
- [x] 3.10 [GREEN] `tenants/prestamype/faq.json` — 8 FAQ entries aligned with lighter flow.
- [x] 3.11 [REFACTOR] `_classify_payment(monto, cuota, saldo)` extracted as pure function.
- [x] 3.12 Updated 22 pre-existing tests to new contract (test_cobranza_prestamype, test_frontend_branding_comprobante, test_chathub_comprobante, test_responses_engine). HTTP endpoint updated to call `validar_comprobante(profile, monto=monto)`.

## Phase 4: Verify + Regression (Slice D)

- [x] 4.1 Full suite `uv run pytest tests/ -q` — 541 passed, 0 failures.
- [ ] 4.2 Manual live P04197 via cobranza_ro: confirm balance=81510.15, cuota=7031.91, dias_mora from latest batch, monto_vencido=28127.64, cuotas_vencidas=4.
- [ ] 4.3 Widget smoke: Inversionista + Cuenta Bancaria rows present; capital row absent; "Vencido / a regularizar" prominent for en-mora borrower; "Estás al día" for al-día.
- [ ] 4.4 Bot smoke: submit test comprobante → EN REVISIÓN + audit has inversionista.
- [ ] 4.5 `git diff --stat` — only intended files; no debug prints.
- [ ] 4.6 Single production deploy (data_source=doris retained; no fixture fallback).

## Phase 5: Overdue Amount Primary Display (Slice E — DONE, commit d1dbc11)

- [x] E.1 [TEST-RED] SQL includes pagos_agg CTE with SUM monto_vencido + COUNT cuotas_vencidas
- [x] E.2 [TEST-RED] consultar_deuda returns monto_vencido=28127.64, cuotas_vencidas=4 for P04197-like fixture
- [x] E.3 [TEST-RED] template/card leads with vencido; al-día (cuotas_vencidas=0) shows "Estás al día"
- [x] E.4 [GREEN] doris_debt_source: pagos_agg CTE + LEFT JOIN + explicit defaults in _row_to_profile
- [x] E.5 [GREEN] tools.py: monto_vencido/formatted/cuotas_vencidas in consultar_deuda
- [x] E.6 [GREEN] shared/templates.py: build_variables adds monto_vencido/cuotas_vencidas/inversionista/cuenta_bancaria
- [x] E.7 [GREEN] responses.json: template leads with vencido/cuotas, saldo secondary
- [x] E.8 [GREEN] widget.js: vencido section prominent when cuotas_vencidas>0; al-día edge case
- [x] E.9 [GREEN] mock/borrowers.json: all 5 borrowers have monto_vencido + cuotas_vencidas
- [x] E.10 Full suite: 560 passed, 0 failures

## Commits (branch: feat/prestamype-credito-comprobante)

- f57817b — feat(doris): first-unpaid balance + latest-batch window CTE (Slice A)
- 4c60a6a — feat(display): inversionista + cuenta_bancaria in debt card + templates (Slice B)
- 54f620e — feat(comprobante): lighter flow — monto-only, server-side CCI, inversionista as warn (Slice C)
- c98a167 — feat(comprobante): image SHA-256 dedup (Slice D)
- d1dbc11 — feat(cobranza): surface monto_vencido as primary debt display (Slice E)
- a1b8f45 — fix(doris): cast numero_de_cuenta DECIMAL(38,0) at source to prevent scientific notation (Hotfix F)
