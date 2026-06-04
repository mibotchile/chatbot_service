# Explore: per-tenant-landings

**Change:** per-tenant-landings
**Date:** 2026-06-04
**Persistence:** hybrid (this file + engram `sdd/per-tenant-landings/explore`, id 12423)

## Root problem

Tenant is resolved 100% client-side. The default is hardcoded `"prestaunion"` in **two** places:

- `frontend/index.html:494` — `const TENANT = (qs.get("tenant") || "prestaunion")`
- `frontend/widget.js:70` — same pattern, same default

Each chatbot already deploys as its own slug + dedicated container (Traefik file-based routing strips the slug prefix; the container sees clean `/`). So `?tenant=` is **vestigial plumbing**, and the `|| "prestaunion"` default is a **latent bug**: hitting the prestamype container without `?tenant=` serves prestaunion.

`prestamype` has diverged **structurally** (not just skin): ~190 lines of `body.tenant-prestamype` CSS (`index.html:165-354`) + three `if (TENANT === "prestamype")` JS branches (lines 511, 544, 554).

## Decision (locked)

Tenant = **server-side deployment fact**, not client-side. Container resolves its tenant via `DEFAULT_TENANT` env; server injects it and serves the per-tenant landing file with fallback to the generic one. `?tenant=` stays only as a dev/local override. No build step, no templating engine (overkill for 2-3 tenants). Only structurally-divergent tenants earn their own file. widget.js / embed.js / branding anti-flash gate stay shared.

## Current state (verified)

- Serving: `apps/agent/api/main.py:262` — blanket `app.mount("/", StaticFiles(directory=frontend, html=True))`.
- **No 307 redirect found in app code or Traefik configs checked** — yet the live `curl -I` returned `location: .../?tenant=prestamype`. ⚠️ OPEN: locate the real source of that redirect before killing `?tenant=` (likely a Traefik middleware on the server not in the inspected config). Must resolve in design.
- Dockerfile `infrastructure/docker/Dockerfile.agent:20`: `COPY frontend/ frontend/` → a new `frontend/tenants/prestamype/` dir ships automatically.
- Compose `infrastructure/docker-compose.yml`: `pubot-demo` has no `DEFAULT_TENANT` today.
- Branding endpoint takes tenant in PATH: `apps/agent/api/routers/cobranza.py:146` `GET /api/v1/tenant/{tenant_id}/branding` → JS must know its tenant to build the URL.

## Recommended approach — A: server-side str.replace at serve time

| Approach | Pros | Cons | Verdict |
|---|---|---|---|
| **A. `str.replace` injecting `window.__TENANT__` at `GET /`** | Zero build step; one env var; StaticFiles handles assets; bytes cached at startup | startup file read (trivial) | **RECOMMENDED** |
| B. `/api/v1/config` whoami endpoint | clean separation | extra round-trip; FOUC risk; more JS | No |
| C. bake at Docker build | zero runtime cost | rebuild per tenant; breaks single-image | No |

### Serving refactor (A)
Add an explicit `GET /` route BEFORE the StaticFiles mount (FastAPI resolves explicit routes before mounted apps). Resolve `DEFAULT_TENANT` → pick `frontend/tenants/<tenant>/index.html` if it exists else generic → `str.replace(b"__TENANT__", tenant)` → cache bytes at startup → `HTMLResponse`. Keep StaticFiles for `/widget.js`, `/embed.js`, favicon, assets.

### Frontend changes
- `<head>` (before THEME_HINTS): `<script>window.__TENANT__ = "__TENANT__";</script>`.
- `index.html:385` + `:494`: `const t = (window.__TENANT__ || qs.get("tenant") || "prestaunion")`.
- `widget.js:70`: read `window.__TENANT__` first (no file edit strictly needed — global set in `<head>`, widget loads at end of `<body>`).
- prestamype scoped CSS + JS branches move into `frontend/tenants/prestamype/index.html` as the default (no `body.tenant-*` scoping needed there); generic file cleaned.

## Affected files
| File | Change |
|---|---|
| `apps/agent/api/main.py:255-268` | explicit `GET /` injecting DEFAULT_TENANT; keep StaticFiles for assets |
| `frontend/index.html` | `window.__TENANT__` placeholder; read it; remove prestamype CSS + JS branches |
| `frontend/tenants/prestamype/index.html` | NEW — full prestamype variant |
| `frontend/widget.js:70` | read `window.__TENANT__` before `?tenant=` |
| `infrastructure/docker-compose.yml` | `DEFAULT_TENANT: prestaunion` for pubot-demo |
| remote `infrastructure-prestamype/docker-compose.yml` | `DEFAULT_TENANT: prestamype` (path on automation, unverified from dev) |
| `tests/test_frontend_serving.py` | NEW — strict-TDD, written first |

## Test gap (strict TDD)
Zero existing tests for frontend serving / tenant injection. Write first:
- `test_get_root_injects_default_tenant` (prestaunion)
- `test_get_root_prestamype_injects_tenant` (prestamype, per-tenant file present)
- `test_get_root_falls_back_to_generic` (no per-tenant file)
- `test_widget_js_served_as_static` (`GET /widget.js` → 200)

## Risks
1. **Sync burden**: `frontend/tenants/prestamype/index.html` duplicates the generic file's shared HTML/JS structure → coupling as the project evolves. Design must keep shared logic DRY (shared JS/branding stays in shared assets; per-tenant file holds layout + copy only).
2. The unresolved `?tenant=` 307 redirect source (see OPEN above).
3. Prestamype remote compose path not filesystem-verified from dev machine.
4. Direct hit to `/index.html` served by StaticFiles is un-replaced (no injection) — production never hits this URL directly; acceptable.

**Next:** sdd-propose
