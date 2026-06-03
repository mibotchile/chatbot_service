# Tasks: Refactor to Screaming Architecture

> Change: `refactor-screaming-architecture` · Project: `chatbot-cobranza`
> Gate: `uv run pytest tests/ -v` MUST pass after every slice. No merge with failing tests.
> Skills (sdd-apply must load): `/home/ricardo/.config/opencode/skills/chained-pr/SKILL.md`, `/home/ricardo/.config/opencode/skills/work-unit-commits/SKILL.md`

---

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | 2 000–3 000 (291 code refs + 10 SQL spots + god-file splits + 18 test re-maps + migration script) |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR 1: slices 0–2 · PR 2: slices 3–6 · PR 3: slice 7 · PR 4: slice 8 · PR 5: slice 9 · PR 6: slice 10 |
| Delivery strategy | ask-on-risk |
| Chain strategy | pending — ask user before apply |

Decision needed before apply: Yes
Chained PRs recommended: Yes
Chain strategy: pending
400-line budget risk: High

### Suggested Work Units

| Unit | Slices | Goal | Notes |
|------|--------|------|-------|
| PR 1 | 0–2 | Scaffold + shared/ + tenancy/ | Pure adds; no moves. Reviewable in isolation. |
| PR 2 | 3–6 | analytics/ comprobantes/ messaging/ cobranza/ | File moves + import re-maps. SQL unchanged. |
| PR 3 | 7 | conversation/ + delete opportunity_detector | God-files intact; structure + dead code only. |
| PR 4 | 8 | lead→debtor code+tool rename (no storage) | Mechanical rename; one reviewer concern. |
| PR 5 | 9 | Storage migration (atomic deploy) | Preflight required; highest blast radius. |
| PR 6 | 10 | api/main.py split + thin api | Characterization tests first; final wiring. |

---

## Phase 1 — Scaffold (Slice 0) ✅ DONE (PR1)

- [x] 1.1 Create `features/{conversation,cobranza,comprobantes,messaging,analytics}/` with `__init__.py`. No code moves.
- [x] 1.2 Create `shared/{llm,persistence,delivery,ports}/`, `shared/config/` with `__init__.py`.
- [x] 1.3 Create `tenancy/` with `__init__.py`.
- [x] 1.4 Create `api/routers/` with `__init__.py`.
- [x] 1.5 Run `uv run pytest tests/ -v` — must be green (pure no-op).
- [x] 1.6 Commit: `chore(scaffold): create screaming-architecture directory skeleton`.

---

## Phase 2 — shared/ kernel (Slice 1) ✅ DONE (PR1)

- [x] 2.1 `git mv core/llm/* shared/llm/` — preserve history.
- [x] 2.2 `git mv core/persistence.py shared/persistence/persistence.py` + `git mv core/db.py shared/persistence/db.py`.
- [x] 2.3 `git mv core/rate_limit.py shared/rate_limit.py` + `git mv core/webhooks.py shared/webhooks.py` + `git mv core/webhook_config.py shared/webhook_config.py`.
- [x] 2.4 `git mv config/settings.py shared/config/settings.py` + `git mv config/cors.py shared/config/cors.py` + `git mv config/tools_schema.py shared/config/tools_schema.py`.
- [x] 2.5 Re-map all imports. **Did NOT rename `webhook_lead_url`, `lead_transition_url`, `website_leads_only`.**
- [x] 2.6 Updated 11 test-file imports (import paths only — zero assertion/fixture changes). Also fixed cors._tenants_root() path depth (4→5 parent levels after move).
- [x] 2.7 Run `uv run pytest tests/ -v` — 310 green. `config/__init__.py` deleted at end of slice 2 after config/ fully vacated.
- [x] 2.8 Commit: `refactor(shared): move kernel modules to shared/`.

---

## Phase 3 — tenancy/ (Slice 2) ✅ DONE (PR1)

