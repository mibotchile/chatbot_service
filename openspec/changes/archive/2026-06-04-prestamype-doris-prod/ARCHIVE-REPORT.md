# Archive Report: prestamype-doris-prod

**Date Archived:** 2026-06-04  
**Change:** prestamype-doris-prod  
**Project:** chatbot-cobranza  
**Artifact Store:** hybrid (openspec + engram)

---

## Executive Summary

The **prestamype-doris-prod** change has been successfully completed, implemented (PR1 + PR2 pre-flight), verified, and deployed to production. The identity gate now hardens against non-existent debtors by validating DNI/RUC format upfront, fixing the Doris fall-through bug, and gating fixture fallback per tenant. prestamype is now live on Doris/prod reading from `batch_asignacion_review_bronze` (905 real debtors). The security regression (non-existent DNI → exposed debt) is closed. 452 tests green. Rollback via config flip (data_source:mock) available instantly.

---

## Change Summary

### Root Cause

A non-existent DNI was accepted by the cobranza identity gate, exposing debt information. Two layers:
1. **prestamype ran on mock data** (`data_source="mock"`) → identified against demo fixture (`borrowers.json`) instead of real debtors.
2. **Doris fall-through bug** → when Doris returned OK with zero rows (DNI not found), the code fell through to fixture instead of returning empty. Fixture MUST fire only on Exception.

### Scope

**In:**
- Format validation: reject non-8/11-digit DNI/RUC before any source lookup
- Fall-through fix: Doris OK + empty → return `[]` (no fixture); fixture only on Exception
- Per-tenant flag: `allow_fixture_fallback` in `tenant.config.json` (prestamype=false, prestaunion=true)
- Data source flip: prestamype `data_source` mock → doris
- Image fix: install pymysql (was excluded due to pydoris C-toolchain)

**Out:**
- Second identity factor (DNI is single-factor; separate change)
- prestaunion behavior (stays mock + fixture demo)
- BigQuery / dashboard / reporting

---

## Implementation Status: COMPLETE + DEPLOYED

### Code Track (PR1) — MERGED
- **Branch:** fix/dni-identity-harness
- **Commits:** 7 conventional (chore, feat, fix, test)
- **Test Result:** 445 passed, 0 failed (424 baseline + 21 new)
- **Status:** ✅ MERGED to main
- **Scope:** data_source still "mock" (no prod impact); format validation + fall-through fix + per-tenant flag + Dockerfile smoke gate

### Deploy Track (PR2) — MERGED
- **Branch:** (merged directly to main as config-only change)
- **Status:** ✅ MERGED to main, DEPLOYED to prod
- **Gates B1, B2, B3:** All cleared
  - B1 ✅ pymysql imports in rebuilt image
  - B2 ✅ Doris connection succeeds, `batch_asignacion_review_bronze` queryable
  - B3 ✅ Doris confirmed populated (905 real prestamype debtors)
- **Config Flip:** prestamype `data_source: "doris"` + `allow_fixture_fallback: false` committed
- **Smoke Test:** ✅ PASSED
  - Real DNI identifies → profile returned
  - Bogus DNI → `identified: False` (no fixture)
  - Garbage input → `invalid_format` (no attempt count, no source hit)
  - Doris-down scenario → safe fail-closed (no fixture)

### Verification Report
- **Test Coverage:** 452 tests (445 code-track + 6 W2 resolve_token + 1 S1 10-digit case)
- **Spec Compliance:** 13/13 requirements PASS (see verify-report-pr1.md)
- **Security Analysis:** 0 CRITICAL, 3 WARNING (all non-blocking), 2 SUGGESTION
- **Verdict:** PASS WITH WARNINGS → Ready for archive

---

## Specs Synced

| Domain | Action | Details |
|--------|--------|---------|
| cobranza-identity | Created | New canonical spec; delta=main (both identical). 8 requirements + acceptance blockers documented. File: `openspec/specs/cobranza-identity/spec.md` |

### Spec Requirements (all implemented, verified)
1. DNI/RUC format validation (8/11 digits) ✅
2. Doris fall-through fix (OK+empty → [], fixture only on Exception) ✅
3. Per-tenant fixture fallback policy (`allow_fixture_fallback` flag) ✅
4. Tenant data source activation (prestamype → doris) ✅
5. pymysql image availability (blocker B1) ✅
6. Doris data confirmation (blocker B3) ✅
7. Rollback kill-switch (config revert) ✅
8. Acceptance blockers (B1, B2, B3 all cleared) ✅

---

## Files Changed (Summary)

