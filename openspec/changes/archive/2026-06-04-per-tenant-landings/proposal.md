# Proposal: Per-Tenant Landings (server-side tenant resolution)

## Intent

Each tenant runs in a dedicated container, but tenant is still resolved 100% client-side via a vestigial `?tenant=` query param defaulting to `prestaunion`. Hitting the prestamype container without `?tenant=prestamype` silently falls back to prestaunion — a latent wrong-tenant bug. prestamype has also structurally diverged (~190 scoped CSS lines + 3 `if (TENANT === ...)` branches) forced into a shared monolith. Make tenant a server-side deployment fact via `DEFAULT_TENANT`, demote `?tenant=` to dev-only override, and split prestamype into its own file.

## Scope

### In Scope
- `GET /` route (registered BEFORE StaticFiles) that picks `frontend/tenants/<DEFAULT_TENANT>/index.html` (fallback generic), injects `window.__TENANT__` via `str.replace`, bytes cached at startup.
- Read `DEFAULT_TENANT` env in `_mount_demo_frontend()` (default `prestaunion`).
- Frontend reads `window.__TENANT__` first, then `?tenant=` dev override. Remove the `|| "prestaunion"` default in `index.html` (lines 385, 494) and `widget.js:70`.
- Extract `frontend/tenants/prestamype/index.html`; remove prestamype CSS (165-354) + branches (511, 544, 554) + THEME_HINTS entry from the generic file.
- Add `DEFAULT_TENANT` env to both compose files. New `tests/test_frontend_serving.py`.

### Out of Scope
- widget.js security/build hardening (parallel `widget-secure-distribution` change).
- Any change to prestaunion behavior — must stay byte-identical.
- Branding-as-data model (`/api/v1/tenant/{id}/branding`) stays unchanged.
- No build step, no templating engine.

## Capabilities

### New Capabilities
- None (refactor of serving + frontend; no new spec-level capability).

### Modified Capabilities
- None at spec level — behavior contract for prestaunion is preserved.

## Approach

Approach A (server-side string replace at serve time, startup-cached). FastAPI resolves the explicit `GET /` route before the `StaticFiles("/")` mount, so the route injects `window.__TENANT__` while StaticFiles continues serving `widget.js` and all assets. Tenant becomes a deployment fact set by env per container.

## Design Constraints (carry into design phase)

1. **Unlocated `?tenant=` redirect**: the live `?tenant=` 307 source is NOT in app code or inspected Traefik config. Design MUST locate and resolve it before removing the param, else a dangling redirect remains.
2. **DRY / no re-coupling**: the per-tenant file MUST NOT duplicate shared HTML/JS. Shared logic (widget bootstrap, branding fetch, anti-flash gate, THEME_HINTS) stays in shared assets; the per-tenant index holds layout + copy + tenant-specific CSS only. Design MUST specify the seam preventing re-coupling.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `apps/agent/api/main.py:255-268` | Modified | `_mount_demo_frontend()`: `DEFAULT_TENANT` env, `GET /` route, startup byte cache |
| `frontend/index.html` | Modified | Remove prestamype CSS/branches/THEME_HINTS; read `window.__TENANT__` first (385, 494) |
| `frontend/tenants/prestamype/index.html` | New | prestamype layout + copy + CSS as default path |
| `frontend/widget.js:70` | Modified | Read `window.__TENANT__`; drop `|| "prestaunion"` default |
| `infrastructure/docker-compose.yml` | Modified | Add `DEFAULT_TENANT: prestaunion` |
| prestamype compose (remote) | Modified | Add `DEFAULT_TENANT: prestamype` |
| `tests/test_frontend_serving.py` | New | Injection + fallback + static-asset coverage |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Remote prestamype compose path unverified | Med | Implementer verifies actual path on automation.mibot.cl before edit |
| `GET /index.html` direct hit served un-injected | Low | Acceptable — prod links only `/`; no fix needed |
| Unlocated `?tenant=` 307 redirect | Med | Design constraint #1 — locate/resolve before param removal |
| prestaunion regression | Med | Test parity; anti-flash gate logic unchanged |

## Rollback Plan

Single PR revert. Frontend/serving changes are isolated; reverting restores client-side `?tenant=` default. Compose env additions are additive — removing `DEFAULT_TENANT` reverts container to generic serving.

## Dependencies

- prestamype compose file must be reachable on automation.mibot.cl for the env addition.

## Success Criteria

- [ ] prestamype container serves prestamype with NO `?tenant=` in URL.
- [ ] Hitting prestamype container without query param no longer falls back to prestaunion.
- [ ] prestaunion behavior unchanged.
- [ ] Full suite green (currently 452) + new `tests/test_frontend_serving.py`.
