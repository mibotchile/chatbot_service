# Spec: Per-Tenant Landings (server-side tenant resolution)

> Out of scope: widget.js security/build (parallel change: widget-secure-distribution).

---

## ADDED Requirements

### Requirement: Server-Side Tenant Resolution

The server MUST resolve the active tenant from the `DEFAULT_TENANT` environment variable at startup. The `GET /` handler MUST select `frontend/tenants/<DEFAULT_TENANT>/index.html` if that file exists, otherwise fall back to `frontend/index.html`. The resolved HTML MUST have the placeholder `__TENANT__` replaced with the tenant value and be returned as `text/html;charset=utf-8`. The resolved bytes MUST be cached at startup (read+replace once, not per-request).

#### Scenario: Tenant file exists

- GIVEN `DEFAULT_TENANT=prestamype` and `frontend/tenants/prestamype/index.html` exists
- WHEN `GET /` is requested (no query params)
- THEN response is 200 with `window.__TENANT__ = "prestamype"` injected in the HTML body

#### Scenario: Tenant file absent — generic fallback

- GIVEN `DEFAULT_TENANT=newclient` and no `frontend/tenants/newclient/index.html`
- WHEN `GET /` is requested
- THEN response is 200 using `frontend/index.html` with `window.__TENANT__ = "newclient"` injected

#### Scenario: Env unset — default to prestaunion

- GIVEN `DEFAULT_TENANT` is not set
- WHEN `GET /` is requested
- THEN response is 200 with `window.__TENANT__ = "prestaunion"` injected

---

### Requirement: Latent Wrong-Tenant Bug Regression

Hitting a tenant's container without any query parameter MUST serve that tenant. The prestamype container MUST NOT serve prestaunion content when `?tenant=` is absent.

#### Scenario: prestamype container, no query param (regression guard)

- GIVEN the server is running with `DEFAULT_TENANT=prestamype`
- WHEN `GET /` is requested with no `?tenant=` parameter
- THEN `window.__TENANT__` in the response equals `"prestamype"` — NOT `"prestaunion"`

---

### Requirement: Shared JS Extracted to app.js

A file `frontend/app.js` MUST exist containing the extracted shared inline JavaScript. Both `frontend/index.html` and `frontend/tenants/prestamype/index.html` MUST reference it via `<script src="/app.js">`. The file MUST be served as a static asset (200).

#### Scenario: app.js served as static

- GIVEN the server is running
- WHEN `GET /app.js` is requested
- THEN response is 200

---

### Requirement: Per-Tenant index.html for prestamype

`frontend/tenants/prestamype/index.html` MUST exist and MUST contain only layout, copy, and prestamype-specific CSS. It MUST NOT duplicate shared JS logic (shared JS lives in app.js). It MUST include `<script>window.__TENANT__ = "__TENANT__";</script>` in `<head>` and reference `app.js`.

#### Scenario: prestamype index contains no duplicated shared JS

- GIVEN `frontend/tenants/prestamype/index.html` is read
- WHEN its `<script>` blocks are inspected
- THEN no inline JS beyond the `window.__TENANT__` sentinel and `<script src="/app.js">` is present

---

## MODIFIED Requirements

### Requirement: Tenant Reading in app.js and widget.js

`app.js` MUST read tenant as: `window.__TENANT__` first, then `?tenant=` query param as dev-only fallback, then `'prestaunion'` as last-resort dev fallback. The hardcoded `|| "prestaunion"` default MUST NOT be the sole resolution path in production — `window.__TENANT__` is the production source.

`widget.js:70` MUST read `window.__TENANT__` first, then `?tenant=` as dev fallback. The bare `|| "prestaunion"` default at line 70 MUST be removed or subordinated to server-injected value.

(Previously: `index.html:494` and `widget.js:70` resolved tenant via `?tenant=` with hardcoded `|| "prestaunion"` fallback — no server injection.)

#### Scenario: Server-injected tenant used in app.js

- GIVEN `window.__TENANT__ = "prestamype"` is set in `<head>` before app.js loads
- WHEN `app.js` evaluates tenant resolution
- THEN `TENANT === "prestamype"` without any query param

#### Scenario: Dev override via query param

- GIVEN `window.__TENANT__` is absent (raw file served outside app container)
- WHEN `?tenant=prestamype` is present in the URL
- THEN `TENANT === "prestamype"` (dev fallback works)

---

### Requirement: generic index.html Prestamype CSS and Branches Removed

`frontend/index.html` MUST NOT contain prestamype-specific CSS (lines 165-354 in pre-change state) nor `if(TENANT==='prestamype')` branches. Those belong exclusively in `frontend/tenants/prestamype/index.html` and `app.js`.

(Previously: shared `index.html` contained ~190 lines of prestamype CSS and 3 tenant-gated JS branches.)

#### Scenario: Generic index.html is tenant-neutral

- GIVEN `frontend/index.html` is served with `DEFAULT_TENANT=prestaunion`
- WHEN the page renders
- THEN no prestamype-specific CSS classes or styles are applied

---

## ADDED Requirements (Infrastructure)

### Requirement: DEFAULT_TENANT env in compose files

`infrastructure/docker-compose.yml` MUST declare `DEFAULT_TENANT: prestaunion`. The prestamype compose file MUST declare `DEFAULT_TENANT: prestamype`.

#### Scenario: prestaunion compose sets env

- GIVEN `docker-compose.yml` is read
- WHEN the environment section for the app service is inspected
- THEN `DEFAULT_TENANT=prestaunion` is present

#### Scenario: prestamype compose sets env

- GIVEN the prestamype compose file is read
- WHEN the environment section is inspected
- THEN `DEFAULT_TENANT=prestamype` is present

---

### Requirement: Static Assets Still Served

`widget.js`, `embed.js`, `app.js`, and `favicon` MUST continue to be served as static assets with 200 responses. The explicit `GET /` route MUST NOT interfere with static file serving for any other path.

#### Scenario: widget.js static serving unaffected

- GIVEN the server is running
- WHEN `GET /widget.js` is requested
- THEN response is 200

#### Scenario: GET /index.html direct hit (acceptable un-injected)

- GIVEN the server is running
- WHEN `GET /index.html` is requested directly
- THEN StaticFiles serves it (un-injected); this is accepted behavior for non-production paths

---

### Requirement: prestaunion Behavioral Parity

All existing behavior visible to a prestaunion user MUST be unchanged after this change. Anti-flash gate, THEME_HINTS bootstrap, branding fetch, and widget behavior for prestaunion MUST function identically to pre-change state.

#### Scenario: prestaunion parity — tenant injected correctly

- GIVEN `DEFAULT_TENANT=prestaunion` (default)
- WHEN `GET /` is requested
- THEN `window.__TENANT__ = "prestaunion"` is in the response and no prestamype CSS is present

---

## Test Coverage Contract

`tests/test_frontend_serving.py` MUST include:

| Test | Covers |
|------|--------|
| `test_get_root_injects_default_tenant` | prestaunion default injection |
| `test_get_root_prestamype_injects_tenant` | prestamype injection without ?tenant= |
| `test_get_root_falls_back_to_generic` | unknown tenant → generic index.html |
| `test_widget_js_served_as_static` | static serving unaffected |
| `test_app_js_served_as_static` | DRY seam asset served |
