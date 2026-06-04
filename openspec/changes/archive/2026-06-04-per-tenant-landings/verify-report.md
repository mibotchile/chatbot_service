# Verify Report: Per-Tenant Landings — PR1 + PR2 (Full Change Verdict)

**Date**: 2026-06-04  
**Branch**: feat/per-tenant-landings-pr2 (commit c3ba5de)  
**Base**: feat/prestamype-landing-redesign  
**Verdict**: PASS WITH WARNINGS (0 CRITICAL, 2 WARNING, 1 SUGGESTION)

---

## Test Results

**459 passed, 0 failed, 1.81s** (457 PR1 baseline + 2 new PR2 tests)

---

## Spec Compliance Matrix (Full Change)

| Requirement | Status | Evidence |
|-------------|--------|----------|
| Server-side DEFAULT_TENANT resolution | PASS | main.py:276–303 GET / handler, env read, per-tenant file select, byte cache |
| Latent wrong-tenant bug regression | PASS | test_get_root_prestamype_injects_tenant_no_query_param passes |
| Shared JS extracted to app.js | PASS | frontend/app.js exists (230 lines); both index files reference it |
| app.js served as static 200 | PASS | test_app_js_served_as_static passes |
| Per-tenant index.html for prestamype | PASS | frontend/tenants/prestamype/index.html has no inline shared logic |
| DEFAULT_TENANT in docker-compose.yml | PASS | infrastructure/docker-compose.yml:12 |
| Static assets still served | PASS | widget.js, app.js tests pass; StaticFiles unchanged |
| prestaunion behavioral parity | PASS | generic index.html intact; THEME_HINTS, anti-flash, branding unchanged |
| Tenant reading chain | PASS | app.js:4, widget.js:70 — window.__TENANT__ first, ?tenant= fallback, 'prestaunion' last |
| prestamype CSS removed from generic | PASS | generic index.html: 306 lines (was 730); zero prestamype CSS rules |
| if(TENANT==='prestamype') branches removed | PASS | generic index.html: zero if-prestamype branches |

---

## Diff Stat (Full Change vs Base)

```
7 files changed, +838 / -434 lines
- apps/agent/api/main.py         +54 / -0
- frontend/app.js                +230 (new)
- frontend/index.html            -424 net (730→306 lines)
- frontend/tenants/prestamype/index.html  +350 (new)
- frontend/widget.js             +1 / -1
- infrastructure/docker-compose.yml  +4
- tests/test_frontend_serving.py +198 (new)
```

---

## Issues

### WARNING W1 — Asset path style mismatch (no operational risk)

**Spec says**: `<script src="/app.js">` (absolute)  
**Actual**: `<script src="app.js">` (relative) in both HTML files

**Assessment**: Functionally equivalent. Both HTML files are served at URL `/` by the GET / handler. Browser resolves relative `app.js` → `/app.js`. StaticFiles mount at `"/"` serves `frontend/app.js` at `/app.js`. No broken assets in production. Same pattern used for `widget.js` throughout codebase.

**Verdict**: Update spec text to match actual. No operational fix needed.

---

### WARNING W2 — widget.js TENANT chain order (pre-existing, accepted)

**Actual order**: `opts.tenant || scriptEl.dataset.tenant || window.__TENANT__ || qs.get("tenant") || "prestaunion"`

`window.__TENANT__` is third in chain, not first per design intent. However, in landing page deployment (no embed options set), opts.tenant and data-tenant are both absent, so window.__TENANT__ wins in practice.

**Verdict**: Accepted in PR1 verify. Intentional for embed use case (programmatic embed options take priority). Unchanged in PR2.

---

### SUGGESTION S1 — Remote prestamype compose path unconfirmed

Manual deploy step (add `DEFAULT_TENANT=prestamype` to remote compose on automation.mibot.cl) documented in apply-progress but path not verified without SSH access.

**Verdict**: Verify path before cutover. Expected location: `/home/onbot/automation/prestaunion-demo/infrastructure-prestamype/docker-compose.yml`

---

## Task Completion

| Phase | Tasks | Status |
|-------|-------|--------|
| Phase 1: Test infrastructure (RED→GREEN) | 6 | 6/6 complete |
| Phase 2: Backend GET / handler | 4 | 4/4 complete |
| Phase 3: Env wiring | 2 | 2/2 complete (1 remote manual) |
| Phase 4: Frontend extraction | 5 | 5/5 complete |
| Phase 5: Verification + deploy | 5 | 2/2 automated complete; 3 manual-deploy steps pending (server-only, expected) |

**All 18 automated tasks complete. 0 blockers.**

---

## PR1 Regression Check

PR1 baseline: 457 tests  
PR2 total: 459 tests  
Overlap: all 457 PR1 tests still pass ✓  
New: +2 (test_app_js_served_as_static, test_prestamype_parity_serves_per_tenant_file)

**Zero regressions.**

---

## Key Decisions Verified

| Decision | Implementation | Status |
|----------|----------------|--------|
| Approach A: server-side str.replace, startup-cached | main.py GET / handler, byte cache | VERIFIED |
| DRY seam: extract to /app.js | frontend/app.js (230 lines), both HTML files reference it | VERIFIED |
| Per-tenant file for prestamype only | frontend/tenants/prestamype/index.html exists; no duplication | VERIFIED |
| 3 prestamype branches in app.js keyed on window.__TENANT__ | app.js:21, 54, 64 | VERIFIED |
| Traefik redirectRegex removal (server-only) | Documented in apply-progress; pending manual execution | DOCUMENTED |

---

## Deployment Status

**Code READY for merge**. 2 server-only manual steps required (documented in apply-progress):
1. Remove Traefik redirectRegex middleware on prestamype router
2. Add DEFAULT_TENANT=prestamype to remote prestamype compose; restart

**Live verification**: Tracked separately (cobranza/per-tenant-landings deployment record shows DEPLOYED to prod 2026-06-04 with 504 tests green, 2 root_path bugs found and fixed).

---

## Archive Verdict

**Status**: PASS WITH WARNINGS  
**Blockers**: 0 CRITICAL  
**Risks**: 2 WARNING (both low operational impact), 1 SUGGESTION (path confirmation)  
**Recommendation**: SAFE TO ARCHIVE. Merge to main and execute server-only manual steps before production cutover.

---

Saved: 2026-06-04  
SDD Cycle: Proposal → Spec → Design → Tasks → Apply (PR1+PR2) → Verify → Archive ✓