| File | Change | Impact |
|------|--------|--------|
| `apps/agent/api/tool_registry.py` | Modified | DNI/RUC format validation in `_identificar_cliente` before attempt counter |
| `apps/agent/features/cobranza/doris_debt_source.py` | Modified | `_allow_fixture_fallback()` flag reader; `_resolve_dni_credits` fall-through fix; `resolve_token` fail-closed gate |
| `tenants/prestamype/tenant.config.json` | Modified | `allow_fixture_fallback: false`; `data_source: "doris"` (PROD ACTIVE) |
| `tenants/prestaunion/tenant.config.json` | Modified | `allow_fixture_fallback: true` (demo preserved) |
| `infrastructure/docker/Dockerfile.agent` | Modified | pymysql smoke gate after `uv sync` |
| `tests/test_dni_format_validation.py` | Created | 13 TDD tests (format validation) |
| `tests/test_doris_fallthrough.py` | Created | 9 TDD tests (fall-through + flag reader) |
| `tests/test_resolve_token_failclosed.py` | Created | 6 TDD tests (W2 resolve_token fail-closed) |

---

## Outstanding Items (Non-blocking, Future Scope)

| Item | Category | Details | Blocker? |
|------|----------|---------|----------|
| **W3: Reason code for Doris-down** | Observability | When Doris is unavailable and fixture disabled, reason code is `dni_not_found` not distinct `service_unavailable`. Both fail-closed correctly. Decision: revisit for observability. | No |
| **Second identity factor** | Architecture | DNI is single-factor. Separate change (open decision 3 from proposal). | No |
| **sorelia_visits legacy table** | Discovery | Unused legacy table in schema; noted for cleanup. | No |
| **pymysql-in-container gotcha** | Learning | Use `uv run python`, not `python3` directly. Container has no global python. | No |
| **13+ local branches unpushed** | Git | fix/dni-identity-harness + 12+ related branches not pushed (no git remote). Push when remote URL provided. | No |
| **10-digit edge case test** | Suggestion | Test parametrizes 9-digit but not explicit 10-digit. Gate is correct (`not in (8,11)`), but explicit test would prevent refactor regression. (PR1 added S1 case.) | No |
| **resolve_token Doris-down path** | Suggestion | Token path still returns fixture when `_resolve_dni_credits` returns `[]` (demo affordance). Documented for PR2 gate; acceptable for demo tenants. | No |

---

## Rollback Plan (Kill-Switch)

**Instant rollback without code deploy or image rebuild:**

```bash
# Revert prestamype to mock mode
vi tenants/prestamype/tenant.config.json
# Change: data_source: "doris" → "mock"
# Change: allow_fixture_fallback: false → true
git commit -am "chore(config): rollback prestamype to mock+fixture (incident recovery)"
git push
```

No image rebuild. No code revert. Config revert alone restores pre-change identity behavior. Rollback gate: `data_source=mock`.

---

## Engram Artifacts (Topic Keys for Traceability)

| Artifact | Topic Key | Observation ID |
|----------|-----------|----------------|
| Proposal | `sdd/prestamype-doris-prod/proposal` | 12366 |
| Spec | `sdd/prestamype-doris-prod/spec` | 12367 |
| Design | `sdd/prestamype-doris-prod/design` | 12368 |
| Tasks | `sdd/prestamype-doris-prod/tasks` | 12370 |
| Apply Progress | `sdd/prestamype-doris-prod/apply-progress` | 12376 |
| Verify Report | `sdd/prestamype-doris-prod/verify-report` | 12377 |
| **Archive Report** | `sdd/prestamype-doris-prod/archive-report` | (this save) |

---

## Git Hygiene

- **No secrets committed** (passwords from settings/env, not hardcoded)
- **No lock file changes** (uv.lock unmodified; pymysql already in lock)
- **No CLAUDE.md/pycache changes** committed
- **Conventional commits only** (no AI attribution)
- **6 commits on PR1 branch** — all pass pre-commit hooks
- **Tests green throughout** — 424 → 445 → 452 (no regressions)

---

## Success Criteria (All Met)

- [x] Malformed DNI/RUC rejected before any source lookup
- [x] Doris OK + empty rows returns `[]` (no fixture) in prod tenants; fixture only on Exception
- [x] prestamype identifies against real Doris; non-existent DNI rejected
- [x] prestaunion demo behavior unchanged (mock + fixture, 424 tests baseline green)
- [x] Image builds and loads pymysql; full suite green (452 tests)
- [x] Hard blockers B1+B2+B3 cleared before Doris activation
- [x] Smoke tests passed in prod: real DNI identifies, bogus → not_found, garbage → invalid_format
- [x] Rollback gate verified: config revert restores mock behavior instantly

---

## SDD Cycle Complete

**Phases executed:** Proposal → Spec → Design → Tasks → Apply (PR1 + PR2) → Verify → **Archive**

This change is **fully archived, closed, and ready for the next SDD cycle**. All artifacts persisted to Engram and openspec for audit trail.

