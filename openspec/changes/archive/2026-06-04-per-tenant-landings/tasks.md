# Tasks: Per-Tenant Landings (server-side tenant resolution)

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | 500–650 (index.html -190 CSS + JS refactor, app.js +~220, new prestamype/index.html +~180, main.py +~30, widget.js ~5, docker-compose ~3, new test file ~80) |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR 1: tests (RED) + backend GET / handler + env wiring → PR 2: frontend extraction (app.js + per-tenant index.html + generic cleanup + widget.js fix) |
| Delivery strategy | ask-on-risk |
| Chain strategy | pending |

Decision needed before apply: Yes
Chained PRs recommended: Yes
Chain strategy: pending
400-line budget risk: High

### Suggested Work Units

| Unit | Goal | Likely PR | Notes |
|------|------|-----------|-------|
| 1 | Tests (RED) + `GET /` handler + compose env wiring | PR 1 | Base: `feat/prestamype-landing-redesign`; handler passes tests; StaticFiles still works |
| 2 | Extract `app.js` + create `tenants/prestamype/index.html` + clean generic `index.html` + fix `widget.js:70` | PR 2 | Base: PR 1 branch; turns RED tests GREEN; all 452+ tests pass |

---

## Phase 1: Test Infrastructure (RED — write failing tests first)

- [x] 1.1 Create `tests/test_frontend_serving.py` with `test_get_root_injects_default_tenant`: set `DEFAULT_TENANT=prestaunion`, `GET /`, assert 200 + `window.__TENANT__ = "prestaunion"` in body.
- [x] 1.2 Add `test_get_root_prestamype_injects_tenant`: monkeypatch `DEFAULT_TENANT=prestamype`, mock `frontend/tenants/prestamype/index.html`, `GET /` with no `?tenant=`, assert body contains `window.__TENANT__ = "prestamype"` — regression guard for latent wrong-tenant bug.
- [x] 1.3 Add `test_get_root_falls_back_to_generic`: set `DEFAULT_TENANT=newclient` (no matching tenant file), assert 200 using generic `index.html` with `window.__TENANT__ = "newclient"`.
- [x] 1.4 Add `test_widget_js_served_as_static`: `GET /widget.js`, assert 200.
- [x] 1.5 Add `test_app_js_served_as_static`: `GET /app.js`, assert 200 (GREEN — PR2 done, app.js exists).
- [x] 1.6 Confirm RED state before implementation (verified: 3 of 5 failed with 422 before fix).

## Phase 2: Backend — GET / Handler (GREEN for Phase 1 tests)

- [x] 2.1 In `apps/agent/api/main.py` `_mount_demo_frontend()` (~line 255–268): read `DEFAULT_TENANT = os.environ.get("DEFAULT_TENANT", "prestaunion")`.
- [x] 2.2 Add startup byte-cache: select `frontend/tenants/<T>/index.html` if path exists else `frontend/index.html`; read bytes; replace `b'"__TENANT__"'` → `b'"' + T + b'"'` (targets quoted value only, not JS property name).
- [x] 2.3 Register `@app.get("/", include_in_schema=False)` BEFORE the `app.mount("/", StaticFiles(...))` call; handler returns `HTMLResponse(content=cached, media_type="text/html;charset=utf-8")` — no Request param (FastAPI 0.136.3 underscore param issue).
- [x] 2.4 StaticFiles mount unchanged (widget.js/assets still served, verified by test_widget_js_served_as_static).

## Phase 3: Env Wiring (compose files)

- [x] 3.1 In `infrastructure/docker-compose.yml` add `DEFAULT_TENANT: prestaunion` to the `pubot-demo` service environment block.
- [x] 3.2 Documented in apply-progress.md: remote manual steps for Traefik redirectRegex removal + DEFAULT_TENANT: prestamype on prestamype compose.

## Phase 4: Frontend Extraction (GREEN for 1.5; completes all tests)

- [x] 4.1 Extract the inline `<script>` block from `frontend/index.html` into new file `frontend/app.js` (~230 lines, DRY seam).
- [x] 4.2 `frontend/index.html`: replace inline `<script>` block with `<script src="app.js"></script>` (relative path, matches widget.js style).
- [x] 4.3 Remove prestamype-specific CSS lines 165–354 from `frontend/index.html`; 3 `if(TENANT==='prestamype')` JS branches remain in app.js (data branches, not duplication). generic index.html: 730→306 lines.
- [x] 4.4 Create `frontend/tenants/prestamype/index.html`: head sentinel + de-scoped prestamype CSS (body.tenant-prestamype prefix dropped) + `<script src="app.js"></script>` — NO inline JS logic.
- [x] 4.5 (widget.js:70 done in PR1; ordering intentionally preserved per verify WARNING — correct for embed use-case).

## Phase 5: Verification and Deploy-Time Manual Steps

- [x] 5.1 Run full test suite: `uv run pytest tests/ -q`; 459 passed (457 + 2 new PR2 tests, zero regressions).
- [ ] 5.2 Manual smoke: `docker compose up` locally with `DEFAULT_TENANT=prestaunion`; verify `GET /` injects prestaunion, no prestamype CSS present.
- [ ] 5.3 Manual smoke: `DEFAULT_TENANT=prestamype`; verify `GET /` injects prestamype, prestamype CSS active.
- [ ] 5.4 **Deploy-time (manual, no automated test):** SSH to `automation` and locate prestamype Traefik router dynamic config: `ssh automation "grep -rniE 'tenant=|redirectregex|replacement' /home/onbot/automation/*/shared/traefik/dynamic/ /home/onbot/automation/prestaunion-demo/ 2>/dev/null"`. Remove the `redirectRegex` middleware that injects `?tenant=prestamype` (keep stripPrefix + redirectScheme). Add `DEFAULT_TENANT=prestamype` to remote prestamype compose env. Restart the prestamype container.
- [ ] 5.5 Verify no `git add -A`; stage only changed files; conventional commit message.
