# Design: Widget Secure Distribution

**Change**: widget-secure-distribution
**Date**: 2026-06-04
**Artifact store**: hybrid
**Approach (locked)**: publishable key + origin allowlist (A1) + esbuild Docker multi-stage (B1) + versioned immutable URL (C2) + `"*"` rejection guard.

See full design.md in the source change folder for detailed ADRs and implementation specifics.

---

## Summary of Locked Design Decisions

### ADR-1: Dual-Key Grace Window (CHOSEN)
`tenant.config.json` holds `publishable_keys: [{key, status: current|previous, added}]`. Rotation = prepend new key, keep old for 30 days, then delete. No version pinning. Pure config edit, hot-reloadable.

### ADR-2: Per-Route FastAPI Dependency Gate
`require_publishable_key(allow_no_key=False)` applied per-route. Gated: POST /chat, /comprobante, /conversations/messages, GET /conversations/{id}/messages, POST /page-context. allow_no_key=True: /branding, /csrf-token, /session-token, /certificate (bootstrap routes).

### ADR-3: Versioned URL + 302 Legacy Alias
Explicit FastAPI route `/widget/{version}/widget.min.js` before StaticFiles mount. WIDGET_VERSION from env (build-time ARG). Legacy `/widget.js` → 302 to versioned path.

## Gate Dependency (FastAPI)

`apps/agent/api/deps/widget_gate.py`:
- Reads `X-Publishable-Key` header
- None + allow_no_key → pass; None + !allow_no_key → 403
- resolve_tenant_by_pk None → 403
- Origin check via _origin_allowed (reuses cors.py pattern)
- Sets request.state.tenant_slug

## esbuild Docker Multi-Stage

`FROM node:20-slim AS widgetbuild`; `npx esbuild@0.21 --minify`; `COPY --from=widgetbuild` into Python stage. ARG/ENV WIDGET_VERSION injected into embed.js. Zero runtime cost.

## Wildcard Guard

In `collect_embed_origins`: drop exact string `"*"` + logger.warning. Port wildcard `http://localhost:*` unaffected.

## Coordination with per-tenant-landings

Both changes append to `tenant.config.json`. This change owns `publishable_keys` + `embed_origins`; per-tenant-landings owns presentation fields. Append at end-of-object only to avoid merge conflicts.

---

(Full design.md with detailed technical specifications, file-by-file changes, and rollback procedures is archived in the change folder.)
