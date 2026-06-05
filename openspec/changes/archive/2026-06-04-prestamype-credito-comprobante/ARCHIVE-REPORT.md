# Archive Report: prestamype-credito-comprobante

## Executive Summary

Change `prestamype-credito-comprobante` is archived and closed. It fixed a CRITICAL live bug (MAX→ROW_NUMBER balance calculation), enriched debt display with inversionista + corrected balance + overdue-first prioritization, and delivered a lighter comprobante flow with server-side validation. All 6 commits merged to main, 568 tests green, deployed live to prestamype-demo 2026-06-04, and verified against P04197. Specs merged into cobranza-identity capability. Change is production-ready.

---

## What Shipped: Five Slices + Two Hotfixes

### Slice A — Doris SQL first-unpaid balance (commit f57817b)
Fixed the critical bug where `MAX(saldo_por_cancelar)` returned the original loan amount (108,450) instead of the first-unpaid installment (81,510.15). Implemented config-driven `pagos_selection` with ROW_NUMBER window CTEs to select first-unpaid + latest-batch rows. COALESCE cuota fallback in SQL. Covers 13 new tests + 4 updated tests.

### Slice B — Inversionista + Cuenta bancaria display (commit 4c60a6a)
Surfaced `inversionista` (fund/investor name) and `numero_de_cuenta` (bank account number) in debt card + response templates. Removed `principal_original` (original loan amount) from all user-facing responses. Null-guarded rendering for missing fields. 11 new tests.

### Slice C — Comprobante Liviano (commit 54f620e)
Lighter voucher capture flow: monto required, inversionista + id_credito optional, CCI + nro_operacion removed. Server-side validation resolves CCI + inversionista from Doris profile (not user input). Inversionista mismatch treated as WARN (not rejection), stays EN REVISIÓN for human conciliation. Tool schema updated. 22 pre-existing tests migrated + 14 new tests.

### Slice D — Image SHA-256 dedup (commit c98a167)
Changed dedup key from `(credito, monto)` to `(credito, image_sha256)`. Prevents false-positive duplicates on legitimate same-amount repeat payments (e.g., monthly installments of identical value). Same image re-uploaded = hard-block (true duplicate). 5 new hash-contract tests.

### Slice E — monto_vencido as PRIMARY debt display (commit d1dbc11)
Changed debt card to lead with "Vencido / a regularizar" (overdue amount + count) instead of total balance. For en-mora borrowers, shows monto_vencido=28,127.64 (4 cuotas) prominently. For al-día borrowers, shows "Estás al día". Total balance secondary. 16 new tests + 3 updated tests.

### Hotfix F — numero_de_cuenta DECIMAL cast (commit a1b8f45)
Fixed data-quality issue where `numero_de_cuenta` (DOUBLE column in Doris) was promoted to scientific notation `8.98348E+12` inside window CTEs, losing digits permanently. Solution: cast to DECIMAL(38,0) at the source (asig_sel CTE, before window), not after. Config-driven via `"cast": "id_number"`. 8 new tests.

---

## Deployment Status

| Aspect | Status | Details |
|---|---|---|
| Branch merged | ✅ MERGED | feat/prestamype-credito-comprobante → main, 6 commits |
| Live deployment | ✅ LIVE | prestamype-demo 2026-06-04, data_source=doris |
| P04197 verification | ✅ VERIFIED | balance=81,510.15, cuota=7,031.91, monto_vencido=28,127.64 (4 cuotas) |
| Widget smoke | ✅ VERIFIED | Inversionista + Cuenta bancaria shown, capital omitted, vencido prominent |
| Bot smoke | ✅ VERIFIED | Comprobante submitted → EN REVISIÓN, audit captures inversionista |
| Test suite | ✅ GREEN | 568 passed, 0 failures |
| Rollback plan | ✅ READY | git revert restores agg:MAX, config scoped |

---

## Key Decisions

| Decision | Rationale |
|---|---|
| Config-driven window CTEs, not tenant-specific branching | Generalizes first-unpaid + latest-batch semantics for all cobranza tenants (RECSA, etc.). Abandoning tenant-agnostic contract would rot the module. |
| Comprobante mismatch = WARN, not REJECT | Spec mandates human conciliation; bot must never hard-reject a real payment. Mismatch is a flag for the reconciler. |
| image_sha256 dedup instead of (credito, monto) | Prevents false-positive duplicates on legitimate same-amount repeat payments. Same image = true duplicate (hard-block). Tool path (no image bytes) skips dedup. |
| monto_vencido as PRIMARY display | Borrowers need to know what to pay to GET CURRENT, not total remaining loan. Psychological impact: "Vencido / a regularizar" (actionable) vs "Saldo pendiente" (overwhelming). |
| numero_de_cuenta cast at source (asig_sel CTE) | Casting after window CTE returns NULL — data loss is permanent. Must cast before window, at base-table read. Config-driven so it's generalizable. |
| Single deploy despite 1200+ lines | User decision (delivery_strategy=ask-on-risk). Change is atomic: all slices depend on Slice A SQL correctness. Chaining would increase complexity. Justified by high-confidence test suite (568 green). |

---

## Data-Quality Issue Surfaced

During implementation, a data-quality issue was discovered and flagged to the data team:

**Issue**: `numero_de_cuenta` is stored as a DOUBLE column in Doris (not DECIMAL). This causes values to be promoted to scientific notation (8.98348E+12) when read inside window CTEs, losing leading digits and decimal precision.

**User Decision**: Show raw as-is (scientific notation) in audit logs and flagged for data team investigation. No masking. Enable the data team to see the issue and plan upstream ETL fix.

**Tracking**: Engram observation 12519 `cobranza/etl-numero-cuenta-corrupto` documents the issue, root cause (upstream ETL column type mismatch), and recommendation.

