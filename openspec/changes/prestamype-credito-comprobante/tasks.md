# Tasks: prestamype-credito-comprobante

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | 550–700 (10 files, window SQL + schema + frontend + prompt copy) |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | Slice A (SQL+config) → Slice B+C (display+comprobante) → Slice D (verify) |
| Delivery strategy | ask-on-risk |
| Chain strategy | pending (user ships as one deploy; internal review slices recommended) |

Decision needed before apply: Yes
Chained PRs recommended: Yes
Chain strategy: pending
400-line budget risk: High

### Suggested Work Units

| Unit | Goal | Likely PR | Notes |
|------|------|-----------|-------|
| A | Doris SQL correctness | PR 1 | `_build_sql` + config; all other slices depend on this |
| B+C | Display + Comprobante | PR 2 | targets PR 1 branch; atomic tool schema change |
| D | Verify + green suite | PR 3 | regression gate before single deploy |

---

## Phase 1: Foundation — Config + SQL Skeleton (Slice A, RED)

- [ ] 1.1 **[TEST-RED]** `tests/features/cobranza/test_doris_sql.py` — assert `_build_sql` output contains `ROW_NUMBER() OVER (PARTITION BY codigo_contrato` and `PARTITION BY id_credito`; assert it does NOT contain `MAX(saldo_por_cancelar)`.
- [ ] 1.2 **[TEST-RED]** Same file — mock cursor returning P04197 fixture rows (3 batches, first-unpaid saldo=81510.15); assert `consultar_deuda` returns `balance=81510.15`, `next_installment_amount=7031.91`, `next_due_date="2026-03-02"`.
- [ ] 1.3 **[TEST-RED]** Assert cuota COALESCE: when `cuota_esperada_actualizada=NULL` fixture row, result equals `cuota_esperada_mensual`; when both present, uses `cuota_esperada_actualizada`.
- [ ] 1.4 **[TEST-RED]** Assert `dias_mora` comes from latest-batch row only (not earlier batches) — deterministic across fixture permutations.
- [ ] 1.5 Add `pagos_selection` + `batch_selection` blocks to `tenants/prestamype/tenant.config.json`; add `cuenta_bancaria→numero_de_cuenta` to `column_map`; mark `saldo_por_cancelar`, `cuota_esperada_mensual`, `fecha_de_pago_esperada_original` as `from_selected_row: true`; remove `agg: MAX` from those columns; keep `principal_original` omitted.
- [ ] 1.6 **[GREEN]** Redesign `_build_sql` in `apps/agent/features/cobranza/doris_debt_source.py` — emit `pagos_sel` CTE with `ROW_NUMBER() OVER (PARTITION BY codigo_contrato ORDER BY (fecha_de_pago_del_cliente IS NULL) DESC, fecha_de_pago_esperada_original ASC)` and `asig_sel` CTE with `ROW_NUMBER() OVER (PARTITION BY id_credito ORDER BY creado_el DESC, archivo DESC)`; JOIN on `p.rn=1 AND a.rn=1`; embed `COALESCE(cuota_esperada_actualizada, cuota_esperada_mensual)` for cuota field.
- [ ] 1.7 **[GREEN]** Resolve open question live: inspect `dias_de_atraso_de_pago` column in first-unpaid row vs `today - fecha_de_pago_esperada_original`; wire whichever is authoritative; document in a code comment.
- [ ] 1.8 **[REFACTOR]** Extract `_pagos_cte()` and `_asig_cte()` private helpers if SQL exceeds 40 lines; keep `_build_sql` readable.
- [ ] 1.9 Run `uv run pytest tests/features/cobranza/test_doris_sql.py -v` — all tests GREEN.

---

## Phase 2: Core Display — Debt Card + Templates (Slice B, RED→GREEN)

- [ ] 2.1 **[TEST-RED]** `tests/features/cobranza/test_debt_card.py` — mock `consultar_deuda` return with `inversionista="Fondo A"`, `numero_de_cuenta="19200123456789"`, `cci="00219200123456789012"`, `capital=None`; assert rendered card HTML contains "Fondo A", "19200123456789"; assert "capital" / "saldo_capital" NOT in HTML.
- [ ] 2.2 **[TEST-RED]** Assert graceful omission: when `numero_de_cuenta=None`, the bank-account row is absent from card HTML; no literal "None" in output.
- [ ] 2.3 **[GREEN]** `apps/agent/features/cobranza/tools.py` — `_credit_brief` / `consultar_deuda` return dict: add `inversionista`, `cuenta_bancaria` (from `numero_de_cuenta`); explicitly omit `principal_original` / `capital` keys.
- [ ] 2.4 **[GREEN]** `frontend/widget.js` — `_debtCardHtml`: add Inversionista row; add Cuenta Bancaria row with `numero_de_cuenta`; use corrected `balance` (not MAX); guard both rows with null-check.
- [ ] 2.5 **[GREEN]** `tenants/prestamype/responses.json` — add `{inversionista}` + `{cuenta_bancaria}` placeholders to `donde_pagar`, `datos-de-pago`, and `consulta_deuda` templates.
- [ ] 2.6 Run `uv run pytest tests/features/cobranza/ -v` — Phase 1+2 GREEN.