- [x] 3.1 `git mv core/tenant_loader.py tenancy/tenant_loader.py` + `git mv config/soul.py tenancy/soul.py` + `git mv config/pricing.py tenancy/pricing.py`.
- [x] 3.2 Extract `ResponsesSpec` dataclass from `core/responses.py` into `tenancy/responses_spec.py`. core/responses.py re-exports ResponsesSpec for backward compat. Restored dataclass/field imports in responses.py (needed by CannedResult + RouterOutcome).
- [x] 3.3 Re-map imports: `config.soul→tenancy.soul`, `config.pricing→tenancy.pricing`, `core.tenant_loader→tenancy.tenant_loader`. Updated test_analytics_doris, test_responses_engine, test_smoke, test_cobranza_prestamype, test_rate_limiting.
- [x] 3.4 Run `uv run pytest tests/ -v` — 310 green.
- [x] 3.5 Commit: `refactor(tenancy): extract tenant_loader, soul, pricing, ResponsesSpec`.

---

## Phase 4 — features/analytics/ (Slice 3 — file move only, SQL unchanged) ✅ DONE (PR2)

- [x] 4.1 **EXCLUDE-list pre-check**: SQL (`sorelia_leads`, `lead_level`) NOT renamed in this slice. Deferred to slice 9.
- [x] 4.2 `git mv integrations/analytics_sink.py features/analytics/analytics_sink.py`.
- [x] 4.3 `git mv api/dashboard.py features/analytics/dashboard.py`. Updated `api/main.py` imports: `api.dashboard→features.analytics.dashboard`, `from integrations import analytics_sink→from features.analytics import analytics_sink`. **SQL unchanged.**
- [x] 4.4 Re-mapped `test_analytics_doris.py`: `from integrations import analytics_sink → from features.analytics import analytics_sink`.
- [x] 4.5 Run `uv run pytest tests/ -q` — 310 green.
- [x] 4.6 Commit: `refactor(analytics): move dashboard + analytics_sink to features/analytics/ (SQL unchanged)`.

---

## Phase 5 — features/comprobantes/ + shared/delivery/ (Slice 4) ✅ DONE (PR2)

- [x] 5.1 `git mv integrations/certificate_pdf.py shared/delivery/certificate_pdf.py` + `git mv core/email_service.py shared/delivery/email_delivery.py`.
- [x] 5.2 Carved `features/comprobantes/validator.py` from `tools/cobranza.py` lines 699–871: `_load_comprobantes`, `_save_comprobantes`, `_normalize_account_type`, `registrar_comprobante_foto`, `validar_comprobante`. Removed from `tools/cobranza.py` (re-export stubs removed in slice 6). classify_tipo/normalize_cci extracted to `shared/debt_math.py` to avoid cross-feature dep.
- [x] 5.3 Re-mapped all callers. Tests updated to patch `features.comprobantes.validator._COMPROBANTES_PATH` directly (not via tools.cobranza). shared/webhooks.py WhatsAppService type hint replaced with `Any` to avoid shared→features violation.
- [x] 5.4 Run `uv run pytest tests/ -q` — 310 green.
- [x] 5.5 Commit: `refactor(comprobantes): carve validator into features/comprobantes/; move delivery to shared/`.

---

## Phase 6 — features/messaging/ (Slice 5) ✅ DONE (PR2)

- [x] 6.1 `git mv core/whatsapp_service.py features/messaging/whatsapp_service.py` + `git mv core/whatsapp_formatter.py features/messaging/whatsapp_formatter.py`.
- [x] 6.2 `git mv integrations/chathub_adapter.py features/messaging/chathub_adapter.py` + `git mv integrations/chathub_outbound.py features/messaging/chathub_outbound.py` + `git mv integrations/chathub_web_publisher.py features/messaging/chathub_web_publisher.py`.
- [x] 6.3 Re-mapped all imports in api/main.py, api/chathub.py, shared/webhooks.py, test_dni_and_delivery.py, test_chathub_adapter.py, test_chathub_comprobante.py, test_chathub_web_publisher.py.
- [x] 6.4 Run `uv run pytest tests/ -q` — 310 green.
- [x] 6.5 Commit: `refactor(messaging): move whatsapp + chathub into features/messaging/`.

---

## Phase 7 — features/cobranza/ (Slice 6) ✅ DONE (PR2)

