# Apply Progress: Per-Tenant Landings — PR1 + PR2

## Status
PR1 complete. Branch: `feat/per-tenant-landings-pr1`. Commit: 6a9934f. 457 passed.
PR2 complete. Branch: `feat/per-tenant-landings-pr2`. Commit: c3ba5de. 459 passed (+2 new).

## TDD Cycle Evidence

| Task | RED | GREEN | REFACTOR |
|------|-----|-------|----------|
| 1.1 test_get_root_injects_default_tenant_prestaunion | ✓ FAIL (422 — FastAPI treated `_req: Request` with underscore prefix as query param) | ✓ PASS | Fixed replace needle (`"__TENANT__"` not `__TENANT__`) + removed Request param |
| 1.2 test_get_root_prestamype_injects_tenant_no_query_param | ✓ FAIL (422) | ✓ PASS | Fixed sentinel assertion (`b'"__TENANT__"'` not `b"__TENANT__"`) |
| 1.3 test_get_root_falls_back_to_generic | ✓ FAIL (422) | ✓ PASS | middleware_stack reset + param fix |
| 1.4 test_widget_js_served_as_static | ✓ PASS (was already green) | ✓ PASS | n/a |
| 1.5 test_get_root_does_not_serve_unresolved_sentinel | ✓ FAIL (assertion wrong) | ✓ PASS | Fixed assertion to check `'"__TENANT__"'` |

### Root Causes Fixed During RED→GREEN

1. **FastAPI 0.136.3 underscore param bug**: `_req: Request` with underscore prefix is treated as a query param, not request injection. Fix: remove the unused Request parameter from `_serve_root()`.
2. **Replace needle too broad**: `replace(b"__TENANT__", ...)` replaced the JS property name `window.__TENANT__` too, producing `window.prestaunion = "prestaunion"`. Fix: `replace(b'"__TENANT__"', b'"' + tenant + b'"')` targets only the quoted sentinel value.
3. **Starlette middleware_stack cache**: After mutating `app.routes`, Starlette's compiled ASGI stack must be reset via `app.middleware_stack = None` so the new route is honoured by subsequent TestClient instances.
4. **Sentinel assertion over-broad**: `b"__TENANT__"` appears in `window.__TENANT__` (the JS property name, legitimate). Assertion corrected to `b'"__TENANT__"'` (the unresolved quoted value).

## Completed Tasks (PR1)

- [x] 1.1 tests/test_frontend_serving.py: test_get_root_injects_default_tenant (prestaunion)
- [x] 1.2 tests/test_frontend_serving.py: test_get_root_prestamype_injects_tenant (regression, no ?tenant=)
- [x] 1.3 tests/test_frontend_serving.py: test_get_root_falls_back_to_generic (unknown tenant)
- [x] 1.4 tests/test_frontend_serving.py: test_widget_js_served_as_static (200)
- [x] 1.5 (skipped — test_app_js_served_as_static deferred to PR2 when app.js is created)
- [x] 1.6 Confirmed RED state before implementation
- [x] 2.1 main.py: DEFAULT_TENANT = os.environ.get("DEFAULT_TENANT", "prestaunion")
- [x] 2.2 Startup byte-cache: select tenant file or generic, read + replace sentinel, store cached
- [x] 2.3 Register @app.get("/") BEFORE StaticFiles mount; return HTMLResponse(cached bytes)
- [x] 2.4 StaticFiles mount unchanged (widget.js/assets still served)
- [x] 3.1 infrastructure/docker-compose.yml: DEFAULT_TENANT: prestaunion added to pubot-demo
- [x] frontend/index.html: add window.__TENANT__ sentinel in <head> before THEME_HINTS
- [x] frontend/index.html:385: THEME_HINTS reads window.__TENANT__ first
- [x] frontend/index.html:494: TENANT reads window.__TENANT__ first
- [x] frontend/widget.js:70: reads window.__TENANT__ first

## Completed Tasks (PR2)

- [x] 3.2 Document remote manual step: DEFAULT_TENANT: prestamype on automation prestamype compose
- [x] 4.1 Create frontend/app.js (extracted shared inline script ~230 lines, DRY seam)
- [x] 4.2 frontend/index.html: replace inline script with <script src="app.js">
- [x] 4.3 frontend/index.html: remove prestamype CSS lines 165-354 (730→306 lines)
- [x] 4.4 Create frontend/tenants/prestamype/index.html (de-scoped CSS + <script src="app.js">)
- [x] 4.5 test_app_js_served_as_static (200) — GREEN
- [x] parity test: test_prestamype_parity_serves_per_tenant_file — GREEN
- [x] 5.1 Full test suite: 459 passed (457 + 2 new)

## Server-Only Manual Steps (NOT in this PR)

These require SSH to automation.mibot.cl and cannot be automated here:

1. **Remove Traefik redirectRegex middleware** on the prestamype router:
   ```
   ssh automation "grep -rniE 'tenant=|redirectregex|replacement' /home/onbot/automation/*/shared/traefik/dynamic/ 2>/dev/null"
   ```
   Locate the prestamype-*.yml router with `redirectRegex` middleware injecting `?tenant=prestamype`. Remove that middleware (keep stripPrefix + redirectScheme).

2. **Add DEFAULT_TENANT to remote prestamype compose**:
   Path likely: `/home/onbot/automation/prestaunion-demo/infrastructure-prestamype/docker-compose.yml`
   Add under environment: `DEFAULT_TENANT: prestamype`
   Then restart: `docker compose up -d`

## Files Changed (PR1)

| File | Action | Description |
|------|--------|-------------|
| `apps/agent/api/main.py` | Modified | GET / handler, DEFAULT_TENANT env, startup byte-cache, sentinel replace fix |
| `frontend/index.html` | Modified | window.__TENANT__ sentinel in head; THEME_HINTS + TENANT read window.__TENANT__ first |
| `frontend/widget.js` | Modified | TENANT reads window.__TENANT__ before ?tenant= |
| `infrastructure/docker-compose.yml` | Modified | DEFAULT_TENANT: prestaunion on pubot-demo |
| `tests/test_frontend_serving.py` | Created | 5 tests, TDD RED→GREEN cycle |

## Files Changed (PR2)

| File | Action | Description |
|------|--------|-------------|
| `frontend/app.js` | Created | Extracted shared branding JS (~230 lines, DRY seam) |
| `frontend/tenants/prestamype/index.html` | Created | Per-tenant file with de-scoped prestamype CSS + `<script src="app.js">` |
| `frontend/index.html` | Modified | Removed prestamype CSS block + inline script; 730→306 lines |
| `tests/test_frontend_serving.py` | Modified | +2 tests: app.js static + parity guard |

## Branches
- PR1: `feat/per-tenant-landings-pr1` (commit: 6a9934f) — 457 passed
- PR2: `feat/per-tenant-landings-pr2` (commit: c3ba5de, stacked on PR1) — 459 passed

## Remaining Manual Deploy Steps (server-only)
1. Remove Traefik redirectRegex middleware on prestamype router (see Server-Only Manual Steps above)
2. Add DEFAULT_TENANT: prestamype to remote prestamype compose, restart container
