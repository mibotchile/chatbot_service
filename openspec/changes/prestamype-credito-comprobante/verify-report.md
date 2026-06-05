# Verify Report: prestamype-credito-comprobante

## Verdict: PASS WITH WARNINGS

**Test suite**: 541 passed, 0 failures (`uv run pytest tests/ -q`, 4.05s)
**Issues**: 0 CRITICAL, 3 WARNING, 2 SUGGESTION
**Branch**: feat/prestamype-credito-comprobante (3 commits on main)
**Diff size**: 19 files, +1209 / -247 lines (accepted as single deploy per user decision)

---

## Spec Compliance Matrix

| Spec Requirement | Test | Status |
|---|---|---|
| Balance = first-unpaid saldo (P04197 = 81510.15, NOT 108450) | test_p04197_balance_is_first_unpaid_not_max | PASS |
| No MAX(saldo_por_cancelar) in SQL | test_build_sql_uses_row_number_not_max_for_pagos | PASS |
| cuota = COALESCE(actualizada, mensual) — NULL fallback | test_p04197_cuota_coalesce_uses_mensual_when_actualizada_is_null | PASS |
| next_due_date = 2026-03-02 | test_p04197_next_due_date_is_first_unpaid_row | PASS |
| Latest batch only (ROW_NUMBER PARTITION BY id_credito) | test_latest_batch_is_deterministic_regardless_of_row_order | PASS |
| days_overdue = DATEDIFF(CURDATE(), fecha_de_pago_esperada_original) | test_build_sql_derives_days_overdue | PASS |
| inversionista + cuenta_bancaria in debt card | test_consultar_deuda_returns_inversionista/cuenta_bancaria | PASS |
| capital/principal_original absent from card | test_consultar_deuda_omits_principal_original | PASS |
| NULL cuenta_bancaria gracefully omitted | test_consultar_deuda_cuenta_bancaria_none_is_ok | PASS |
| widget null guards (rows conditional on truthiness) | test_debt_card_contract_capital_absent | PASS |
| validar_comprobante: monto required, inversionista/id_credito optional | test_validar_comprobante_schema_* (5 tests) | PASS |
| CCI from profile, never user input | test_validar_comprobante_cci_from_profile_not_user | PASS |
| inversionista mismatch = WARN, stays EN REVISIÓN | test_validate_comprobante_inversionista_mismatch_warns_not_rejects | PASS |
| Anti-dup by (credito, monto) | test_validate_comprobante_anti_dup_same_monto | PASS |
| classify: pago_cuota / abono / cancelacion | test_validate_comprobante_* (3 classification tests) | PASS |
| identity gate intact | test_gate_blocks_validar_comprobante_without_identity | PASS |
| prestaunion not regressed | test_prestaunion_still_uses_mock | PASS |

---

## Issues

### WARNING 1: Dedup key weakened from nro_operacion → (credito, monto)

**Risk**: Two legitimate same-amount payments on the same credit (e.g., two monthly installments of identical value, or a retry after a bank error) will be hard-blocked as duplicates within the same comprobantes.json file. The dedup store is file-backed (persistent across restarts) with no TTL.

**Mitigating factors**: Spec says "anti-duplicate logic MUST remain active" — it is. All comprobantes remain EN REVISIÓN anyway, so a human reconciler can manually accept a false-positive duplicate.

**Recommendation before deploy**: Decide if (credito, monto) is acceptable for prod or if a time-window TTL (e.g., 24h) or date component should be added to the key.

### WARNING 2: DATEDIFF(CURDATE()) — live Doris syntax unverified (BLOCKING pre-deploy)

**Risk**: `GREATEST(DATEDIFF(CURDATE(), p.fecha_de_pago_esperada_original), 0)` is untested against live Doris. If `fecha_de_pago_esperada_original` is stored as VARCHAR (not DATE/DATETIME), DATEDIFF returns NULL on Doris. All tests are mock-only.

**Status**: Task 4.2 (manual live P04197 verification) covers this and is marked incomplete.

### WARNING 3: TODO in solicitar_documento (tools.py:603) touched in Slice B

The TODO about production document delivery destination security was carried forward in the Slice B commit. It is a pre-existing limitation (not a new gap from this change) and is in `solicitar_documento`, not the comprobante path. Low risk for this deploy.

---

## SUGGESTIONS

1. Add time-windowed dedup (24h TTL or month+year in key) to eliminate false-positive duplicate rejection for same-amount repeat payments.
2. Add `dedup_window_hours` config in tenant.config.json so the window is tenant-tunable without code changes.

---

## Design Coherence

| Decision | Implemented | Notes |
|---|---|---|
| pagos_selection + batch_selection config-driven | Yes | tenant.config.json |
| _build_sql_window() + _build_sql_legacy() split | Yes | legacy tenants unaffected |
| COALESCE in SQL, not Python | Yes | clean separation |
| CCI from profile, never user args | Yes | validator.py line 162 |
| inversionista mismatch = WARN not reject | Yes | inversionista_match False, estado en_revision |
| comprobante always EN REVISIÓN | Yes | hardcoded "en_revision" |
| data_source stays doris | Yes | no fixture fallback |
| principal_original removed from display | Yes | tools.py + widget.js + templates |

---

## Task Completeness

| Task | Status |
|---|---|
| 1.1–1.9 Slice A SQL + config + tests | COMPLETE |
| 2.1–2.6 Slice B display | COMPLETE |
| 3.1–3.12 Slice C comprobante | COMPLETE |
| 4.1 Full suite green (541 passed) | COMPLETE |
| 4.2 Live P04197 manual verification | INCOMPLETE — blocking pre-deploy |
| 4.3 Widget smoke test | INCOMPLETE — pre-deploy |
| 4.4 Bot smoke comprobante flow | INCOMPLETE — pre-deploy |
| 4.6 Production deploy | INCOMPLETE — pending |

---

## Manual Live Checks Required Before Deploy

1. **BLOCKING** — Run window SQL for P04197 on live Doris (cobranza_ro, 10.110.0.15:9030). Confirm: balance=81510.15, cuota=7031.91, next_due=2026-03-02, days_overdue is a non-null positive integer.
2. **BLOCKING** — Confirm `fecha_de_pago_esperada_original` column type is DATE or DATETIME (not VARCHAR) in `batch_pagos_v2_bronze`. Use `SELECT * FROM batch_pagos_v2_bronze LIMIT 1` and inspect the value format.
3. Widget smoke — Inversionista + Número de cuenta rows present; no capital/saldo_capital row.
4. Bot smoke — Submit test comprobante; confirm EN REVISIÓN + audit captures inversionista from profile.
5. **Decision needed** — Confirm (credito, monto) dedup key is acceptable for prod, or add TTL before go-live.

---

## Commits Verified

- f57817b — feat(doris): first-unpaid balance + latest-batch window CTE (Slice A)
- 4c60a6a — feat(display): inversionista + cuenta_bancaria in debt card + templates (Slice B)
- 54f620e — feat(comprobante): lighter flow — monto-only, server-side CCI, inversionista as warn (Slice C)
