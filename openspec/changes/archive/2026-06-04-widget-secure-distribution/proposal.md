# Proposal: Widget Secure Distribution

## Intent

Enable secure third-party embedding of the chat widget on external tenant sites, plus simple CDN-like distribution — **without false security**. The widget must carry no real secret (the browser reads it, so can any attacker). We formalize the locked security model: a PUBLIC publishable key + server-side origin allowlist, building on the CSRF + proof-of-origin HMAC + rate limiter that already exist.

## Scope

### In Scope
- `publishable_key` (`pk_<random>`) field per tenant in `tenant.config.json` (+ `_template`).
- FastAPI dependency gating widget API routes on publishable key + `Origin`/`Referer` allowlist (`embed_origins`).
- esbuild multi-stage Docker step producing `widget.min.js` + sourcemap.
- Versioned static URL `/widget/<version>/widget.min.js` with `Cache-Control: immutable` + CORS for cross-origin.
- Reject `"*"` in `collect_embed_origins` (open-CORS guard).

### Out of Scope
- Parallel change `per-tenant-landings` (separate change).
- CSP changes — `connect-src 'self'` is NOT a blocker; CORS is the gate. Stated explicitly to kill the misconception.
- New secret handling — `csrf_secret` stays server-only; widget.js already has no hardcoded secrets.
- JS encryption (impossible as security). Obfuscation, if added, is defense-in-depth + size only.

## Capabilities

### New Capabilities
- `widget-key-gate`: publishable key + origin/referer allowlist enforcement on widget API routes.
- `widget-distribution`: versioned, immutable-cached, minified widget asset serving.

### Modified Capabilities
- `embed-cors`: extend `collect_embed_origins` to reject `"*"` (requirement-level guard).

## Approach

Stripe publishable-key pattern. **A1** (per-route FastAPI dependency, `allow_no_key=True` for same-origin demo) + **B1** (esbuild Docker multi-stage) + **C2** (versioned immutable path, VERSION injected into `embed.js` at build) + star-rejection guard. Existing CSRF/session HMAC unchanged.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `tenants/*/tenant.config.json` + `_template` | Modified | Add `publishable_key` |
| `apps/agent/api/main.py` | Modified | Register key-gate dependency + versioned static |
| `apps/agent/shared/config/cors.py` | Modified | Reject `"*"` |
| `frontend/widget.js`, `embed.js` | Modified | Send `X-Publishable-Key`; versioned URL |
| `infrastructure/docker/Dockerfile.agent` | Modified | esbuild stage |
| `tests/test_widget_key_gate.py` | New | Key+origin gate tests |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Key rotation breaks live embeds | Med | Grace period (old key valid N days) |
| Open-CORS if `"*"` not rejected | Med | Explicit guard + test |
| Versioned-path test breakage | High | Update `test_embed_widget.py` (expects `/widget.js`) |
| Merge conflict on `_template` with per-tenant-landings | Med | Coordinate `publishable_key` addition explicitly |

## Rollback Plan

Per-area: revert commit. Key gate uses `allow_no_key` passthrough — disabling enforcement restores prior behavior. Drop versioned route; alias `/widget.js` to legacy asset. No data migration.

## Dependencies

- Node.js in Docker build stage (esbuild). Multi-stage — no runtime impact.
- Coordination with parallel `per-tenant-landings` change on `tenant.config.json` schema.

## Success Criteria

- [ ] Widget API rejects requests with missing pk or non-allowlisted origin.
- [ ] Legit cross-origin embed works end-to-end.
- [ ] Minified, versioned widget served with immutable cache.
- [ ] `collect_embed_origins` rejects `"*"`.
- [ ] Existing 452-test suite green (with `test_embed_widget.py` updated for versioned path).
