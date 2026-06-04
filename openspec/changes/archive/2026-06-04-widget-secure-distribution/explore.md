# Exploration: widget-secure-distribution

**Change**: widget-secure-distribution
**Date**: 2026-06-04
**Persistence**: hybrid (this file + engram `sdd/widget-secure-distribution/explore`, id 12431)

---

## Current State

**widget.js** (1362 lines, hand-written, no build step):
- Zero hardcoded URLs or secrets. Config driven entirely by `data-*` attrs / mount opts.
- `_resolveConfig(opts)` at line 64: resolves API, TENANT, CT from opts/data attrs/query params.
- Only hardcoded strings: default tenant slug `"prestaunion"` (fallback, not a secret), Google Fonts URL, demo prefills (not secrets).
- API endpoints called: /security/session-token, /security/csrf-token, /chat, /comprobante, /tenant/{tenant}/branding.

**embed.js** (100 lines):
- Derives API base from `src` URL or `data-api` override.
- Loads `widget.js` from `api + "/widget.js"` — unversioned, no cache-bust.
- Passes `data-*` attrs to `PubotWidget.mount()`.

**Existing security layer (middleware.py)** — no changes needed:
- CSRF: HMAC(csrf_secret, timestamp), 1h expiry.
- Session token: HMAC(csrf_secret, visitor_id:timestamp), 1h expiry.
- Rate limiter: in-memory (Redis-ready). Covers /chat, /conversations/messages, /comprobante.

**CORS (cors.py)** — already correct:
- `build_cors_origin_regex()` = settings.cors_origins ∪ all tenant `embed_origins`.
- prestamype already has `embed_origins` set.
- `CORSMiddleware` uses `allow_origin_regex`, `allow_credentials=True`.

**Tenant config**: `tenants/<slug>/tenant.config.json` has `embed_origins` already. No `publishable_key` yet.

**Build/Dockerfile**: Pure Python/uv. No Node.js. `frontend/` copied as-is.

**Tests**: Gap — no tests for publishable key gate, versioned static serving.

---

## Security Model (Non-Negotiable)

**A secret CANNOT live in client-side JS.** Browser must parse+execute JS to run it — so can any attacker. Encrypting JS is impossible as security; obfuscation = defense-in-depth only.

Correct model (Stripe publishable key pattern):
1. **Publishable key** (`pk_<random>`) — PUBLIC, identifies tenant/site. NOT sensitive.
2. **Origin allowlist** — key only accepted from tenant's `embed_origins`. Server checks `Origin`/`Referer`.
3. **CSRF token** — already exists.
4. **Session token / proof-of-origin** — already exists.
5. **Rate limiting** — already exists.

---

## Approaches Evaluated

### Key Gate

| Approach | Pros | Cons |
|----------|------|------|
| **A1: FastAPI dependency** per-route | Surgical, testable | Must add to each route |
| A2: WidgetKeyMiddleware all /api/v1/* | Blanket, no forgotten routes | Runs on unrelated endpoints |

**Recommendation: A1** — surgical, isolated, testable. `allow_no_key=True` passthrough for demo.

### Build/Minify

| Approach | Pros | Cons |
|----------|------|------|
| **B1: esbuild Docker multi-stage** | Reproducible, no binaries in git, ~100ms | Node in build stage |
| B2: make widget pre-commit | Simpler | Humans forget; binaries in git |
| B3: + obfuscator | Slower reverse-engineering | NOT security |

**Recommendation: B1**. B3 optional (label as defense-in-depth only).

### Distribution

| Approach | Pros | Cons |
|----------|------|------|
| **C2: `/widget/<version>/widget.min.js`** | Immutable caching correct, rollback trivial | embed.js must know VERSION |
| C1: `?v=<hash>` query param | Zero path changes | Hash injected; old pinned embeds don't auto-update |
| C3: Version manifest endpoint | Operator controls rollout | Extra round-trip, complexity |

**Recommendation: C2** — versioned path, VERSION injected at build time.

---

## Combined Recommendation

**A1 + B1 + C2** + `embed_origins` star-rejection guard. Existing CSRF + session unchanged. CSP not a blocker.

---

## Risks & Mitigations

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Open CORS if embed_origins has "*" | Med | Explicit guard in collect_embed_origins + test |
| Key rotation breaks live embeds | Med | Grace period (old key valid 30 days) |
| test_embed_widget.py path breakage | High | Update for 302 redirect; add versioned tests |
| esbuild adds Node to build | Low | Multi-stage, ~100ms cost, zero runtime |
| Merge conflict with per-tenant-landings | Med | Coordinate schema; append at end-of-object |

---

## Ready for Proposal
Yes. All constraints understood. No blockers.
