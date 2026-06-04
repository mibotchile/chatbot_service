# Tasks: Widget Secure Distribution

**Change**: widget-secure-distribution
**Date**: 2026-06-04
**Artifact store**: hybrid
**Delivery strategy**: ask-on-risk | **Chain strategy**: stacked-to-main
**TDD mode**: STRICT — order RED → GREEN → REFACTOR

---

## Review Workload Forecast

| Metric | Estimate |
|---|---|
| Estimated changed lines | ~450–550 |
| 400-line budget risk | **High** |
| Chained PRs recommended | **Yes** |
| Decision needed before apply | **Yes** |
| Suggested split | PR1 = Slice A + Slice B (key gate + CORS guard); PR2 = Slice C (build + distribution) |

**Note**: PR1 is pure Python/config — independently mergeable, zero frontend risk. PR2 is Dockerfile + route additions + test updates, depends on PR1 only for the versioned route registration pattern. PR3 closes the deploy-blocker: server gate is active but widget never sent the key and /branding never returned it.

---

## Coordination Note

`tenant.config.json` and `_template` edits in this change append `publishable_keys` and `embed_origins` at the **end of the config object** (after `agent`). The in-flight `per-tenant-landings` PR does NOT touch these fields — confirmed disjoint. Apply this change via targeted edit (not full-file rewrite) to stay merge-safe.

---

## Slice A — Publishable Key Gate

### A-1 [RED] Test: widget_gate dependency unit tests
- **File**: `tests/test_widget_gate.py` (new)
- **Scenarios** (all from spec):
  - Valid pk + allowlisted origin → passes (returns tenant slug in `request.state.tenant_slug`)
  - Missing pk, `allow_no_key=False` → raises 403
  - Unrecognized pk → raises 403
  - Valid pk, non-allowlisted origin → raises 403
  - Missing pk, `allow_no_key=True` → passes
  - Present-but-bad pk on `allow_no_key=True` route → still 403
- **Spec refs**: widget-key-gate scenarios 1–5
- **Parallel**: No — defines the contract all downstream tests rely on

### A-2 [RED] Test: resolve_tenant_by_pk unit tests
- **File**: `tests/test_widget_gate.py` (same file, separate describe block)
- **Scenarios**:
  - Single current key → returns slug
  - Dual keys (current + previous) → both return slug
  - Legacy scalar `publishable_key` field → treated as single current entry
  - Unknown key → returns None
- **Parallel**: Can run alongside A-1 tests (same file, write together)

### A-3 [GREEN] Implement: `resolve_tenant_by_pk` helper
- **File**: `apps/agent/api/deps/widget_gate.py` (new)
- Scans all tenant `config.json` files; builds `{pk: slug}` map
- Handles both `publishable_keys: [{key, status, added}]` list and legacy scalar `publishable_key`
- Key format accepted: any `pk_live_*` (no format enforcement at validation time)
- **Parallel**: No — A-1/A-2 must be written (RED) first

### A-4 [GREEN] Implement: `require_publishable_key` dependency factory
- **File**: `apps/agent/api/deps/widget_gate.py` (same file)
- Reads `X-Publishable-Key` header
- None + `allow_no_key=True` → pass
- None + `allow_no_key=False` → 403
- `resolve_tenant_by_pk` None → 403
- Origin from `Origin` header, fallback `Referer`; call `_origin_allowed(slug, origin)` reusing `cors.py`'s `_origin_pattern` (no drift)
- Sets `request.state.tenant_slug`
- **Parallel**: No — after A-3

### A-5 [RED] Test: integration tests for gated routes
- **File**: `tests/test_widget_gate_integration.py` (new)
- Routes under test: POST `/api/v1/chat`, POST `/api/v1/comprobante`, POST `/api/v1/conversations/messages`, GET `/api/v1/conversations/{id}/messages`, POST `/api/v1/page-context`
- Scenarios: missing pk → 403; bad pk → 403; bad origin → 403; allow_no_key routes return non-403 on missing pk
- Use test tenant config fixture with known pk + origin
- **Parallel**: Can be written alongside A-3/A-4 (tests run RED until wired)

### A-6 [GREEN] Wire `require_publishable_key` onto gated routes
- **Files**: route files for chat, comprobante, conversations, page-context (read before edit)
- Add `Depends(require_publishable_key())` to each gated route
- Add `Depends(require_publishable_key(allow_no_key=True))` to branding, csrf-token, session-token, certificate routes
- **Parallel**: No — after A-4 + A-5 written