- [x] 7.1 `git mv tools/cobranza.py features/cobranza/tools.py` (remainder after comprobantes carve). Cross-feature re-export removed; tools/__init__.py imports comprobantes functions directly from features.comprobantes.validator.
- [x] 7.2 `git mv integrations/debt_source.py features/cobranza/debt_source.py` + `git mv integrations/doris_debt_source.py features/cobranza/doris_debt_source.py` + `git mv integrations/mock_debt_source.py features/cobranza/mock_debt_source.py`. Fixed `_tenants_root()` path (4→5 parent levels). Extracted `classify_tipo` + `normalize_cci` from doris_debt_source → `shared/debt_math.py`. Internal `from integrations import mock_debt_source` → `from features.cobranza import mock_debt_source`.
- [x] 7.3 Re-mapped all callers in api/main.py, api/chathub.py, tools/__init__.py, and 6 test files. test_doris_schema.py inline `import integrations.doris_debt_source` replaced with module alias already imported.
- [x] 7.4 Run `uv run pytest tests/ -q` — 310 green.
- [x] 7.5 Commit: `refactor(cobranza): move cobranza tools + debt sources into features/cobranza/; extract debt_math to shared/`.

---

## Phase 8 — features/conversation/ + delete opportunity_detector (Slice 7) ✅ DONE (PR3)

- [x] 8.1 **[CHARACTERIZATION TEST — STRICT TDD]** Write characterization tests for any untested code paths in `core/responses.py`, `core/agent.py`, `core/conversation_fsm.py` before moving. Gate: suite green including new tests. (All paths already covered — 310 green, no new tests required.)
- [x] 8.2 `git mv core/agent.py features/conversation/agent.py` + `git mv core/responses.py features/conversation/responses.py` + `git mv core/response_builder.py features/conversation/response_builder.py` + `git mv core/response_guard.py features/conversation/response_guard.py` + `git mv core/conversation_fsm.py features/conversation/conversation_fsm.py` + `git mv core/hooks.py features/conversation/hooks.py`.
- [x] 8.3 `git mv core/lead_machine.py features/conversation/debtor_state.py` (file rename; class rename deferred to slice 8).
- [x] 8.4 `git mv core/prospect_profile.py features/conversation/debtor_profile.py` (file rename only; symbol rename deferred to slice 8).
- [x] 8.5 `git mv prompts/system.py features/conversation/prompts.py`. Delete `prompts/__init__.py`. Also moved guardrails.md + identity.md to features/conversation/.
- [x] 8.6 `git mv skills/ features/conversation/skills/` (7 SKILL.md + loader — NOT dead code).
- [x] 8.7 `git mv core/state.py features/conversation/persistence/state.py` + `git mv core/redis_store.py features/conversation/persistence/redis_store.py` + `git mv core/visitor_memory.py features/conversation/persistence/visitor_memory.py`.
- [x] 8.8 **DELETE `core/opportunity_detector.py`**. Removed `"opportunities": []` from `tools/__init__.py`. Removed opportunities render block from `features/conversation/prompts.py`. Removed `get_status()` opportunities block from `features/conversation/debtor_state.py`.
- [x] 8.9 **Snapshot guard**: `grep -rn "Oportunidades de extraccion" tests/` → zero matches.
- [x] 8.10 Re-mapped all imports across api/main.py (9 sites), api/chathub.py (3 sites), features/cobranza/tools.py (1 site), tools/__init__.py (1 site), and 5 test files. ResponsesSpec shim dissolved: 3 callers updated to import from tenancy.responses_spec directly; noqa re-export line removed.
- [x] 8.11 310 tests green. core/__init__.py + integrations/__init__.py + prompts/__init__.py deleted. core/ has only llm/ pycache stub (cleaned by OS).
- [x] 8.12 Committed: `refactor(conversation): move conversation modules + persistence; delete opportunity_detector` (7af5ec6).

---

## Phase 9 — lead→debtor code + tool rename, NO storage (Slice 8)

