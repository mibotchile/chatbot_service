# Verification Report: Widget Secure Distribution — FULL (PR1 + PR2 + PR3)

**Change**: widget-secure-distribution
**Scope**: PR1 (Slice A + B) + PR2 (Slice C) + PR3 (Slice D — deploy-blocker)
**Branch**: feat/widget-secure-pr3 (ecf8b3b, 9a32f5e) stacked on feat/widget-secure-pr2 → feat/widget-secure-pr1 → feat/per-tenant-landings-pr2
**Date**: 2026-06-04
**Verdict**: PASS (clean — 0 CRITICAL, 0 WARNING)

---

## Build / Test Evidence

| Check | Result |
|---|---|
| `uv run pytest tests/ -q` | **504 passed in 7.8s** — 0 failures, 0 errors (post-cleanup) |
| New tests PR1 (Slice A+B) | 31 (14 unit widget_gate + 14 integration widget_gate + 3 CORS wildcard) |
| New tests PR2 (Slice C) | 3 (2 new versioned + 1 updated) |
| New tests PR3 (Slice D) | 7 (test_pk_loop.py) |
| Per-tenant-landings regression | PASS |
| PR1 base regression | PASS |
| Cleanup (dead alias removal) | 504 passing |

---

## Task Completeness

| Task | Status |
|---|---|
| A-1 … A-8 (gate RED+GREEN) | DONE |
| A-9 pk map cache | OPTIONAL — deferred (acceptable) |
| B-1, B-2 (CORS wildcard) | DONE |
| C-1 through C-5 (distribution build) | DONE |
| D-1 through D-7 (deploy-blocker) | DONE |

---

## Spec Compliance Matrix

### widget-key-gate

| Scenario | Test | Status |
|---|---|---|
| Valid key + allowlisted origin → 200 | test_widget_gate_integration.py | PASS |
| Missing key → 403 | test_widget_gate.py + integration | PASS |
| Unrecognized key → 403 | test_widget_gate.py | PASS |
| Valid key + non-allowlisted origin → 403 | test_widget_gate_integration.py | PASS |
| allow_no_key route passes without key | test_widget_gate_integration.py | PASS |
| CSRF composes independently (key-valid but CSRF-missing → 403) | test_widget_gate_integration.py | PASS |
| Dual-key grace window | test_widget_gate.py | PASS |
| Legacy scalar publishable_key backward-compat | test_widget_gate.py | PASS |

### widget-distribution

| Scenario | Test | Status |
|---|---|---|
| GET /widget/{version}/widget.min.js → 200 + immutable Cache-Control | test_versioned_widget_served | PASS |
| Unknown version → 404 | test_unknown_widget_version_returns_404 | PASS |
| GET /widget.js → 302 redirect to versioned path | test_widget_js_redirects_to_versioned_url | PASS |

### embed-cors (CORS wildcard guard)

| Scenario | Test | Status |
|---|---|---|
| Wildcard "*" rejected at config load | test_cors_wildcard_guard.py | PASS |
| Legitimate origins unaffected | test_cors_wildcard_guard.py | PASS |

### Client→Server PK Loop

| Scenario | Test | Status |
|---|---|---|
| /branding returns publishable_key (variants) | test_pk_loop.py | PASS |
| window.__PK__ sentinel injected in index.html | test_pk_loop.py | PASS |
| widget.js sends X-Publishable-Key on /chat | test_pk_loop.py | PASS |
| widget.js sends X-Publishable-Key on /comprobante | test_pk_loop.py | PASS |

---

## Critical Items Inspection

### 1. Route Ordering (CRITICAL — PASS)
`@app.get('/widget/{version}/widget.min.js')` and `@app.get('/widget.js')` registered BEFORE `app.mount("/", StaticFiles...)`. No silent catch-all override risk.

### 2. Test Stub Cannot Ship to Prod (CRITICAL — PASS)
`frontend/widget.min.js` marked as test stub in file header. Dockerfile `COPY --from=widgetbuild` unconditionally overwrites in Docker build. Stub contains no sensitive data.

### 3. esbuild Stage (PASS)
`--keep-names` preserves PubotWidget. `--minify` compresses. `--sourcemap` for debugging. WIDGET_VERSION injected via ARG.

### 4. Legacy Compat (PASS)
`/widget.js` → 302 → `/widget/{WIDGET_VERSION}/widget.min.js`. embed.js loads `/widget.js`. Chain verified. Test checks Location regex.

### 5. Obfuscation Honesty (PASS)
Minification = size reduction + mild deterrence ONLY, NOT security. Documented in code + spec.

### 6. Secret Hygiene (PASS)
Placeholder keys pk_live_PLACEHOLDER_* only. No real secrets in diff.

### 7. No Regression in Per-Tenant-Landings (PASS)
459 tests from landings still pass. Coordinated schema edits (publishable_keys appended at end-of-object).

---

## Final Verdict

- CRITICAL: **0**
- WARNING: **0**
- SUGGESTION: **0**

**PASS** — safe to archive and deploy. 504 tests green. Spec compliance 100%. Code merged to main. Production verified (prestamype-demo live).
