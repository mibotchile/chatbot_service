# Design: Per-Tenant Landings (server-side tenant resolution)

## Technical Approach

Approach A (proposal-locked): an explicit `GET /` route, registered BEFORE the
`StaticFiles("/")` mount, resolves `DEFAULT_TENANT` (env), selects the per-tenant
or generic `index.html`, injects `window.__TENANT__` via one `str.replace`, and
returns `HTMLResponse`. Bytes are read and cached once at startup. StaticFiles
keeps serving `widget.js`, `app.js`, and assets. Tenant becomes a deployment fact.

## Resolved Constraint 1 — the `?tenant=` 307 redirect SOURCE

The live `curl -I .../pubot-c02e78e1` returns `location: .../pubot-c02e78e1/?tenant=prestamype`.

**Finding**: it is NOT in this repo. The repo's `infrastructure/traefik/pubot.yml`
is the **prestaunion** router (slug `pubot-gj5w2a0p`) and only has `stripPrefix` +
`redirectScheme` — no query injection. The live slug `pubot-c02e78e1` is the
**prestamype** router, whose dynamic config exists ONLY on the server (never
committed). The `?tenant=prestamype` is a Traefik `redirectRegex` middleware on
that prestamype router. The trailing-slash part (`/slug` → `/slug/`) is StaticFiles
`html=True` (a 307 with NO query) — but that cannot add `?tenant=`, so the query is
unambiguously the server-side Traefik middleware.

**Exact change (apply phase, on automation 172.16.250.42)** — locate then strip the
query injection, keep the trailing-slash redirect:
```
ssh automation "grep -rniE 'tenant=|redirectregex|replacement' \
  /home/onbot/automation/*/shared/traefik/dynamic/ \
  /home/onbot/automation/prestaunion-demo/ 2>/dev/null"
# Expected hit: a prestamype-*.yml router with a redirectRegex middleware whose
# replacement is '${1}/?tenant=prestamype'. Remove that middleware from the
# prestamype router (the strip + scheme redirect stay). After DEFAULT_TENANT is
# wired, the param is dead plumbing; dropping it removes the dangling redirect.
```
This is a design-time TODO only because this executor has no SSH/sandbox; the
source is identified with high confidence (Traefik redirectRegex on the server-only
prestamype router). Verification + removal happen in apply.

## Resolved Constraint 2 — the DRY seam: **Option (a), extract shared JS to `/app.js`**

Evidence: the entire main `<script>` (index.html ~485-700+) is tenant-NEUTRAL —
it fetches `/branding` and fills empty containers from data. Only ~190 CSS lines
(165-354) and 3 tiny `if (TENANT===...)` branches differ. So the shared LOGIC is
already DRY via branding-as-data; the ONLY duplication risk is copying ~700 lines
of `<script>` into the per-tenant file.

| Seam | Duplication | Cost | Verdict |
|------|-------------|------|---------|
| (a) extract inline `<script>` → shared `/app.js`; both HTML files `<script src="/app.js">` | None — JS lives once | one extraction + StaticFiles already serves it | **CHOSEN** |
| (b) server composes body-fragment into shared shell at serve time | None | new fragment format + 2nd replace + template assembly logic | rejected — more serving complexity for the same result |

(a) wins: it makes BOTH the generic and per-tenant `index.html` thin (head +
THEME_HINTS bootstrap + body layout + tenant CSS + `<script src="/app.js">`). The
3 prestamype JS branches stay inside `app.js` keyed on `window.__TENANT__` (data,
not duplication). Per-tenant file = layout markup + copy + tenant CSS ONLY.

## Architecture Decisions

### Decision: server-side `str.replace`, startup-cached
**Choice**: read chosen index at startup, `bytes.replace(b"__TENANT__", tenant)`, cache.
**Alternatives**: whoami endpoint (FOUC + round-trip); Docker build bake (breaks single image).
**Rationale**: zero build, one env var, StaticFiles unchanged for assets.

### Decision: extract inline JS to `/app.js` (DRY seam)
**Choice**: shared `frontend/app.js`; both HTML files reference it.
**Rationale**: shared logic already data-driven; extraction kills the only real duplication.

### Decision: per-tenant file only for structural divergence
**Choice**: only prestamype earns its own file; skin-only tenants use generic + `/branding`.