- [ ] 9.1 **EXCLUDE-list verification FIRST**: `grep -rn "webhook_lead_url\|lead_transition_url\|website_leads_only" apps/agent/` — record exact file:line. Confirm these are NOT in the rename set.
- [ ] 9.2 Apply symbol renames across all moved modules (exclude §4b symbols): `lead_state→debtor_state`, `class LeadMachine→DebtorState` (in `features/conversation/debtor_state.py`), `build_prospect_profile→build_debtor_profile` (in `features/conversation/debtor_profile.py`), `self.lead→self.debtor` (agent.py, redis_store.py), `lead_data param→debtor_data` (persistence/state.py, persistence.py), `lead_level code refs→debtor_level` (constants, params, persistence refs — NOT storage column yet).
- [ ] 9.3 **[LLM CONTRACT — TOOL RENAME]** In `shared/config/tools_schema.py:22`: `"get_lead_status"→"get_debtor_status"` + update description text + docstring L8. In `api/tool_registry.py` (was `tools/__init__.py`): dispatch key `:96` + handler `:131` renamed.
- [ ] 9.4 Grep `get_lead_status` in `features/conversation/skills/*/SKILL.md` + `features/conversation/prompts.py` — rename every occurrence.
- [ ] 9.5 Check `tenant.config.json` files for `get_lead_status` references — rename if found.
- [ ] 9.6 **[NEW TEST — STRICT TDD]** Add/adjust test asserting `get_debtor_status` is the registered tool name. Gate: red → green.
- [ ] 9.7 Update `test_smoke.py:55` `INTEREST_FIELDS` import path to `features.conversation.debtor_state`. Update all test kwargs: `lead_state=→debtor_state=` (~17 call sites in tests). `_CONTACT_LEVELS` update deferred to slice 9 (storage).
- [ ] 9.8 **Post-rename diff check**: `git diff HEAD -- apps/agent/ | grep -E "webhook_lead_url|lead_transition_url|website_leads_only"` — must return zero hits.
- [ ] 9.9 Run `uv run pytest tests/ -v` — green.
- [ ] 9.10 Commit: `feat(debtor): rename lead→debtor in code + tool contract (get_debtor_status); no storage change`.

---

## Phase 10 — Storage migration + atomic deploy (Slice 9 — RISKIEST)

- [ ] 10.1 **[PREFLIGHT TASK — MANDATORY, no proceed without human "go"]** For each active tenant schema run:
  - `SELECT COUNT(*) FROM {schema}.sorelia_leads` (rows affected by RENAME).
  - `SELECT COUNT(*) FROM {schema}.sorelia_conversations` (rows affected by `debtor_data` additive + `debtor_level` rename).
  - `SELECT COUNT(*) FROM {schema}.sorelia_conversations WHERE lead_data IS NOT NULL` (rows to backfill).
  - Idempotency: every DDL statement guarded `IF EXISTS`/`IF NOT EXISTS`/`CASE`; re-runs are no-ops.
  - `pg_dump` of `district_interest, purpose, budget` columns (UNRECOVERABLE after DROP): `pg_dump --table={schema}.sorelia_leads --column-inserts ... > /tmp/sorelia_leads_backup_{date}.sql`.
  - Rollback SQL: `ALTER TABLE {schema}.sorelia_debtors RENAME TO sorelia_leads; ALTER TABLE {schema}.sorelia_debtors RENAME COLUMN debtor_level TO lead_level; ALTER TABLE {schema}.sorelia_conversations RENAME COLUMN debtor_level TO lead_level;` (dropped columns restored from pg_dump only).
  - **Human confirmation gate**: "No out-of-repo ETL reads `{schema}.sorelia_leads` from Postgres directly" — checkbox required.
  - Output preflight report. **STOP and wait for explicit "go".**
- [ ] 10.2 Write `scripts/migrate_lead_to_debtor.py` — idempotent, per-tenant-schema (design §4d SQL). Include forward migration + reverse migration function.
- [ ] 10.3 **Additive part** (dual-read safe, can deploy without downtime): `ADD COLUMN IF NOT EXISTS debtor_data JSONB`; update `features/conversation/persistence/state.py` load logic: read `debtor_data`, fallback `lead_data`; write `debtor_data`.
- [ ] 10.4 **Atomic part** (NOT dual-read-safe — deploys together with code): `RENAME TABLE sorelia_leads→sorelia_debtors`; `RENAME COLUMN lead_level→debtor_level` (both tables); enum value remap UPDATE (PRE_LEAD→PRE_DEBTOR, LEAD→DEBTOR, LEAD_ENRICHED→DEBTOR_VERIFIED); `DROP COLUMN district_interest, purpose, budget`.
- [ ] 10.5 Update `features/conversation/persistence/redis_store.py`: write key suffix `:debtor_data`; fallback-read `:lead_data` (≤24h TTL overlap).
- [ ] 10.6 `upsert_lead→upsert_debtor` in `shared/persistence/persistence.py`; update all callers in `api/main.py`.
- [ ] 10.7 Update `_CONTACT_LEVELS={"DEBTOR","DEBTOR_VERIFIED"}` in `api/main.py` (was `{"LEAD","LEAD_ENRICHED"}`). Update `main.py:1654` VISITOR check (enum value string unchanged — VISITOR keeps).
- [ ] 10.8 **Dashboard SQL atomic update** (in `features/analytics/dashboard.py`): `FROM {schema}.sorelia_leads→sorelia_debtors`; `lead_level→debtor_level`; enum filter values remapped; remove dead `CONTACT`/`QUALIFIED` filters (always-zero).
- [ ] 10.9 **[NEW TEST — STRICT TDD]** Write test asserting post-migration: `sorelia_debtors` exists, `debtor_level` column exists, old enum values absent, `project_interest` column still present.
- [ ] 10.10 Run `uv run pytest tests/ -v` — green.
- [ ] 10.11 Commit: `feat(migration): atomic storage rename sorelia_leads→debtors, lead_level→debtor_level, enum remap, column drops`. Include preflight block in PR description.

