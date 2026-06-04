# cobranza-identity Specification
## Change: prestamype-doris-prod

## Purpose
Governs the identity gate that controls whether a cobranza chatbot session can proceed with debt exposure. Covers DNI/RUC format validation, data source selection per tenant, fixture fallback policy, and hard blockers before activating a production data source.

---

## Requirements

### Requirement: DNI/RUC Format Validation
The system MUST normalize (strip all non-digit characters) then validate length BEFORE querying any debt source. DNI MUST be exactly 8 digits; RUC MUST be exactly 11 digits. Any other value MUST be rejected with `identified: False` and MUST NOT trigger a source query.

**Scenarios:**
- Valid 8-digit DNI → passes, source queried
- Valid 11-digit RUC → passes, source queried
- DNI with formatting dots/spaces → normalized then accepted if 8 digits
- Too-short input (e.g. 4 digits) → `identified: False`, NO source query
- Too-long input (not 8 or 11 digits) → `identified: False`, NO source query
- Non-numeric input → empty after normalization → `identified: False`, NO source query

### Requirement: Doris Fall-Through Fix
When Doris responds successfully (no exception) with zero rows, `_resolve_dni_credits` MUST return `[]`. Fixture fallback MUST NOT be invoked on empty result. Fixture fallback MUST ONLY be invoked on Exception (connection failure, timeout, driver error).

**Scenarios:**
- Doris reachable, DNI not found → return `[]`, fixture NOT consulted
- Doris reachable, DNI found → return real credits, fixture NOT consulted
- Doris raises Exception → defer to tenant's `allow_fixture_fallback` policy

### Requirement: Per-Tenant Fixture Fallback Policy
Each tenant MUST declare `allow_fixture_fallback` boolean in `tenant.config.json`. When `false` + Exception → MUST NOT use fixture; respond with safe degradation message: "No puedo verificar tu identidad en este momento; intenta más tarde o te derivo con un asesor." When `true` + Exception → MAY use fixture.

**Tenants:** prestamype/prod = `false`. prestaunion/demo = `true`.

**Scenarios:**
- Prod tenant (false), Doris down → safe message, NO fixture, user NOT identified
- Demo tenant (true), source down → fixture fallback allowed
- prestaunion baseline unchanged → all 424 tests green

### Requirement: Tenant Data Source Activation (prestamype → Doris)
`data_source` in `tenants/prestamype/tenant.config.json` MUST be set to `"doris"`. This MUST NOT deploy to production unless B1+B2+B3 blockers are cleared.

**Scenarios:**
- prestamype on Doris: DNI in `batch_asignacion_review_bronze` → identified; absent → `identified: False`, no fixture
- Regression guard: non-existent DNI → no debt revealed

### Requirement: pymysql Image Availability (Hard Blocker B1+B2)
Container image MUST have pymysql installed and importable. `ModuleNotFoundError` blocks deployment. Cleared when: `python -c "import pymysql"` exits 0 AND a live Doris connection query succeeds.

**Scenarios:**
- Image rebuilt with pymysql → `import pymysql` exits 0
- Live connection test → query against `batch_asignacion_review_bronze` returns results

### Requirement: Doris Data Confirmation (Hard Blocker B3)
`batch_asignacion_review_bronze` MUST be confirmed to contain prestamype real debtors (non-zero count, operator-confirmed) BEFORE flipping `data_source`. Zero rows blocks activation.

**Scenarios:**
- Count query returns non-zero → Ricky confirms plausible → blocker cleared
- Count query returns 0 → flip MUST NOT proceed

### Requirement: Rollback Kill-Switch
Reverting `data_source` to `"mock"` and `allow_fixture_fallback` to `true` in prestamype tenant config MUST be sufficient to restore pre-change behavior. No code deploy or image rebuild required.

**Scenario:**
- Operator reverts config → mock source re-active, fixture fallback re-enabled, no deploy

---

## Acceptance Blockers (gate Doris activation only — not code track)

| # | Blocker | Cleared by |
|---|---------|-----------:|
| B1 | pymysql installs in rebuilt image | `import pymysql` exits 0 |
| B2 | Doris connection succeeds in rebuilt image | Live query returns results |
| B3 | `batch_asignacion_review_bronze` has prestamype data | Operator count query, non-zero, confirmed |

Code-track (DNI validation, fall-through fix, per-tenant flag) is NOT gated on B1–B3.

---

## Out of Scope
- Second identity factor (separate future change)
- Any prestaunion behavior change
- BigQuery / dashboard / reporting