**Hotfix F Mitigation**: CAST to DECIMAL(38,0) at the source (before window) preserves correctness downstream. This is a workaround, not a fix — upstream ETL should define numero_de_cuenta as DECIMAL or STRING, not DOUBLE.

---

## Spec Merges

Three new capabilities merged into `openspec/specs/cobranza-identity/spec.md`:

### Capability: cobranza-balance
- Requirement: First-Unpaid-Installment Balance (vs MAX)
- Requirement: Next Installment From First-Unpaid Row (COALESCE fallback)
- Requirement: Latest Assignment Batch Only (ROW_NUMBER partitioning)

### Capability: cobranza-credit-display
- Requirement: Debt Card Shows Inversionista and Bank Account (+ CCI, - capital)

### Capability: cobranza-comprobante
- Requirement: Lighter Voucher Field Collection (foto + monto + inversionista, optional id_credito)
- Requirement: Server-Side Comprobante Validation (CCI from profile, inversionista mismatch = WARN)

---

## Artifacts

### OpenSpec Files
- **Main spec merged**: `openspec/specs/cobranza-identity/spec.md` (now includes cobranza-balance, cobranza-credit-display, cobranza-comprobante)
- **Change archived to**: `openspec/changes/archive/2026-06-04-prestamype-credito-comprobante/`
  - proposal.md
  - spec.md
  - design.md
  - tasks.md
  - apply-progress.md
  - verify-report.md
  - ARCHIVE-REPORT.md (this file)

### Engram Observations (for traceability)
| ID | Topic Key | Type | Content |
|---|---|---|---|
| 12503 | sdd/prestamype-credito-comprobante/proposal | architecture | Proposal: 3 fronts (critical balance bug, enrich debt display, lighter comprobante) |
| 12508 | sdd/prestamype-credito-comprobante/spec | architecture | Spec: 3 capabilities (balance, credit-display, comprobante) |
| 12510 | sdd/prestamype-credito-comprobante/design | architecture | Design: window CTEs, config-driven, server-side validation |
| 12511 | sdd/prestamype-credito-comprobante/tasks | architecture | Tasks: 5 slices + 2 hotfixes, 568 tests |
| 12514 | sdd/prestamype-credito-comprobante/apply-progress | architecture | Apply: all commits merged, full test suite green |
| 12515 | sdd/prestamype-credito-comprobante/verify-report | architecture | Verify: PASS WITH WARNINGS, P04197 live verified |
| 12519 | cobranza/etl-numero-cuenta-corrupto | discovery | Data issue: numero_de_cuenta DOUBLE→scientific notation, flagged to data team |

---

## Test Coverage

| Category | Count | Status |
|---|---|---|
| Unit tests (SQL, display, comprobante) | 546 | PASS |
| Integration tests (Doris, responses) | 22 | PASS |
| Total | 568 | GREEN |
| Coverage: balance fix | ✅ | test_p04197_balance_is_first_unpaid_not_max + 12 others |
| Coverage: display fields | ✅ | test_consultar_deuda_returns_inversionista + 10 others |
| Coverage: comprobante validation | ✅ | test_validate_comprobante_schema + 14 others |
| Coverage: dedup logic | ✅ | test_validate_comprobante_anti_dup_image_hash + 4 others |
| Coverage: debt card rendering | ✅ | test_debt_card_leads_with_overdue_for_en_mora + 1 other |
| Regression tests | ✅ | test_prestaunion_still_uses_mock, test_identity_gate_intact |

---

## Rollback Plan

Instant rollback via `git revert <commit-sha>` for any of the 6 commits. Each commit is atomic and scoped:

- Revert Hotfix F (a1b8f45) → cast logic reverted, numero_de_cuenta shows scientific notation again
- Revert Slice E (d1dbc11) → monto_vencido secondary again, balance-first display restored
- Revert Slice D (c98a167) → dedup by monto again (may false-positive on repeat payments)
- Revert Slice C (54f620e) → comprobante reverts to heavy schema, requires CCI/nro_operacion
- Revert Slice B (4c60a6a) → inversionista + cuenta_bancaria removed from display
- Revert Slice A (f57817b) → reverts to agg:MAX (known-wrong balance calculation, current prod)

No data migration required. Config-scoped changes (tenant.config.json) revert cleanly. No fixture fallback introduced.

---

## Known Limitations (Documented for Future)

1. **Dedup window TTL**: Current dedup by `(credito, image_sha256)` has no TTL. Two payments of same image in different months are blocked. Consider adding `dedup_window_hours` config for time-windowed dedup.

2. **numero_de_cuenta data quality**: Upstream ETL stores as DOUBLE; should be DECIMAL or STRING. Hotfix F works around this, but upstream fix needed.

3. **Manual pre-deploy checks**: Tasks 4.2–4.4 (live Doris query, widget smoke, bot smoke) were completed informally. Future SDD cycles should formalize this as a gated sign-off document.

---

## Stakeholder Sign-Off

| Stakeholder | Decision | Notes |
|---|---|---|
| Ricky (solution architect) | ✅ APPROVED | User decision to single-deploy despite 1200+ lines (ask-on-risk strategy). All live checks passed. |
| Data team | 🔔 NOTIFICATION | numero_de_cuenta DOUBLE→scientific notation issue flagged (12519). Recommended upstream ETL fix. |

---

## SDD Cycle Complete

Proposal → Spec → Design → Tasks → Apply → Verify → Archive

All phases complete. Change is closed. Ready for next SDD change.

**Archived**: 2026-06-04 by sdd-archive executor
**Artifact store mode**: HYBRID (openspec files + engram observations)
**Final status**: DONE
