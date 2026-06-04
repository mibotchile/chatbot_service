# Deploy-Readiness Report — Frontend Platform
## Combined stack: per-tenant-landings + widget-secure-distribution (all PRs)
**Branch**: feat/widget-secure-pr3 (tip commit b26e3b8)
**Stacked on**: feat/widget-secure-pr2 → feat/widget-secure-pr1 → feat/per-tenant-landings-pr2 → feat/per-tenant-landings-pr1
**Date**: 2026-06-04
**Verdict**: GO-WITH-DEPLOY-STEPS

---

## Test Evidence

| Command | Result |
|---------|--------|
| `uv run pytest tests/ -q` | **497 passed, 0 failed, 0 errors — 7.05s** |

Expected count per apply-progress: 497. Actual: 497. EXACT MATCH.

---

## 1. PK Loop Correctness

### /branding returns publishable_key
- `apps/agent/api/routers/cobranza.py:187` — `"publishable_key": _resolve_current_pk(cfg)` in branding response.
- `_resolve_current_pk` at line 167: reads `publishable_keys` list (current-status entry first), falls back to legacy scalar `publishable_key`.
- PASS.

### GET / injects window.__PK__ into BOTH index.html files
- `frontend/index.html:191` — `<script>window.__PK__ = "__PK__";</script>` sentinel present.
- `frontend/tenants/prestamype/index.html:252` — same sentinel present.
- `apps/agent/api/main.py:312` — `_cached_html.replace(b'"__PK__"', b'"' + _default_pk.encode() + b'"')` replaces the byte sentinel at startup using the DEFAULT_TENANT's current key.
- PASS for both files.

### widget.js PK resolution chain
Priority order (widget.js:82–85):
1. `opts.pk` — from embed.js `data-pk` attribute
2. `scriptEl.dataset.pk` — direct script tag attribute
3. `window.__PK__` — server-injected (covers same-origin landing)
4. `branding.publishable_key` — async fallback after `/branding` fetch (covers third-party embeds for non-default tenants)

Sentinel guard: `if (PK === "__PK__") PK = null` — widget nulls out any unresolved sentinel (misconfigured deploy guard).

### All fetch calls in widget.js

| Route | Method | Sends X-Publishable-Key | Gated? | Notes |
|-------|--------|------------------------|--------|-------|
| `/api/v1/security/session-token` | GET | No | No (allow_no_key=True) | Bootstrap, expected |
| `/api/v1/security/csrf-token` | GET | No | No (allow_no_key=True) | Bootstrap, expected |
| `/api/v1/tenant/${TENANT}/branding` | GET | No | No (allow_no_key=True) | Bootstrap, expected |
| `/api/v1/chat` | POST | Yes (`...(PK ? {"X-Publishable-Key": PK} : {})`) | YES | Correct |
| `/api/v1/comprobante` | POST | Yes (`...(PK ? {"X-Publishable-Key": PK} : {})`) | YES | Correct |

PASS — every gated route called by widget.js sends the key. Every allow_no_key route omits it correctly.

---

## 2. Orphan Gated Routes (CRITICAL check — item 3)

Three gated routes not called by widget.js:
- `POST /api/v1/conversations/messages`
- `GET /api/v1/conversations/{conversation_id}/messages`
- `POST /api/v1/page-context`

### Search results across entire codebase (frontend/, apps/, all JS)

| Route | Callers found outside route definition |
|-------|----------------------------------------|
| `POST /conversations/messages` | **None** in frontend JS. Only in `middleware.py:33` (rate-limiter allowlist — server-side only, never makes HTTP calls). Route definition only. |
| `GET /conversations/{id}/messages` | **None** anywhere in frontend/ or non-test Python. Route definition only. |
| `POST /page-context` | **None** in frontend/ or app JS. One hit in `features/conversation/skills/navegacion-web/SKILL.md:36` — documentation string, not a caller. Route definition only. |

**Verdict**: These 3 routes are dormant/future-use. No caller exists that would hit them without X-Publishable-Key. Gating is harmless — no 403 risk from missing key. PASS.

---

## 3. Origin Allowlist vs Real Deploy (CRITICAL check — item 4)

### prestamype — `tenants/prestamype/tenant.config.json`
```json
"embed_origins": [
  "https://demos.mibot.cl",
  "http://localhost:*",
  "http://127.0.0.1:*"
]
```
- `https://demos.mibot.cl` is present. This is the REAL production origin. PASS.

### prestaunion — `tenants/prestaunion/tenant.config.json`
```json
"embed_origins": [
  "https://demos.mibot.cl",
  "http://localhost:*",
  "http://127.0.0.1:*"
]
```
- `https://demos.mibot.cl` is present. PASS.