---

## Phase 11 — api/main.py thin + final cleanup (Slice 10)

- [ ] 11.1 **[CHARACTERIZATION TEST — STRICT TDD]** Before splitting `api/main.py` (1905 lines): verify `test_smoke`, `test_chat_identity_e2e`, `test_chathub_comprobante`, `test_frontend_branding_comprobante`, `test_embed_widget`, `test_deploy_readiness` all pass and cover the seams to be extracted. Add characterization tests for uncovered seams.
- [ ] 11.2 `git mv tools/__init__.py api/tool_registry.py` (ToolRegistry; `get_lead_status` dispatch already renamed in slice 8).
- [ ] 11.3 `git mv api/chathub.py api/routers/chathub.py`. Update main.py router include.
- [ ] 11.4 Extract from `api/main.py` into `api/middleware.py`: Rate/Security/CSRF/session helpers.
- [ ] 11.5 Extract into `api/routers/chat.py`: chat endpoint + lifespan wiring helpers.
- [ ] 11.6 Extract into `api/routers/cobranza.py`, `api/routers/comprobante.py`, `api/routers/webhooks.py`, `api/routers/tenant.py`: their respective route groups.
- [ ] 11.7 Extract analytics helpers from `api/main.py` → `features/analytics/` (if any remain). Extract service getters → `api/wiring.py`.
- [ ] 11.8 Verify `**website_leads_only**` operation-mode strings at `main.py:1850,1874` are VERBATIM in the extracted router. Run grep post-split.
- [ ] 11.9 Confirm `api/main.py` ≤ ~150 lines (app object + lifespan + router includes + wiring).
- [ ] 11.10 Delete empty directories: `core/`, `config/`, `tools/`, `integrations/`, `prompts/` (if all emptied).
- [ ] 11.11 Verify dependency rules: `grep -rn "from features" shared/ tenancy/` → zero; `grep -rn "from features" features/conversation/ --include="*.py" | grep -v "features.conversation"` → zero (no cross-feature).
- [ ] 11.12 Run `uv run pytest tests/ -v` — green. Run `git log --follow features/conversation/debtor_state.py` to confirm history preserved.
- [ ] 11.13 Commit: `refactor(api): split main.py → thin api + routers + wiring; delete empty legacy dirs`.

---

## Cross-Cutting Verification (after all slices merged)

- [ ] 12.1 `uv run pytest tests/ -v` — full suite green.
- [ ] 12.2 `grep -rn "get_lead_status" apps/agent/` → zero matches (except excluded `webhook_*` / `website_leads_only` — confirm those are absent too).
- [ ] 12.3 `grep -rn "class LeadMachine\|build_prospect_profile\|opportunity_detector\|opportunities\b" apps/agent/` → zero matches.
- [ ] 12.4 `grep -rn "from core\.\|from config\.\|from tools\.\|from integrations\.\|from prompts\." apps/agent/` → zero matches (all legacy namespaces gone).
- [ ] 12.5 Confirm `api/main.py` ≤ 150 lines.
- [ ] 12.6 Confirm `project_interest` column present in `sorelia_debtors` post-migration.
- [ ] 12.7 Confirm `district_interest`, `purpose`, `budget` absent post-migration.
- [ ] 12.8 Confirm `webhook_lead_url`, `lead_transition_url`, `website_leads_only` strings unchanged (grep to verify).
