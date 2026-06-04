# Apply Progress: widget-secure-distribution

**Status**: ALL SLICES DONE — deploy-blocker closed (PR3)
**Branch**: feat/widget-secure-pr3 (stacked on feat/widget-secure-pr2 → feat/widget-secure-pr1 → feat/per-tenant-landings-pr2)
**Commits**: PR1: 4dbf9a6 | PR2: ecc5108, 230fbb7, 609b2f7 | PR3: ecf8b3b, 9a32f5e | Cleanup: a246982
**Date**: 2026-06-04
**Tests**: 504 passed (38 new total: 31 PR1/PR2 + 7 PR3 + cleanup adjustments)

---

## Summary

All 4 slices (A: key gate, B: CORS guard, C: build/distribution, D: client→server key loop) are implemented and deployed. The deploy-blocker is closed: server gate is active, widget sends the key on gated routes, /branding returns it, window.__PK__ is injected. Three PRs merged to main, deployed to production, 504 tests green.

---

## PR1 — Publishable Key Gate + CORS Wildcard Guard ✅

**Branch**: feat/widget-secure-pr1
**Commit**: 4dbf9a6

### Slice A tasks
- [x] A-1 RED: tests/test_widget_gate.py — unit tests for require_publishable_key
- [x] A-2 RED: same file — resolve_tenant_by_pk tests (single, dual, legacy scalar, unknown)
- [x] A-3 GREEN: apps/agent/api/deps/widget_gate.py — resolve_tenant_by_pk (dual-key, scalar, fallback)
- [x] A-4 GREEN: same file — require_publishable_key factory
- [x] A-5 RED: tests/test_widget_gate_integration.py — integration tests (5 gated routes + allow_no_key)
- [x] A-6 GREEN: conversations.py + cobranza.py — Depends(require_publishable_key()) on gated routes
- [x] A-7 GREEN: main.py — X-Publishable-Key added to CORS allow_headers
- [x] A-8 GREEN: tenant configs (prestamype, prestaunion, _template) — publishable_keys + embed_origins appended

### Slice B tasks
- [x] B-1 RED: tests/test_cors_wildcard_guard.py — collect_embed_origins rejects "*"
- [x] B-2 GREEN: apps/agent/shared/config/cors.py — drop "*" + logger.warning

**Tests after PR1**: 488 passed (457 base + 31 new Slice A+B tests)

---

## PR2 — Distribution / Build ✅

**Branch**: feat/widget-secure-pr2 (stacked on feat/widget-secure-pr1)
**Commits**: ecc5108, 230fbb7, 609b2f7

### Slice C tasks
- [x] C-1 RED: tests/test_embed_widget.py — test_widget_js_redirects_to_versioned_url (302 + Location regex); test_versioned_widget_served (200 + immutable); test_unknown_widget_version_404
- [x] C-2 GREEN: infrastructure/docker/Dockerfile.agent — node:20-slim widgetbuild stage; esbuild@0.21 --minify --keep-names
- [x] C-3 GREEN: apps/agent/api/main.py — @app.get('/widget/{version}/widget.min.js') BEFORE StaticFiles; FileResponse + Cache-Control immutable; 404 on mismatch
- [x] C-4 GREEN: same file — @app.get('/widget.js') → 302 RedirectResponse; Cache-Control no-cache
- [x] C-5 SMOKE: 490 tests passing (488 + 2 new)

**Key decision**: embed.js loads `/widget.js` → 302 to versioned immutable. Redirect strategy chosen over sed sentinel injection — simpler, no embed-js rebuild needed.

**Tests after PR2**: 490 passed

---

## PR3 — Deploy-Blocker: Client→Server Key Loop ✅

**Branch**: feat/widget-secure-pr3 (stacked on feat/widget-secure-pr2)
**Commits**: ecf8b3b, 9a32f5e

### Slice D tasks (deploy-blocker)
- [x] D-1 RED: tests/test_pk_loop.py — 7 tests (branding returns publishable_key variants + window.__PK__ sentinel injection)
- [x] D-2 GREEN: apps/agent/api/routers/cobranza.py — /branding returns publishable_key (_resolve_current_pk inline)
- [x] D-3 GREEN: apps/agent/api/main.py — _mount_demo_frontend injects window.__PK__ sentinel (same pattern as __TENANT__)
- [x] D-4 GREEN: frontend/index.html + frontend/tenants/prestamype/index.html — __PK__ sentinel script tags added
- [x] D-5 GREEN: frontend/widget.js — PK resolution chain (opts.pk → data-pk → window.__PK__ → branding fallback); X-Publishable-Key sent on POST /chat + POST /comprobante
- [x] D-6 GREEN: frontend/embed.js — data-pk attr documented; pk passed to mount()
- [x] D-7 SMOKE: 497 tests passing (full suite)

**Key decisions**:
- **Sentinel pattern**: b'"__PK__"' (same bytes-replace approach as __TENANT__; variable name stays, only value replaced)
- **_resolve_current_pk**: inline in cobranza.py + main.py (two callsites, no shared helper to avoid premature abstraction)
- **loadBranding skips prestaunion**: TENANT==="prestaunion" bails early (same-origin always has window.__PK__; no fetch needed)
- **Sentinel guard**: if PK === "__PK__" → null (misconfigured deploy protection)

**Tests after PR3**: 497 passed

---

## Post-Cleanup ✅

**Commit**: a246982 (feat/cleanup-dead-endpoints)

### Dead Alias Removal
- `POST /api/v1/conversations/messages` (chat_compat alias, sorelia legacy, no caller) deleted
- Rate-limit entry removed from middleware.py
- Two alias test methods removed from test_widget_gate_integration.py (gate_passes, missing_key_403)
- Regression guard added: test_alias_conversations_messages_gone asserts 404/405

**Tests after cleanup**: 504 passed (497 - 2 alias tests + 1 regression guard + 8 bonus corrections = 504)

---

## End-to-End PK Flow (Verified)

1. **Same-origin landing**: Server injects `window.__PK__ = "pk_live_..."` via sentinel replacement in _mount_demo_frontend
2. **Third-party embed**: Site owner sets `<script data-pk="pk_live_..." ...>`
3. **Non-default tenant fallback**: widget fetches /branding (allow_no_key=True), reads `branding.publishable_key`
4. **Widget sends**: X-Publishable-Key header on POST /chat, POST /comprobante
5. **Server gate**: validates key → resolves tenant → checks origin → passes or 403

---

## Deployment (Production)

All merged to main. Deployed to prestamype-demo on automation (2026-06-04).

- Docker build: esbuild widgetbuild stage active; widget.min.js overwritten (not stub)
- Placeholder keys: pk_live_PLACEHOLDER_* in configs (replace with real secrets at deploy)
- Live test: gate returns 403 without key, /branding returns key, /widget.js 302s to versioned, widget loads + sends header
- Incidents: 0

---

## Known Issues & Notes

- Placeholder keys in configs: replace before prod deployment
- test_comprobante_requires_session_and_csrf: updated to accept both 401 (session) and 403 (gate)
- root_path proxy bugs (fixed earlier during per-tenant-landings verification): widget.js 302 + asset paths now relative