---

## Phase 3: Comprobante Liviano (Slice C, RED→GREEN — ATOMIC)

> Tasks 3.1–3.7 must land together. Schema + registry + validator must be consistent at every commit; partial deploy breaks the LLM tool contract.

- [ ] 3.1 **[TEST-RED]** `tests/features/comprobantes/test_validate_comprobante.py` — mock profile `{cci:"00219200...", inversionista:"Fondo A"}`; call `validate_comprobante(profile, monto=7031.91, inversionista="Fondo A")`; assert `tipo="pago_cuota"`, `estado="en_revision"`, `inversionista_match=True`, `audit["inversionista"]="Fondo A"`.
- [ ] 3.2 **[TEST-RED]** Mismatch scenario: `inversionista="Fondo B"` → assert `inversionista_match=False`, `estado="en_revision"` (NOT rejected), `cuenta_valida=True` (mismatch is warn-only).
- [ ] 3.3 **[TEST-RED]** Anti-dup: call twice with same monto+credito → second call returns `dedup_ok=False`.
- [ ] 3.4 **[TEST-RED]** Classification: monto=81510.15 → `tipo="cancelacion"`; monto=50000 → `tipo="abono"`.
- [ ] 3.5 **[TEST-RED]** `test_tools_schema.py` — assert `validar_comprobante` schema: `monto` required; `inversionista` optional; `id_credito` optional; `nro_operacion` NOT in required list; `cci` NOT in required list.
- [ ] 3.6 **[GREEN]** `apps/agent/shared/config/tools_schema.py` — `validar_comprobante`: drop `cci` + `nro_operacion` from required; add optional `inversionista` (str), optional `id_credito` (str); keep `monto` required.
- [ ] 3.7 **[GREEN]** `apps/agent/api/tool_registry.py` — `_validar_comprobante` handler signature: `(monto, *, inversionista=None, id_credito=None)`; resolve CCI+inversionista from `profile` (Doris verified), never from user args.
- [ ] 3.8 **[GREEN]** `apps/agent/features/comprobantes/validator.py` — implement `validate_comprobante(profile, monto, *, inversionista=None, id_credito=None)` per design interface: CCI from `profile["cci"]`; classify monto; mismatch → `inversionista_match=False` warn + EN REVISIÓN; anti-dup by `(credito, monto[, op])`; audit record includes `inversionista`.
- [ ] 3.9 **[GREEN]** `tenants/prestamype/guardrails.md` — rewrite comprobante section: ask foto + monto + inversionista + ID crédito (opcional); remove demands for CCI / nro_operacion; flexible collection rule.
- [ ] 3.10 **[GREEN]** `tenants/prestamype/faq.json` — align comprobante FAQ entries with lighter flow.
- [ ] 3.11 **[REFACTOR]** Extract `_classify_payment(monto, cuota, saldo)` pure function; unit-test it in isolation.
- [ ] 3.12 Run `uv run pytest tests/features/comprobantes/ -v` — Phase 3 GREEN.

---

## Phase 4: Verify + Regression (Slice D)

- [ ] 4.1 Run full suite `uv run pytest tests/ -v` — all GREEN; no regressions from Phase 1–3.
- [ ] 4.2 Manual live check (gated, not in CI): query P04197 via `cobranza_ro`; assert balance=81510.15, cuota=7031.91, dias_mora from latest batch `_20052026_F`; document result.
- [ ] 4.3 Verify widget renders: load chat locally with mock session; confirm Inversionista + Cuenta Bancaria rows appear; confirm capital row absent.
- [ ] 4.4 Smoke: submit test comprobante via bot; confirm EN REVISIÓN response + audit log includes inversionista.
- [ ] 4.5 `git diff --stat` — confirm only intended files changed; no debug prints / stray TODO hacks.
- [ ] 4.6 Single deploy to production (all slices together). Confirm Doris data_source=doris remains; no fixture fallback re-introduced.