Neither config is empty, neither has `"*"`. The CORS wildcard guard (collect_embed_origins) would have caught `"*"` anyway. PASS.

---

## 4. Placeholder Keys (Deploy-time action required)

### prestamype — `tenants/prestamype/tenant.config.json`
```json
"publishable_keys": [
  {"key": "pk_live_PLACEHOLDER_PRESTAMYPE", "status": "current", "added": "2026-01-01"}
]
```
PLACEHOLDER detected. Must be replaced at deploy.

### prestaunion — `tenants/prestaunion/tenant.config.json`
```json
"publishable_keys": [
  {"key": "pk_live_PLACEHOLDER_PRESTAUNION", "status": "current", "added": "2026-01-01"}
]
```
PLACEHOLDER detected. Must be replaced at deploy.

This is NOT a code bug — it is the correct pattern (no real secrets in git). These are deploy-time manual steps.

---

## 5. window.__PK__ + window.__TENANT__ Both Inject Correctly

- `frontend/index.html:186` — `window.__TENANT__ = "__TENANT__";` ✓
- `frontend/index.html:191` — `window.__PK__ = "__PK__";` ✓
- `frontend/tenants/prestamype/index.html:247` — `window.__TENANT__ = "__TENANT__";` ✓
- `frontend/tenants/prestamype/index.html:252` — `window.__PK__ = "__PK__";` ✓
- Server replacement: `main.py:292` replaces `"__TENANT__"`, `main.py:312` replaces `"__PK__"`.
- Sentinel guard in widget.js: `if (PK === "__PK__") PK = null` — catch-all if server forgot to replace.

Neither sentinel can reach the browser unresolved in a correctly configured deploy. PASS.

---

## Issues by Severity

### CRITICAL — 0

### WARNING — 1

**W1 — Placeholder keys must be replaced before first real widget session**
- `tenants/prestamype/tenant.config.json` and `tenants/prestaunion/tenant.config.json` contain `pk_live_PLACEHOLDER_*`.
- If deployed as-is, the widget will have a PK (no 403), but all tenants share easily-guessable placeholder values — any client knowing the pattern can forge keys.
- Required deploy step: generate real keys (`secrets.token_urlsafe(24)` → `pk_live_<value>`) and replace before routing live traffic.

### SUGGESTION — 1

**S1 — PK header is conditional (`PK ? ...header... : {}`)**
When PK is null (unresolved sentinel + failed branding fallback), the widget silently omits X-Publishable-Key. The gate will 403 the request. This is correct security behavior but the error surfacing to the user is generic ("network error"). Not a deploy blocker — but worth noting for future UX: detect PK=null before submitting and show a configuration-error state instead of a generic chat error.

---

## Deploy-Time Manual Steps (Required Before Going Live)

1. **Generate real publishable keys** for each tenant:
   ```python
   import secrets
   print("pk_live_" + secrets.token_urlsafe(24))
   ```
2. **Replace placeholder keys** in:
   - `tenants/prestamype/tenant.config.json` — `publishable_keys[0].key`
   - `tenants/prestaunion/tenant.config.json` — `publishable_keys[0].key`
3. **Update embed snippets** (if any are already deployed on external pages) with new `data-pk` values.
4. **Verify `DEFAULT_TENANT` env var** is set correctly in `docker-compose.yml` / remote compose on `automation.mibot.cl` (prestaunion for union landing, prestamype for prestamype landing).
5. **Remove `Traefik redirectRegex`** (from per-tenant-landings design) — server-side manual step documented in per-tenant-landings apply-progress.
6. **Build with real `WIDGET_VERSION`**: `docker build --build-arg WIDGET_VERSION=1.0.0 ...` — do NOT use the `dev` default in production (versioned URL caching requires a stable version string).

---

## Summary

| Check | Result |
|-------|--------|
| 497 tests passing | PASS |
| PK loop end-to-end | PASS |
| /branding returns publishable_key | PASS |
| window.__PK__ injected in both index.html files | PASS |
| X-Publishable-Key sent on /chat | PASS |
| X-Publishable-Key sent on /comprobante | PASS |
| Orphan gated routes (/conversations/messages, /conversations/{id}/messages, /page-context) — no unkeyed callers | PASS |
| embed_origins includes https://demos.mibot.cl for both tenants | PASS |
| No wildcard "*" in embed_origins | PASS |
| Placeholder keys detected (deploy step, not code bug) | WARNING |
| Sentinel unresolved guard in widget.js | PASS |

**CRITICAL: 0 | WARNING: 1 | SUGGESTION: 1**

**Verdict: GO-WITH-DEPLOY-STEPS** — code is production-ready; 6 manual deploy steps required, W1 (key replacement) is the most important.
