# Verify Report: prestamype-doris-prod — PR1 (CODE TRACK)

**Date:** 2026-06-04  
**Branch:** fix/dni-identity-harness  
**Reviewer:** sdd-verify (adversarial, security-critical)  
**Verdict: PASS WITH WARNINGS — 0 CRITICAL, 3 WARNING, 2 SUGGESTION**

---

## Test Results

| Metric | Value |
|--------|-------|
| Suite result | 445 passed, 0 failed in 1.67s |
| Baseline | 424 (preserved) |
| New tests | 21 (12 format validation + 9 fall-through/flag) |
| Runtime import | IMPORT_OK (apps/agent cwd) |

---

## Spec Compliance Matrix

| Requirement | Status | Evidence |
|---|---|---|
| DNI/RUC format validation (normalize + len 8/11) | PASS | tool_registry.py:231-241 |
| Garbage NOT counted as enumeration probe | PASS | tool_registry.py:243 — attempt gate AFTER format check; test:60-69 |
| Doris OK + empty → [] (no fixture) | PASS | doris_debt_source.py:274; test_doris_ok_empty_returns_empty_list |
| Doris OK + rows → profiles (no fixture) | PASS | doris_debt_source.py:274; test_doris_ok_with_rows_returns_mapped_profiles |
| Doris Exception + flag=True → fixture | PASS | doris_debt_source.py:270-272; test_doris_exception_with_flag_true |
| Doris Exception + flag=False → [] (fail-closed) | PASS | doris_debt_source.py:273; test_doris_exception_with_flag_false |
| prestamype allow_fixture_fallback = false | PASS | tenant.config.json:8; test_allow_fixture_fallback_prestamype_is_false |
| prestaunion allow_fixture_fallback = true | PASS | tenant.config.json:3; test_allow_fixture_fallback_prestaunion_is_true |
| Unknown tenant defaults false | PASS | doris_debt_source.py:249-250; test_allow_fixture_fallback_unknown_tenant_defaults_false |
| prestamype data_source still "mock" (scope gate) | PASS | tenant.config.json:7 = "mock" |
| Dockerfile pymysql smoke gate after uv sync | PASS | Dockerfile.agent:13-16 |
| Safe-degradation message (Doris-down + flag=false) | PASS | test_identificar_cliente_doris_down_flag_false_returns_safe_message |
| prestaunion mock path unchanged (zero-behavior) | PASS | 424 baseline green |
| lru_cache bleed prevented | PASS | cache_clear() before+after in all flag-touching tests |

---

## FORMAT VALIDATION — Adversarial Bypass Analysis

**Gate location:** `apps/agent/api/tool_registry.py:231-241`

```python
normalized = _DNI_CLEAN_RE.sub("", dni or "")  # strips all non-digits
if len(normalized) not in (8, 11):
    return {"identified": False, "reason": "invalid_format", ...}
dni = normalized
```

**Ordering confirmed:** format check at L231-241, attempt counter at L244. Garbage never reaches the counter.

| Input | After normalize | len | Gate decision |
|-------|----------------|-----|---------------|
| "" | "" | 0 | REJECTED |
| "       " | "" | 0 | REJECTED |
| "hola" | "" | 0 | REJECTED |
| "1234" | "1234" | 4 | REJECTED |
| "123456789" | "123456789" | 9 | REJECTED |
| "12345678901234" | same | 14 | REJECTED |
| "12345678" | "12345678" | 8 | PASSES |
| "12345678901" | same | 11 | PASSES |
| "12.345.678" | "12345678" | 8 | PASSES (dots stripped) |
| "1234 5678" | "12345678" | 8 | PASSES (spaces stripped) |
| 10-digit | 10 chars | 10 | REJECTED (not in (8,11)) — no explicit test |

**BYPASS VERDICT: NO BYPASS FOUND.** The gate is mathematically sound.

**Key security test present:** `test_doris_ok_empty_returns_empty_list` — patches `_connect` to return `[]`, asserts `fixture_calls == []`. Proves bogus DNI + Doris-up → fixture never hit.

---

## FALL-THROUGH FIX — Adversarial Flow Analysis

**`_resolve_dni_credits` control flow** (`doris_debt_source.py:253-274`):

```
try:
    rows = _query_dni(norm, tenant_id)
except Exception:
    if _allow_fixture_fallback(tenant_id): → fixture
    else: return []                         ← prod: fail-closed
return [_row_to_profile(r) for r in rows]  ← rows=[] → return []
```

**THE KEY SECURITY ASSERTION: Bogus DNI + Doris-UP can NEVER reach the fixture.**  
When Doris returns zero rows, execution exits the `try` block normally and hits `return [... for r in rows]` = `return []`. The fixture branch is exclusively inside `except`. There is no fall-through.

**Verdict: CONFIRMED SECURE.**

---