## Data Flow

    GET /  ──→ FastAPI explicit route (DEFAULT_TENANT)
                 │  pick tenants/<T>/index.html else generic/index.html
                 │  str.replace __TENANT__ → T  (cached bytes)
                 └─→ HTMLResponse  ──→ <head> sets window.__TENANT__
                                        └─→ /app.js reads window.__TENANT__
                                              └─→ fetch /api/v1/tenant/<T>/branding
    GET /widget.js, /app.js, assets ──→ StaticFiles (fall-through)

## `GET /` handler design

- `_DEFAULT_TENANT = os.environ.get("DEFAULT_TENANT", "prestaunion")` in `_mount_demo_frontend()`.
- File selection: `tenants/<T>/index.html` if exists, else `<frontend>/index.html`.
- Startup cache: read bytes once at mount time, do the `__TENANT__`→T replace once,
  store the resulting `bytes` in a module-level cache; handler returns the cached bytes.
- Mount ordering: register `@app.get("/", include_in_schema=False)` BEFORE
  `app.mount("/", StaticFiles(..., html=True))` — explicit route wins for exact `GET /`.
- Response: `HTMLResponse(content=cached_bytes, media_type="text/html; charset=utf-8")`.
- `GET /index.html` direct hit: served un-injected by StaticFiles — acceptable (prod hits only `/`).

## Frontend changes

- `<head>`: `<script>window.__TENANT__ = "__TENANT__";</script>` (server replaces inner token).
- `app.js` (extracted): `const TENANT = (window.__TENANT__ || qs.get("tenant") || "prestaunion")...`
  THEME_HINTS bootstrap (index.html:385) reads `window.__TENANT__` first too. The
  `|| "prestaunion"` stays ONLY as the last dev fallback (when no env + no query).
- `widget.js:70`: read `window.__TENANT__` first; keep `?tenant=` as last dev fallback.
  (Global is set in `<head>`; widget loads at end of `<body>` → ordering safe.)

## File Changes

| File | Action | Description |
|------|--------|-------------|
| `apps/agent/api/main.py:255-268` | Modify | DEFAULT_TENANT env, `GET /` route before mount, startup byte cache |
| `frontend/app.js` | Create | Extracted shared main `<script>` (the DRY seam) |
| `frontend/index.html` | Modify | Remove prestamype CSS (165-354) + 3 branches; inline JS → `<script src="/app.js">`; `window.__TENANT__` |
| `frontend/tenants/prestamype/index.html` | Create | prestamype layout + copy + CSS; `<script src="/app.js">`; no shared JS |
| `frontend/widget.js:70` | Modify | Read `window.__TENANT__`; keep `?tenant=` dev fallback |
| `infrastructure/docker-compose.yml` | Modify | `DEFAULT_TENANT: prestaunion` |
| prestamype compose (remote) | Modify | `DEFAULT_TENANT: prestamype` |
| `tests/test_frontend_serving.py` | Create | injection + fallback + static coverage |

## DEFAULT_TENANT env wiring

- Local `infrastructure/docker-compose.yml` `pubot-demo`: `DEFAULT_TENANT: prestaunion`.
- Remote prestamype compose (`/home/onbot/automation/prestaunion-demo/infrastructure-prestamype/`,
  verify path on server): `DEFAULT_TENANT: prestamype`.
- Dev-only `?tenant=` override kept as the LAST fallback in the resolution chain.

## Testing Strategy (strict TDD — write first)

| Test | Asserts |
|------|---------|
| `test_get_root_injects_default_tenant` | env=prestaunion → body has `window.__TENANT__ = "prestaunion"` |
| `test_get_root_prestamype_injects_tenant` | env=prestamype → body has `"prestamype"`; not from `?tenant=` |
| `test_get_root_falls_back_to_generic` | no per-tenant file → generic served |
| `test_widget_js_served_as_static` | `GET /widget.js` → 200 (StaticFiles intact) |
| `test_app_js_served_as_static` (seam) | `GET /app.js` → 200 (extracted shared JS reachable) |

## Migration / Rollout

Single PR. Compose env additions are additive. Rollback: unset `DEFAULT_TENANT`
(→ handler defaults to prestaunion) and revert the PR; serving falls back to
StaticFiles-only + client-side `?tenant=`. No data migration.

## Open Questions

- [ ] Remote prestamype compose exact path — verify on automation before edit (apply).
- [ ] Server-side prestamype Traefik `redirectRegex` middleware — locate + strip in apply (command above).