### A-7 [GREEN] Add `X-Publishable-Key` to CORS `allow_headers`
- **File**: `apps/agent/main.py` (line ~239)
- Append `"X-Publishable-Key"` to the existing `allow_headers` list
- **Parallel**: Can be done alongside A-6 (different line, no conflict)

### A-8 [GREEN] Update tenant config schema: `publishable_keys` + `embed_origins`
- **Files**:
  - `apps/agent/tenants/_template/config.json` — append `publishable_keys: []` and `embed_origins: []` at end of object
  - Each active tenant's `config.json` — append `publishable_keys: [{key: "pk_live_<generate>", status: "current", added: "<date>"}]` if not present; `embed_origins` only if not already present (prestamype already has it)
- **Coordination**: Append at end-of-object only. Do NOT rewrite full file.
- **Parallel**: Can be done alongside A-6/A-7

### A-9 [REFACTOR] Extract `_build_pk_map` as cacheable helper (if load is measurable)
- Only if test run reveals config scan is slow; otherwise skip
- **Parallel**: After A-3 passes GREEN

---

## Slice B — CORS `"*"` Guard

### B-1 [RED] Test: `collect_embed_origins` rejects `"*"`
- **File**: `tests/test_cors.py` or existing cors test file (check first)
- Scenarios (from spec):
  - Config with `embed_origins: ["*"]` → `"*"` not in resulting regex, warning logged
  - Config with `["https://tenant.com", "http://localhost:*"]` → both present unchanged
- **Parallel**: Yes — independent of Slice A tests

### B-2 [GREEN] Implement `"*"` rejection guard in `collect_embed_origins`
- **File**: `apps/agent/api/cors.py` (line ~74)
- Drop exact string `"*"` from list before compiling; emit `logger.warning`
- Port wildcard `http://localhost:*` is unaffected (not exact `"*"`)
- **Parallel**: After B-1 written (RED)

---

## Slice C — Distribution / Build

*Depends on Slice A being merged (PR1) for the versioned route registration pattern, but esbuild Dockerfile work can be drafted in parallel.*

### C-1 [RED] Update `test_embed_widget.py`: assert 302 + versioned redirect
- **File**: `tests/test_embed_widget.py`
- Change `test_widget_js_is_served_and_exposes_mount`:
  - Assert response is 302
  - Assert `Location` header matches `^/widget/.+/widget\.min\.js$`
  - Assert body is empty under redirect
- Add `test_versioned_widget_served`:
  - GET `/widget/dev/widget.min.js` → 200, `Content-Type: application/javascript`, `Cache-Control: public,max-age=31536000,immutable`, body contains `PubotWidget`
- Add `test_unknown_widget_version_404`:
  - GET `/widget/9.9.9/widget.min.js` → 404
- `WIDGET_VERSION` defaults to `"dev"` in test env
- **Parallel**: Can be written independently; runs RED until C-3/C-4 implemented

### C-2 [GREEN] esbuild Docker multi-stage build
- **File**: `Dockerfile` (or `Dockerfile.widget` if separate — check first)
- Add `FROM node:20-slim AS widgetbuild` stage:
  - `ARG WIDGET_VERSION`
  - `npx esbuild@0.21 widget.js --minify --sourcemap --outfile=widget.min.js --legal-comments=none`
  - `sed` stamp `__WIDGET_VERSION__` sentinel into `embed.js`
- Python runtime stage: `ARG+ENV WIDGET_VERSION`, `COPY --from=widgetbuild` for `widget.min.js`, `.map`, `embed.js` into `frontend/`
- Node never present in runtime image
- **Parallel**: Can be drafted alongside C-1; does not require Slice A merge

### C-3 [GREEN] Implement versioned widget route
- **File**: `apps/agent/main.py`
- Register `@app.get('/widget/{version}/widget.min.js')` BEFORE the static mount (same rule as `GET /`)
- Returns `FileResponse(frontend/widget.min.js, media_type="application/javascript", headers={"Cache-Control": "public,max-age=31536000,immutable"})` if `version == WIDGET_VERSION`, else 404
- `WIDGET_VERSION` read from env (build-time `ARG→ENV`), defaults `"dev"`
- **Parallel**: No — after C-1 written (RED) and C-2 available

### C-4 [GREEN] Implement legacy `/widget.js` → 302 redirect
- **File**: `apps/agent/main.py` (same edit as C-3, adjacent lines)
- `@app.get('/widget.js')` → `RedirectResponse(f"/widget/{WIDGET_VERSION}/widget.min.js", status_code=302)`, response headers include `Cache-Control: no-cache`
- **Parallel**: Same edit pass as C-3