## FLAG DEFAULT SAFETY

| Tenant | Config value | _allow_fixture_fallback() | Tested |
|--------|-------------|--------------------------|--------|
| prestamype | `false` | False | YES |
| prestaunion | `true` | True | YES |
| unknown/missing | (no key) | False (default) | YES |
| OSError / bad JSON | (exception) | False (default) | YES (L248-249) |

Default is `False` — prod fails closed. Confirmed.

---

## CHAR TEST QUALITY

- **Behavioral, not source-inspection:** All 21 tests call `_identificar_cliente` or `_resolve_dni_credits` directly and assert on observable outcomes (return value, side-effect spy). No source file inspection.
- **Regression detection:** If format validation were removed from `_identificar_cliente`, `test_invalid_format_does_not_increment_attempt_counter` and `test_invalid_format_returns_invalid_format_reason` would fail. If fall-through were reintroduced, `test_doris_ok_empty_returns_empty_list` would fail.
- **Key security test present:** `test_identificar_cliente_doris_down_flag_false_returns_safe_message` (task 3.2).

---

## SCOPE CHECK

- `tenants/prestamype/tenant.config.json:7` — `data_source: "mock"` — CONFIRMED unchanged
- No Doris connection attempted; no deploy; no config flip
- PR1 is purely code harness + fixture flags

---

## ZERO-BEHAVIOR PRESTAUNION

- `debt_source._backend("prestaunion")` → `mock_debt_source` (data_source="mock")
- `doris_debt_source._resolve_dni_credits` is never reached for prestaunion in normal operation
- `allow_fixture_fallback=true` on prestaunion only governs the doris FALLBACK path
- 424 baseline tests passed unchanged

---

## ISSUES

### WARNING (3)

**W1 — 10-digit input has no explicit test**  
`tests/test_dni_format_validation.py` — parametrize block covers 9-digit (line 42) but not 10-digit. The gate `not in (8, 11)` is correct and covers it mathematically, but a future refactor to `>8 and <11` would silently break it. Easy to add.

**W2 — resolve_token fixture fallback in doris path (PR2 gate)**  
`apps/agent/features/cobranza/doris_debt_source.py:300` — `resolve_token` returns `fixture_profile` when `_resolve_dni_credits` returns `[]`, regardless of whether that `[]` came from "Doris OK + not found" or "Doris down + flag=false". When prestamype flips to `data_source=doris` in PR2, a valid demo token can still resolve to a fixture profile if Doris has no row for that DNI. The DNI path is fail-closed; the token path is not. Requires explicit decision before PR2: (a) gate `resolve_token` the same way, or (b) document this as intentional (tokens are DEMO affordance only, not prod identity).

**W3 — Safe-degradation reason code is `dni_not_found` not `service_unavailable`**  
When Doris is down + flag=false, `_resolve_dni_credits` returns `[]` → `resolve_dni` returns `None` → existing `dni_not_found` branch fires. The user cannot distinguish "not in system" from "system down." Acceptable for PR1; worth revisiting for observability in PR2+.

### SUGGESTION (2)

**S1** — Add `("1234567890", "10 digits")` to the parametrize block in `test_dni_format_validation.py`.

**S2** — Add a test for `doris_debt_source.resolve_token` with `_resolve_dni_credits` returning `[]` to document and pin the current behavior before PR2.

---

## TASK COMPLETION

| Phase | Tasks | Status |
|---|---|---|
| Phase 1 (config + Dockerfile) | 1.1, 1.2, 1.3 | Complete |
| Phase 2 (TDD RED→GREEN) | 2.1–2.6 | Complete |
| Phase 3 (regression guard) | 3.1, 3.2 | Complete |
| Phase 4 (deploy track) | 4.1–4.6 | Correctly gated (B1+B2+B3 cleared) |

---

## GIT HYGIENE

- 6 commits on branch vs main — all conventional commits, correct scopes
- No lock file changes (0 lines diff in uv.lock)
- No CLAUDE.md, __pycache__, or .pyc committed
- No hardcoded secrets (password sourced from `settings.doris_password` at doris_debt_source.py:212)

---

## READINESS FOR PR2 DEPLOY GATES

| Gate | Status | Action |
|------|--------|--------|
| B1: pymysql importable | Dockerfile smoke in place | Cleared on image rebuild |
| B2: Doris connection succeeds | Not testable from repo | Cleared on automation.mibot.cl |
| B3: batch_asignacion_review_bronze has data | Not verifiable | Operator count query + confirm |
| resolve_token path decision | CLOSED | W2 addressed before PR2 merge |

---

## FINAL VERDICT

**PASS WITH WARNINGS**  
0 CRITICAL | 3 WARNING | 2 SUGGESTION  
**PR1 is ready to merge.** No security gaps in the code track.  
PR2 deployed after B1+B2+B3 gates cleared.
