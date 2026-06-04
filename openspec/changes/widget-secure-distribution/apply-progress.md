# Apply Progress: widget-secure-distribution

**Branch**: feat/widget-secure-pr3 (stacked on feat/widget-secure-pr2 → feat/widget-secure-pr1 → feat/per-tenant-landings-pr2)
**PR1 commit**: 4dbf9a6 | **PR2 commits**: ecc5108, 230fbb7, 609b2f7 | **PR3 commits**: ecf8b3b, 9a32f5e
**Date**: 2026-06-04
**Tests**: 497 passed (38 new total: 31 PR1/PR2 + 7 PR3)
**Status**: ALL SLICES DONE — deploy-blocker closed (PR3)

## PR1 — DONE

### Slice A — Publishable Key Gate
- [x] A-1 RED: tests/test_widget_gate.py — unit tests for require_publishable_key
- [x] A-2 RED: same file — resolve_tenant_by_pk tests (single, dual, legacy scalar, unknown)
- [x] A-3 GREEN: apps/agent/api/deps/widget_gate.py — resolve_tenant_by_pk
- [x] A-4 GREEN: same file — require_publishable_key factory
- [x] A-5 RED: tests/test_widget_gate_integration.py — integration tests (5 gated + allow_no_key routes)
- [x] A-6 GREEN: wired Depends onto gated routes in conversations.py + cobranza.py
- [x] A-7 GREEN: X-Publishable-Key added to CORS allow_headers in main.py
- [x] A-8 GREEN: tenant configs updated (prestamype, prestaunion, _template)
- [ ] A-9 OPTIONAL: cache pk map (skipped — scan is fast for 2 tenants)

### Slice B — CORS "*" Guard
- [x] B-1 RED: tests/test_cors_wildcard_guard.py
- [x] B-2 GREEN: apps/agent/shared/config/cors.py — drop "*" + logger.warning

## PR2 — DONE (Slice C)

**Branch**: feat/widget-secure-pr2 (stacked on feat/widget-secure-pr1)
**Commits**: ecc5108, 230fbb7, 609b2f7
**Tests**: 490 passed (31 new total, +2 in PR2)

- [x] C-1 RED: tests/test_embed_widget.py — test_widget_js_redirects_to_versioned_url (302 + Location regex); test_versioned_widget_served (200, immutable, body check); test_unknown_widget_version_returns_404
- [x] C-2 GREEN: infrastructure/docker/Dockerfile.agent — node:20-slim AS widgetbuild; esbuild@0.21 --minify --keep-names --sourcemap; COPY --from=widgetbuild into Python stage; ARG/ENV WIDGET_VERSION=dev
- [x] C-3 GREEN: apps/agent/api/main.py — @app.get('/widget/{version}/widget.min.js') inside _mount_demo_frontend, before StaticFiles mount; FileResponse + Cache-Control: public, max-age=31536000, immutable; 404 on version mismatch or missing file
- [x] C-4 GREEN: apps/agent/api/main.py — @app.get('/widget.js') → 302 RedirectResponse, Cache-Control: no-cache; embed.js documented with 302 strategy + security note
- [x] C-5 SMOKE: 490 tests passing

### Files changed in PR2
| File | Change |
|------|--------|
| `tests/test_embed_widget.py` | Updated test_widget_js (302 assert) + 3 new tests |
| `frontend/widget.min.js` | NEW — test stub (PubotWidget + attachShadow, ~10 lines) |
| `apps/agent/api/main.py` | Versioned route + 302 alias inside _mount_demo_frontend |
| `frontend/embed.js` | Doc: widget URL strategy + security note |
| `infrastructure/docker/Dockerfile.agent` | esbuild multi-stage |

### Key decisions (PR2)
- **widget.min.js stub**: committed minimal file for hermetic local tests. Docker widgetbuild stage overwrites with real esbuild output at build time.
- **embed.js 302 strategy**: embed.js loads `api + "/widget.js"` → server 302s to versioned immutable URL. No build-time sed needed. Redirect is no-cache; asset is permanently cached.
- **esbuild --keep-names**: preserves PubotWidget global so public API survives minification.
- **WIDGET_VERSION=dev default**: local dev + CI tests work without Node/Docker.
- **Obfuscation**: minification = size + mild deterrence only, NOT a security control. Documented in stub + Dockerfile.

## PR3 — DONE (deploy-blocker: client→server key loop)

### Slice D tasks
- [x] D-1 RED: tests/test_pk_loop.py — 7 tests (branding pk variants + sentinel replacement)
- [x] D-2 GREEN: apps/agent/api/routers/cobranza.py — /branding returns publishable_key
- [x] D-3 GREEN: apps/agent/api/main.py — _mount_demo_frontend injects __PK__ sentinel
- [x] D-4 GREEN: frontend/index.html + frontend/tenants/prestamype/index.html — __PK__ sentinel added
- [x] D-5 GREEN: frontend/widget.js — PK resolution chain + X-Publishable-Key on /chat + /comprobante
- [x] D-6 GREEN: frontend/embed.js — data-pk documented + passed to mount()
- [x] D-7 SMOKE: 497 tests passing

### Files changed (PR3)
| File | Change |
|---|---|
| `tests/test_pk_loop.py` | NEW — 7 RED→GREEN tests |
| `apps/agent/api/routers/cobranza.py` | publishable_key added to /branding response |
| `apps/agent/api/main.py` | __PK__ sentinel replacement in _mount_demo_frontend |
| `frontend/index.html` | __PK__ sentinel script tag added |
| `frontend/tenants/prestamype/index.html` | __PK__ sentinel script tag added |
| `frontend/widget.js` | PK resolution + X-Publishable-Key on gated fetches |
| `frontend/embed.js` | data-pk attr documented, passed to mount() |

### Key flow (end-to-end)
1. Same-origin landing: server injects `window.__PK__ = "pk_live_..."` via sentinel
2. Third-party embed: site owner sets `data-pk="pk_live_..."` on the `<script>` tag
3. Non-default tenant (no window.__PK__): widget fetches /branding, reads `branding.publishable_key`
4. widget.js sends `X-Publishable-Key: <pk>` on every gated route call
5. Server gate validates key → resolves tenant → checks origin → passes or 403

### Key decisions (PR3)
- **Sentinel pattern**: b'"__PK__"' → same replace-bytes approach as __TENANT__; variable name `window.__PK__` stays, only the value is replaced
- **_resolve_current_pk**: inline in both cobranza.py and main.py (two separate callsites); no shared helper to avoid premature abstraction across module boundary
- **loadBranding skips prestaunion**: TENANT === "prestaunion" bails early — same-origin landing uses window.__PK__ (always available); no branding fetch needed
- **Sentinel guard**: PK === "__PK__" → null; protects against misconfigured deploy serving raw template
- **Gated fetches in widget.js**: only /chat and /comprobante are called directly; /conversations/messages, /page-context are not called from widget.js (server-side only)

## Notes
- test_comprobante_requires_session_and_csrf: updated assertion to `in (401, 403)` —
  gate now runs first (FastAPI Depends), returns 403 for missing key before session check.
  Auth invariant still holds: unauthenticated requests are rejected.
- Placeholder keys in tenant configs: pk_live_PLACEHOLDER_<TENANT> — replace with
  real secrets before production deployment.