### C-5 [REFACTOR] Verify `PubotWidget` global + `attachShadow` survive minification
- Manual smoke test: load `widget.min.js` in browser or Node, assert `window.PubotWidget` exists
- Add assertion to `test_versioned_widget_served` that body contains `PubotWidget` (already specified in C-1)
- **Parallel**: After C-2 produces artifact

---

## Execution Order

```
A-1+A-2 (RED, parallel) → A-3 → A-4 → A-5 (RED) → A-6+A-7+A-8 (GREEN, parallel) → A-9 (optional)
B-1 (RED, parallel with A-1) → B-2 (GREEN)
[PR1 merge: A + B]
C-1 (RED) → C-2 (parallel) → C-3+C-4 (GREEN, same pass) → C-5 (REFACTOR)
[PR2 merge: C]
D-1 (RED) → D-2+D-3+D-4+D-5+D-6 (GREEN) → D-7 (SMOKE)
[PR3 merge: D — deploy-blocker closed]
```

---

## Slice D — Client→Server Key Loop (PR3) ✅ DONE

Deploy-blocker: gate active but widget never sent key; /branding never returned it.

### D-1 [RED] tests/test_pk_loop.py — 7 tests
- branding returns publishable_key (current/previous/scalar/no-keys variants)
- window.__PK__ sentinel replacement (sentinel bytes absent after replace)

### D-2 [GREEN] apps/agent/api/routers/cobranza.py
- /branding returns `publishable_key` field (_resolve_current_pk inline helper)
- Handles: publishable_keys list (current flagged / first fallback) + legacy scalar + no keys → ""

### D-3 [GREEN] apps/agent/api/main.py
- _mount_demo_frontend replaces b'"__PK__"' sentinel with DEFAULT_TENANT's current pk
- Uses _load_tenant_config (same pattern as __TENANT__ replacement)

### D-4 [GREEN] frontend/index.html + frontend/tenants/prestamype/index.html
- Added `<script>window.__PK__ = "__PK__";</script>` after __TENANT__ sentinel

### D-5 [GREEN] frontend/widget.js
- PK resolution chain: opts.pk → scriptEl.dataset.pk → window.__PK__ → branding fallback
- Sentinel guard: if PK === "__PK__" → null (misconfigured deploy protection)
- X-Publishable-Key header added to POST /chat (submit()) and POST /comprobante (submitComprobante())

### D-6 [GREEN] frontend/embed.js
- data-pk attribute documented in doc block with example snippet
- pk = attr("data-pk", null) → passed to window.PubotWidget.mount({ ..., pk })

### D-7 [SMOKE] 497 tests passing (full suite, was 490, +7 new)

---

## Checklist

### Slice A — Key Gate (PR1) ✅
- [x] A-1: Write widget_gate unit tests (RED)
- [x] A-2: Write resolve_tenant_by_pk unit tests (RED, same file)
- [x] A-3: Implement resolve_tenant_by_pk (GREEN)
- [x] A-4: Implement require_publishable_key factory (GREEN)
- [x] A-5: Write gated-route integration tests (RED)
- [x] A-6: Wire Depends onto gated + allow_no_key routes (GREEN)
- [x] A-7: Add X-Publishable-Key to CORS allow_headers (GREEN)
- [x] A-8: Update _template + tenant configs (append publishable_keys/embed_origins)
- [ ] A-9: Refactor pk map caching (optional)

### Slice B — CORS Guard (PR1) ✅
- [x] B-1: Write collect_embed_origins "*" rejection test (RED)
- [x] B-2: Implement "*" rejection in cors.py (GREEN)

### Slice C — Distribution (PR2) ✅
- [x] C-1: Update test_embed_widget.py (RED: 302 + versioned + 404)
- [x] C-2: Add esbuild Docker multi-stage build
- [x] C-3: Implement versioned /widget/{version}/widget.min.js route (GREEN)
- [x] C-4: Implement legacy /widget.js → 302 redirect (GREEN)
- [x] C-5: Verify PubotWidget global survives minification (REFACTOR/smoke)

### Slice D — Client→Server Key Loop (PR3) ✅ — deploy-blocker closed
- [x] D-1: Write test_pk_loop.py RED tests (branding pk + __PK__ sentinel)
- [x] D-2: /branding returns publishable_key (GREEN)
- [x] D-3: main.py injects window.__PK__ sentinel (GREEN)
- [x] D-4: Add __PK__ sentinel to both index.html files (GREEN)
- [x] D-5: widget.js PK resolution + X-Publishable-Key on gated fetches (GREEN)
- [x] D-6: embed.js data-pk attr documented + passed to mount() (GREEN)
- [x] D-7: Full suite smoke — 497 passing
