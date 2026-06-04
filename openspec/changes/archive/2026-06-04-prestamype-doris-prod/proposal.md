# Proposal: prestamype to Doris/prod (identity gate hardening)

## Intent

A non-existent DNI was accepted by the cobranza identity gate, revealing debt. Root cause has two layers: (1) prestamype runs `data_source="mock"` so it identifies against the DEMO fixture (`borrowers.json`) instead of real data; (2) `doris_debt_source._resolve_dni_credits` falls through to the fixture when Doris returns OK with zero rows (DNI not found), instead of returning `[]` — fixture fallback must fire ONLY on Exception (Doris down). This change moves prestamype to real Doris/prod and hardens the gate. Highest-sensitivity surface: it controls who can see debt.

## Scope

### In Scope
- Format-validate DNI/RUC (8-digit / 11-digit) in `_identificar_cliente` before touching any source.
- Fix fall-through in `_resolve_dni_credits`: Doris OK + empty rows -> `return []`; fixture ONLY on Exception.
- Per-tenant `allow_fixture_fallback` flag in `tenant.config.json` (prestamype/prod=false, prestaunion/demo=true).
- Switch prestamype `data_source` "mock" -> "doris".
- Fix container image so `pymysql` installs (currently excluded from build due to pydoris C-toolchain issue -> ModuleNotFoundError).

### Out of Scope
- Second identity factor (DNI is single-factor) — see Open Decision 3; likely a separate change.
- Any behavior change for prestaunion (demo) — stays mock + fixture, zero behavior delta.
- BigQuery / dashboard / downstream reporting changes.

## Capabilities

### New Capabilities
- None

### Modified Capabilities
- `cobranza-identity`: identity gate must reject malformed DNI/RUC, must not accept debtors absent from the real source, and must disable fixture fallback in prod tenants. (If no `cobranza-identity` spec exists, sdd-spec creates it as the canonical gate spec.)

## Approach

Two tracks. CODE (TDD, testable with fixtures): format validation + fall-through fix + per-tenant flag. OPS/DEPLOY (gated on blockers): rebuild image with pymysql, confirm Doris is populated, flip data_source, deploy. Code track lands first behind the flag without changing prestamype's live source; ops track flips the source only after blockers clear.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `apps/agent/api/tool_registry.py` | Modified | DNI/RUC format validation in `_identificar_cliente` |
| `apps/agent/features/cobranza/doris_debt_source.py` | Modified | Fix `_resolve_dni_credits` fall-through (~line 234) |
| `apps/agent/features/cobranza/debt_source.py` | Modified | Honor `allow_fixture_fallback` flag |
| `tenants/prestamype/tenant.config.json` | Modified | `data_source` mock->doris; `allow_fixture_fallback=false` |
| `tenants/prestaunion/tenant.config.json` | Modified | `allow_fixture_fallback=true` (preserve demo) |
| Dockerfile / build | Modified | Install pymysql |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Doris missing prestamype real debtors -> ALL identification breaks | High | BLOCKER: confirm `batch_asignacion_review_bronze` populated before flipping data_source |
| pymysql build fix fails / driver won't load | Med | BLOCKER: validate image rebuilds with pymysql and driver imports |
| Prod identity regression (reveals/denies debt wrongly) | Med | Strict TDD, adversarial verify, staged deploy, rollback flag |

## Rollback Plan

Code: `git revert` the change SHAs. Config: revert prestamype `data_source` to "mock" and `allow_fixture_fallback` to prior values (single-file edit, instant). Image: redeploy prior container tag. The flag flip is the fast kill-switch for prod.

## Dependencies

- Doris `batch_asignacion_review_bronze` populated with prestamype debtors (UNVERIFIED — gates apply).
- pymysql available in the runtime image (build fix).

## Success Criteria

- [x] Malformed DNI/RUC rejected before any source lookup.
- [x] Doris OK + empty rows returns `[]` (no fixture) in prod tenants; fixture fires only on Exception.
- [x] prestamype identifies against real Doris; non-existent DNI is rejected.
- [x] prestaunion demo behavior unchanged (mock + fixture).
- [x] Image builds and loads pymysql; full test suite green (baseline 424).
