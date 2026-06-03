# Design: Refactor to Screaming Architecture

> Change: `refactor-screaming-architecture` · Project: `chatbot-cobranza`
> Status: design (HOW at architecture level). Structural refactor + a deliberate semantic rename (`lead`→`debtor`) WITH data migration. Zero behavior change EXCEPT the tool-name change (`get_lead_status`→`get_debtor_status`, covered by tests) and the migration (correctness-preserving). Tests are the contract.
> Runner: `uv run pytest tests/ -v` (STRICT TDD active).

## 0. What changed vs the previous design (4 confirmed decisions by Ricky)

1. **`leads` feature DISSOLVED** — it was sorelia ventas heritage, not a cobranza domain. Features are now **FIVE**: `conversation`, `cobranza`, `comprobantes`, `messaging`, `analytics`. `lead_machine` + `prospect_profile` + `visitor_memory` are conversation state/history/memory → move into `features/conversation/`. The `conversation→leads` edge disappears.
2. **`opportunity_detector.py` DELETED** (dead code) + the always-empty `opportunities` field cleaned out.
3. **`lead`→`debtor` FULL RENAME** across code + tool name (`get_lead_status`→`get_debtor_status`) + storage, WITH a data migration. **Scope EXPANDED (later decision by Ricky):** the dashboard/analytics sorelia heritage is now INCLUDED — `sorelia_leads→sorelia_debtors` table, `lead_level→debtor_level` column+enum, drop dead real-estate columns. See §4c (consumer mapping) + §4d.
4. KEEP: `shared/delivery/` for cert+email, `state.py`+`redis_store.py`→`features/conversation/persistence/`, `ToolRegistry`→`api/tool_registry.py`, `skills/`→`features/conversation/` (NOT dead), outside-in slice order.

## 1. Architecture Approach

**Pattern:** Screaming (feature-first / vertical-slice) over a technical kernel.

- **`features/`** — five vertical business slices: `conversation`, `cobranza`, `comprobantes`, `messaging`, `analytics`.
- **`shared/`** (kernel, name fixed by Ricky) — pure plumbing: `llm`, `persistence`, `delivery`, `rate_limit`, `webhooks`, `config`, `ports`.
- **`tenancy/`** — multi-tenant loading/config: `tenant_loader`, `soul`, `pricing`, `responses_spec`.
- **`api/`** — thin HTTP entrypoint: `main.py` (app + wiring) + `routers/` + `tool_registry.py`.

**Dependency rules (enforced):**
1. `features/*` → may import `shared/` + `tenancy/`. Never the reverse.
2. `features/*` isolated from each other except via composition in `api/` (`ToolRegistry`) or a `shared/ports/` protocol.
3. `shared/` imports nothing from `features/`, `tenancy/`, `api/`. Pure.
4. `tenancy/` imports `shared/` only.
5. `api/` orchestrates: imports features + tenancy + shared; no business logic.

**Migration mechanic:** `git mv` per file (preserves history) + import re-map + full suite green after EVERY slice. The rename is applied symbol-by-symbol within the relevant slice; the storage rename + data migration is its own isolated slice (highest blast radius).

## 2. File-by-File Migration Map (VERIFIED against repo)

### features/conversation/  (the dialogue engine — what the bot IS; absorbs dissolved leads)
| From | To | Rename applied |
|------|-----|----------------|
| `core/agent.py` | `features/conversation/agent.py` | `self.lead`→`self.debtor`, `build_prospect_profile`→`build_debtor_profile` |
| `core/responses.py` (764) | `features/conversation/responses.py` — SPLIT §3.C | `lead_state`→`debtor_state` |
| `core/response_builder.py` | `features/conversation/response_builder.py` | refs |
| `core/response_guard.py` | `features/conversation/response_guard.py` | refs |
| `core/conversation_fsm.py` | `features/conversation/conversation_fsm.py` | refs |
| `core/hooks.py` | `features/conversation/hooks.py` | refs |
| `core/lead_machine.py` | `features/conversation/debtor_state.py` | `class LeadMachine`→`DebtorState`; file renamed |
| `core/prospect_profile.py` | `features/conversation/debtor_profile.py` | `build_prospect_profile`→`build_debtor_profile`; file renamed |
| `prompts/system.py` | `features/conversation/prompts.py` | `lead_state` param→`debtor_state`; remove opportunities render (§3.D) |
| `skills/` (package, 7 SKILL.md + loader) | `features/conversation/skills/` | NOT dead — keep |
| `prompts/__init__.py` | DELETE (package dissolved) | — |

### features/conversation/persistence/  (conversation-state persistence)
| From | To | Rename applied |
|------|-----|----------------|
| `core/state.py` | `features/conversation/persistence/state.py` | `lead_data`→`debtor_data` params; import `DebtorState` |
| `core/redis_store.py` | `features/conversation/persistence/redis_store.py` | `self.lead`→`self.debtor`; redis key suffix `lead_data`→`debtor_data` (§4 migration) |
| `core/visitor_memory.py` | `features/conversation/persistence/visitor_memory.py` | (not a debtor symbol; visitor rate-limit memory) |

### features/cobranza/  (debt + negotiation)
| From | To |
|------|-----|
| `tools/cobranza.py` (870) | `features/cobranza/tools.py` — SPLIT §3.B |
| `integrations/debt_source.py` | `features/cobranza/debt_source.py` (port) |
| `integrations/doris_debt_source.py` | `features/cobranza/doris_debt_source.py` |
| `integrations/mock_debt_source.py` | `features/cobranza/mock_debt_source.py` |

### features/comprobantes/  (payment validation + certificates)
| From | To |
|------|-----|
| comprobante block in `tools/cobranza.py` (`_load_comprobantes` L708, `_save_comprobantes` L717, `_normalize_account_type` L733, `registrar_comprobante_foto` L739, `validar_comprobante` L781) | `features/comprobantes/validator.py` (NEW) |

### features/messaging/  (whatsapp + chathub)
| From | To |
|------|-----|
| `core/whatsapp_service.py` | `features/messaging/whatsapp_service.py` |
| `core/whatsapp_formatter.py` | `features/messaging/whatsapp_formatter.py` |
| `integrations/chathub_adapter.py` | `features/messaging/chathub_adapter.py` |
| `integrations/chathub_outbound.py` | `features/messaging/chathub_outbound.py` |
| `integrations/chathub_web_publisher.py` | `features/messaging/chathub_web_publisher.py` |

### features/analytics/
| From | To |
|------|-----|
| `integrations/analytics_sink.py` | `features/analytics/analytics_sink.py` |
| `api/dashboard.py` (31 lead refs — reads `sorelia_leads`/`lead_level`/`project_interest`) | `features/analytics/dashboard.py` — file MOVE in slice 3; SQL rename (`sorelia_debtors`/`debtor_level`) lands ATOMICALLY in slice 9 (§4c/§4d) |

### shared/
| From | To |
|------|-----|
| `core/llm/*` | `shared/llm/*` (whole package) |
| `core/persistence.py` | `shared/persistence/persistence.py` — rename per §4 |
| `core/db.py` | `shared/persistence/db.py` |
| `core/rate_limit.py` | `shared/rate_limit.py` |
| `core/webhooks.py` | `shared/webhooks.py` |
| `core/webhook_config.py` | `shared/webhook_config.py` — **`lead_transition_url` EXCLUDED from rename** |
| `core/email_service.py` | `shared/delivery/email_delivery.py` |
| `integrations/certificate_pdf.py` | `shared/delivery/certificate_pdf.py` |
| `config/settings.py` | `shared/config/settings.py` — **`webhook_lead_url`, `website_leads_only` EXCLUDED** |
| `config/cors.py` | `shared/config/cors.py` |
| `config/tools_schema.py` | `shared/config/tools_schema.py` — `get_lead_status`→`get_debtor_status` (§3.E) |

### tenancy/
| From | To |
|------|-----|
| `core/tenant_loader.py` | `tenancy/tenant_loader.py` |
| `config/soul.py` | `tenancy/soul.py` |
| `config/pricing.py` | `tenancy/pricing.py` |
| `ResponsesSpec` (from `core/responses.py`) | `tenancy/responses_spec.py` (§4 cycle) |
| `config/__init__.py`, `prompts/__init__.py` | DELETE |

### api/  (thin)
| From | To |
|------|-----|
| `api/main.py` (1905, 70 lead refs) | `api/main.py` (~150) — SPLIT §3.A. **`website_leads_only` mode strings EXCLUDED** (L1850,1874) |
| `api/chathub.py` | `api/routers/chathub.py` |
| `tools/__init__.py` (ToolRegistry, dispatch key `get_lead_status` L96 + handler `_get_lead_status` L131 + hardcoded `opportunities` L138) | `api/tool_registry.py` — rename dispatch+handler (§3.E), delete opportunities (§3.D) |

### DELETE (dead code)
| Item | Why |
|------|-----|
| `core/opportunity_detector.py` | Dead: `detect_opportunities` has no callers; sorelia real-estate content (simulate_mortgage, bono MiVivienda). |
| `apps/agent/skills/` | NOT dead — moved, see above. (Corrects prior design.) |

## 3. God-File Splits + Rename Mechanics

### 3.A — `api/main.py` (1905 → ~150)
Seams (unchanged from prior design): app+lifespan+wiring STAYS; `api/middleware.py` (Rate/Security/CSRF/session helpers); `api/routers/{chat,cobranza,comprobante,webhooks,tenant}.py`; analytics helpers → `features/analytics/`; service getters → `api/wiring.py`. Characterization coverage before any move (STRICT TDD): `test_smoke`, `test_chat_identity_e2e`, `test_chathub_comprobante`, `test_frontend_branding_comprobante`, `test_embed_widget`, `test_deploy_readiness`. **`website_leads_only`** branch (L1850,1874) stays verbatim — it is an operation-mode string, NOT a debtor symbol.

### 3.B — `tools/cobranza.py` (870 → cobranza vs comprobantes)
Cobranza stays → `features/cobranza/tools.py`: `consultar_deuda` L83, `registrar_reclamo` L191, `emitir_certificado_no_adeudo` L244, `enviar_info` L470, `enviar_documento` L575 + helpers. Comprobante carve → `features/comprobantes/validator.py`: the L708–870 block. Trivial shared formatters: duplicate (KISS), no cross-feature import.

### 3.C — `core/responses.py` (764)
ONE cohesive curated-response engine. Do NOT split. Only `ResponsesSpec` (config dataclass, L236) extracted → `tenancy/responses_spec.py` (breaks cycle §4-#1). Rename `lead_state`→`debtor_state` in the public `build_variables`/render signatures.

### 3.D — Delete the dead `opportunities` field (zero-behavior)
Three coupled spots:
- `tools/__init__.py:138` — `"opportunities": []` hardcoded → remove the key.
- `core/lead_machine.py:62-74` — `get_status()` builds + returns `"opportunities"` → remove that block + key from the returned dict.
- `prompts/system.py:178,184` — reads it, renders `"Oportunidades de extraccion: ... ninguna"` (always empty) → remove both lines.
**Zero-behavior confirmation:** the rendered prompt loses one line that ALWAYS said "ninguna". No golden/snapshot test asserts that line — `test_smoke.py:63` only checks `build_system_prompt` runs; `test_responses_engine.py` passes `lead_state={}`. **Flag:** if tasks/apply discovers any snapshot fixture containing "Oportunidades de extraccion", update that fixture in the same commit. Confirmed none in the current grep.

### 3.E — Tool rename `get_lead_status`→`get_debtor_status` (LLM contract — 3 spots, must be consistent)
| Spot | Change |
|------|--------|
| `config/tools_schema.py:22` | `"name": "get_lead_status"` → `"get_debtor_status"` + update its `description` text + docstring L8 |
| `tools/__init__.py:96` | dispatch key `"get_lead_status"` → `"get_debtor_status"` |
| `tools/__init__.py:131` | handler `_get_lead_status` → `_get_debtor_status` |
| system prompt / skills | grep `get_lead_status` in `skills/*/SKILL.md` + prompts; rename any reference |
| tests | **no test asserts the string today** (only `SCAFFOLD-NOTES.md` doc). Add/adjust a test asserting `get_debtor_status` is the registered tool name to lock the new contract. |

## 4. lead→debtor Rename Map + Data Migration

### 4a. CODE/DOMAIN (safe, ~291 refs across 23 files)
| Old | New |
|-----|-----|
| `lead_state` | `debtor_state` |
| `class LeadMachine` / `LeadMachine()` | `DebtorState` |
| `lead_machine.py` | `debtor_state.py` |
| `prospect_profile.py` / `build_prospect_profile` / `truncate_history` | `debtor_profile.py` / `build_debtor_profile` / (truncate_history keeps name) |
| `self.lead` (state, redis_store) | `self.debtor` |
| `lead_data` param (persistence, state) | `debtor_data` |
| `_get_lead_status` / `get_lead_status` | `_get_debtor_status` / `get_debtor_status` (§3.E) |
| `INTEREST_FIELDS` (lead_machine) | keep name (domain-neutral); `test_smoke.py:55` imports it → re-map import path to `features.conversation.debtor_state` |

Hot files by count: `api/main.py` (70), `core/persistence.py` (46), `api/dashboard.py` (31), `core/webhooks.py` (16), `core/email_service.py` (16). NOTE many `api/main.py` hits are the EXCLUDED `website_leads_only` strings — filter those out before bulk rename.

### 4b. EXCLUDED from rename (these "lead" are NOT debtor — leave verbatim)
| Symbol | Location |
|--------|----------|
| `settings.webhook_lead_url` | `config/settings.py:65` |
| `webhook_config.lead_transition_url` | `core/webhook_config.py:10,17` |
| `"website_leads_only"` operation mode | `config/settings.py:79-80`, `api/main.py:1850,1874` |

### 4c. CONSUMER MAPPING (MANDATORY FIRST STEP of the dashboard slice — data-source-discipline)
The dashboard IS a contract. Done before any rename. Verified by reading `api/dashboard.py` fully + repo-wide grep for BigQuery/Doris/publish/sink.

**`api/dashboard.py` — every read of `sorelia_leads` / `lead_level`:**
| Column | Read at | Verdict |
|--------|---------|---------|
| `lead_level` | L100,113 (filter param), :132 (SELECT proj), :202/207/235 (enum `LEAD/CONTACT/QUALIFIED`), :280 (`/conversations`) | READ — rename column + enum values are a contract |
| `project_interest` | :132, :219-222 (`/stats` `top_projects` GROUP BY), :282 (`/conversations`) | **READ — LIVE. Do NOT blind-drop.** breaks `/stats` |
| `district_interest` | not read | DROP-safe |
| `purpose` | not read | DROP-safe |
| `budget` | not read | DROP-safe |
| `conversation_id,visitor_id,name,email,phone,created_at,source` | read/written | keep (debtor identity); `source` written never read |

**External sinks (BigQuery / Doris gold tables) carrying these columns — repo-wide grep result:**
- `integrations/analytics_sink.py` → Doris OLAP (`cobranza_analytics.bot_interactions`, `bot_llm_usage`). Columns = telemetry only (datetime, project_uid, tenant_id, session_id, channel, role, content, tokens, cost). **Does NOT carry any `sorelia_leads`/`lead_level` column.**
- **No `bigquery`/`gold`/`publish` of `sorelia_leads` columns exists inside `apps/agent`.** The dashboard is HTTP-only (FastAPI `/api/v1/dashboard/*`), consumed by the sales-team frontend via `X-Dashboard-Key`.
- **CONSUMER VERDICT: NOT BLOCKED.** No external OLAP/BigQuery consumer carries the renamed columns. The ONLY consumer of `sorelia_leads`/`lead_level` is `api/dashboard.py` itself, which is renamed in the SAME change. The repo-external `dashboard-builder`/BigQuery gold tables (Angeles/Paola) consume the Doris telemetry tables (`bot_*`), NOT `sorelia_leads` → unaffected by this rename. **Flag for human confirmation:** verify no out-of-repo ETL reads `{schema}.sorelia_leads` directly (e.g. a scheduled job pulling PG→BigQuery). Cannot be confirmed from this repo alone.

### 4d. PERSISTENCE RENAME + DATA MIGRATION (riskiest slice — INCLUDES sorelia heritage)
Verified schema (`core/persistence.py`):
- Table `{schema}.sorelia_conversations` — column **`lead_data` JSONB**, column **`lead_level` TEXT**.
- Table `{schema}.sorelia_leads` — LIVE denormalized debtor record (written by `upsert_lead` at main.py:419,1151; READ by `api/dashboard.py`). Columns: `id, conversation_id, visitor_id, name, email, phone, district_interest, project_interest, purpose, budget, lead_level, source, created_at, updated_at`.
- Redis: key `sorelia:conv:{id}:lead_data`, **TTL_SECONDS = 86400 (24h)**, value `json.dumps(self.lead.collected)`.

**Decisions (scope EXPANDED — sorelia heritage now INCLUDED):**

1. **Table `sorelia_leads` → `sorelia_debtors`** (keep `sorelia_` physical prefix = existing schema convention; rename the heritage `leads`→`debtors`. It is the denormalized debtor record). `upsert_lead` → `upsert_debtor`. All dashboard SQL `FROM {schema}.sorelia_leads` → `sorelia_debtors` (5 spots: L133,142,161,202-235,284).
   - DDL: `ALTER TABLE {schema}.sorelia_leads RENAME TO sorelia_debtors;` (`ensure_tables` creates `sorelia_debtors` going forward).
2. **`sorelia_conversations.lead_data` → `debtor_data`** (JSONB). Dual-read backward-compat (zero-downtime): `load_conversation` reads `debtor_data`, falls back to legacy `lead_data`; `save_conversation` writes `debtor_data`; `ensure_tables` `ADD COLUMN IF NOT EXISTS debtor_data`. `lead_data` retained until a later cleanup release.
3. **`lead_level` column → `debtor_level`** (now INCLUDED). Renamed in both `sorelia_conversations` and `sorelia_debtors`. Updated everywhere it is read/written: `persistence.py` (param + INSERT/UPDATE), `state.py:88`, dashboard (L100,113,132,202,207,235,280). **Enum VALUES decision:** the live state machine (`DebtorState`, ex-LeadMachine) emits `VISITOR / PRE_LEAD / LEAD / LEAD_ENRICHED` (verified: `main.py:445` `_CONTACT_LEVELS={"LEAD","LEAD_ENRICHED"}`, `main.py:1654` `=="VISITOR"`, `opportunity_detector.py:44` `=="PRE_LEAD"`); the dashboard `/stats` queries `IN ('LEAD','CONTACT','QUALIFIED')` and `='QUALIFIED'` — values the state machine NEVER emits (CONTACT/QUALIFIED are dead dashboard filters, sorelia leftovers).
   - **Enum rename: `VISITOR→VISITOR` (neutral, keep), `PRE_LEAD→PRE_DEBTOR`, `LEAD→DEBTOR`, `LEAD_ENRICHED→DEBTOR_VERIFIED`.** Update `DebtorState` level constants + `_CONTACT_LEVELS={"DEBTOR","DEBTOR_VERIFIED"}` + the two main.py checks. Dashboard `/stats` filters update to the new values; the dead `CONTACT`/`QUALIFIED` filters are removed (they always counted 0 — zero behavior change, but flag: `/stats` `total_contacts`/`qualified`/`conversion_funnel` numbers were already always 0 from these filters; confirm the frontend tolerates the funnel shape). **This enum-value change is a data migration** for existing rows (see migration script).
4. **DROP sorelia dead-weight columns** (verified NOT read by dashboard or any sink): `district_interest`, `purpose`, `budget`. **KEEP `project_interest`** — it is LIVE in `/stats` `top_projects` + `/conversations` + `/leads` projection. Dropping it breaks the dashboard → out of scope to drop now; flag as a follow-up product decision (real-estate concept in a cobranza bot). Also drop their writes in `persistence.upsert_debtor` (the `lead_data.get("district"/"purpose"/"budget")` params) and the `hooks.py`/`prospect_profile.py`/`email_service.py` producers of `purpose`/`budget` are conversation-side — leave producers (they only populate the dropped columns; harmless) OR remove in the same slice for cleanliness (flag, low-risk).

**Migration script** (`scripts/migrate_lead_to_debtor.py` — per tenant schema, idempotent):
```sql
-- guarded, re-runnable
ALTER TABLE {schema}.sorelia_leads RENAME TO sorelia_debtors;            -- skip if already renamed
ALTER TABLE {schema}.sorelia_debtors RENAME COLUMN lead_level TO debtor_level;
ALTER TABLE {schema}.sorelia_debtors DROP COLUMN IF EXISTS district_interest;
ALTER TABLE {schema}.sorelia_debtors DROP COLUMN IF EXISTS purpose;
ALTER TABLE {schema}.sorelia_debtors DROP COLUMN IF EXISTS budget;
ALTER TABLE {schema}.sorelia_conversations ADD COLUMN IF NOT EXISTS debtor_data JSONB DEFAULT '{}'::jsonb;
UPDATE {schema}.sorelia_conversations SET debtor_data = lead_data WHERE (debtor_data IS NULL OR debtor_data = '{}'::jsonb) AND lead_data IS NOT NULL;
ALTER TABLE {schema}.sorelia_conversations RENAME COLUMN lead_level TO debtor_level;
-- enum value remap (both tables)
UPDATE {schema}.sorelia_debtors      SET debtor_level = CASE debtor_level WHEN 'PRE_LEAD' THEN 'PRE_DEBTOR' WHEN 'LEAD' THEN 'DEBTOR' WHEN 'LEAD_ENRICHED' THEN 'DEBTOR_VERIFIED' ELSE debtor_level END;
UPDATE {schema}.sorelia_conversations SET debtor_level = CASE debtor_level WHEN 'PRE_LEAD' THEN 'PRE_DEBTOR' WHEN 'LEAD' THEN 'DEBTOR' WHEN 'LEAD_ENRICHED' THEN 'DEBTOR_VERIFIED' ELSE debtor_level END;
```
- **`RENAME COLUMN`/`RENAME TO`/`DROP COLUMN` are NOT dual-read-safe** — they break old code instantly. So the table/column rename is a SINGLE atomic deploy: code + migration ship together (unlike the additive `debtor_data` which is dual-read). Mitigation: brief maintenance window OR accept that `sorelia_leads` is dashboard-only (no hot-path read) so a few seconds of dashboard 500s during deploy is tolerable — confirm with Ricky.
- **Redis: NO migration** — 24h TTL self-expires; write `:debtor_data`, fallback-read `:lead_data` for the ≤24h overlap.
- **DESTRUCTIVE prod op → MANDATORY PREFLIGHT BLOCK at apply** (per `preflight-destructive.md`): per active tenant schema — rows affected (`COUNT(*)` per table), idempotency (every statement guarded `IF EXISTS`/`IF NOT EXISTS`/`WHERE`/`CASE`; re-runs are no-ops), rollback (`debtor_data` additive = safe; the RENAMEs/DROPs require a reverse-migration script `RENAME debtor_level→lead_level`, `sorelia_debtors→sorelia_leads` — the dropped columns are UNRECOVERABLE so `pg_dump` the three columns before DROP). Enumerate active schemas first; dropped data backed up.

## 5. Circular-Dependency Analysis (UPDATED — leads edge gone)

**Violation #1 — tenancy → conversation (REAL).** `tenant_loader` imports `core.responses.ResponsesSpec`. **Resolution:** `ResponsesSpec`→`tenancy/responses_spec.py`; engine imports it (feature→tenancy allowed). Test re-map: `test_responses_engine.py`, `test_delivery_info.py`.

**Violation #2 — shared → features (DISSOLVED to a non-issue).** `state.py`+`redis_store.py` import `hooks` (conversation) and `lead_machine` (was "leads"). Since leads is dissolved INTO conversation, both `hooks` and `debtor_state` now live in `features/conversation/`. Moving `state.py`+`redis_store.py` into `features/conversation/persistence/` makes ALL their imports intra-feature (conversation→conversation) + `shared/persistence` (db). **No port needed.** `shared/persistence/` keeps only `persistence.py`+`db.py`.

**Violation #3 — cobranza → comprobantes (split-introduced).** `certificate_pdf` + `email_delivery` shared by both. **Resolution:** → `shared/delivery/`. Both features import from shared (allowed).

**Removed edge:** the prior `conversation→leads` edge (`agent.py`→`prospect_profile`, `state.py`→`lead_machine`) is GONE — both targets are now conversation modules (`debtor_profile`, `debtor_state`). No `shared/ports/lead_port.py` needed. This SIMPLIFIES the graph vs the previous design.

**Non-violations:** conversation→cobranza only via `ToolRegistry` in `api/tool_registry.py` (composition root, may import all features). `agent.py` receives the registry by injection (it takes collaborators already) — it does NOT import `api/`. Verify the registry type hint doesn't force a feature→api import; if it does, put the protocol in `shared/ports/tool_registry.py`.

## 6. Import Strategy
Keep absolute imports. Re-map prefixes: `core.llm`→`shared.llm`; `core.persistence`/`db`→`shared.persistence`; `core.rate_limit`/`webhooks`/`webhook_config`→`shared.*`; `config.settings`/`cors`/`tools_schema`→`shared.config.*`; `config.soul`/`pricing`/`tenant_loader`→`tenancy.*`; `core.responses` engine→`features.conversation.responses`, `ResponsesSpec`→`tenancy.responses_spec`; `core.agent`/`response_builder`/`response_guard`/`conversation_fsm`/`hooks`→`features.conversation.*`; `core.lead_machine`→`features.conversation.debtor_state`; `core.prospect_profile`→`features.conversation.debtor_profile`; `core.state`/`redis_store`/`visitor_memory`→`features.conversation.persistence.*`; `prompts.system`→`features.conversation.prompts`; `tools.cobranza`→`features.cobranza.tools` (+comprobante symbols→`features.comprobantes.validator`); `tools.ToolRegistry`→`api.tool_registry`; `integrations.debt_source`/`doris`/`mock`→`features.cobranza.*`; `integrations.chathub_*`/`whatsapp_*`→`features.messaging.*`; `integrations.certificate_pdf`→`shared.delivery.certificate_pdf`; `core.email_service`→`shared.delivery.email_delivery`; `integrations.analytics_sink`→`features.analytics.analytics_sink`; `api.dashboard`→`features.analytics.dashboard`; `api.chathub`→`api.routers.chathub`.
pythonpath stays `["apps/agent"]`. Add `__init__.py` to every new package. 18 test files: import re-map + the rename re-maps (`LeadMachine`→`DebtorState`, `INTEREST_FIELDS` path, `lead_state`→`debtor_state` kwargs in ~17 call sites, ResponsesSpec path).

## 7. Per-Slice Execution Plan (outside-in; rename+migration isolated)

| # | Slice | Content | God-file / rename | Shippable |
|---|-------|---------|-------------------|-----------|
| 0 | Scaffolding | Create `features/{conversation,cobranza,comprobantes,messaging,analytics}/`, `shared/{delivery,ports?}`, `tenancy/`, `api/routers/` + `__init__.py`. No moves. | none | Yes (no-op) |
| 1 | shared/ kernel | llm, persistence(persistence+db), rate_limit, webhooks, webhook_config, config(settings+cors+tools_schema). | none | Yes |
| 2 | tenancy/ | tenant_loader, soul, pricing + `ResponsesSpec`→`responses_spec.py` (cycle #1). | ResponsesSpec extract | Yes |
| 3 | analytics/ | **FIRST: consumer mapping (§4c) — DONE.** analytics_sink → `features/analytics/`. dashboard → `features/analytics/dashboard.py`; rename query strings to new table/columns (`sorelia_debtors`, `debtor_level`) + drop dead `CONTACT/QUALIFIED` filters. NOTE: SQL string rename here must land TOGETHER with the storage migration (slice 9) since `RENAME` is not dual-read-safe — keep the dashboard SQL change in slice 9's atomic deploy, not slice 3. Slice 3 only MOVES the file + maps imports (SQL unchanged). | none | Yes |
| 4 | comprobantes/ + shared/delivery | certificate_pdf + email_delivery→`shared/delivery/`; carve `comprobantes/validator.py` from tools/cobranza (§3.B). | tools/cobranza carve | Yes |
| 5 | messaging/ | whatsapp_service, whatsapp_formatter, chathub_adapter/outbound/web_publisher. | none | Yes |
| 6 | cobranza/ | tools.py remainder, debt_source, doris, mock. | finalize §3.B | Yes |
| 7 | conversation/ (+ DELETE opportunity_detector, §3.D) | agent, responses(engine), response_builder, response_guard, conversation_fsm, hooks, prompts, skills; lead_machine→debtor_state, prospect_profile→debtor_profile; state/redis_store/visitor_memory→conversation/persistence. DELETE opportunity_detector + opportunities field. | none (responses whole) | Yes |
| 8 | **lead→debtor RENAME (code+tool, NO storage)** | Apply §4a code rename + §3.E tool rename across all moved modules; update tests (kwargs, imports, new tool-name assertion). EXCLUDE §4b. | semantic rename | Yes |
| 9 | **STORAGE rename + data migration (RISKIEST — ATOMIC deploy)** | ALL of §4d as ONE atomic deploy because `RENAME`/`DROP` is not dual-read-safe: (a) additive `debtor_data` column + dual-read in persistence; (b) `sorelia_leads→sorelia_debtors`, `lead_level→debtor_level`, enum value remap, DROP `district_interest/purpose/budget`; (c) `upsert_lead→upsert_debtor`; (d) dashboard SQL strings updated to new table/columns + dead-filter removal; (e) redis write `:debtor_data` + fallback. Migration script with PREFLIGHT + pg_dump of dropped columns. | DDL + migration + dashboard SQL | Yes (gated by preflight) |
| 10 | api/ thin + main.py split | ToolRegistry→`api/tool_registry.py`; chathub→routers; split main.py (§3.A); delete empty `core/`,`config/`,`tools/`,`integrations/`,`prompts/`. | main.py split (biggest) | Yes (final) |

Slices 9 and 10 likely exceed 400 changed lines → chained PRs per `delivery_strategy`. Slice 8 (pure code rename) is large by ref-count but mechanical; keep it its own PR so review is "rename only". Slice 9 must NOT be bundled with 8 — storage/migration has independent rollback semantics AND must ship atomically (code+migration+dashboard SQL together).

**Rollback:** each slice = atomic commit, `git revert`. Slice 9 rollback = revert code + run the reverse migration (`debtor_level→lead_level`, `sorelia_debtors→sorelia_leads`); `debtor_data` additive (safe); DROPPED columns restored from the pre-migration `pg_dump`.

## 8. Risks (architectural)
| Risk | Mitigation |
|------|------------|
| Storage migration data loss / partial per-tenant | Additive `debtor_data` + dual-read; `lead_data` retained; preflight COUNT per schema; idempotent guarded UPDATE |
| Redis stale `:lead_data` during rollout | Fallback-read for ≤24h TTL window; removed in cleanup release |
| Tool-name change breaks LLM prompts/skills | grep `get_lead_status` in all SKILL.md + prompts; rename consistently; lock with a test |
| Removing `opportunities` line changes a snapshot | grep "Oportunidades de extraccion" fixtures; none found — re-verify in apply |
| main.py split (slice 10) behavior drift | Characterization tests before move; chained PRs |
| `lead_level→debtor_level` enum + value remap breaks dashboard `/stats` funnel | Dead `CONTACT/QUALIFIED` filters always counted 0; remap `PRE_LEAD/LEAD/LEAD_ENRICHED`; confirm frontend tolerates funnel shape |
| `RENAME TABLE/COLUMN` + `DROP COLUMN` not dual-read-safe → brief dashboard 500s on deploy | Atomic code+migration deploy (slice 9); `sorelia_debtors` is dashboard-only (no hot-path), seconds of downtime tolerable — confirm with Ricky |
| Dropped columns (`district_interest/purpose/budget`) unrecoverable | `pg_dump` the 3 columns before DROP; reverse migration documented |
| Out-of-repo ETL reading `{schema}.sorelia_leads` PG→BigQuery | BLOCKER if exists — cannot verify from repo; flag for human confirmation |
| 291 refs bulk rename over-reaches into EXCLUDED strings | Filter `website_leads_only`/`webhook_lead_url`/`lead_transition_url` before sed; verify diff |

## 9. Success Criteria
- Full suite green after every slice and at end.
- `core/` gone; five features + `shared/ tenancy/ api/` in place; dependency rules hold (no `shared→features`, no `tenancy→features`, no direct `feature→feature`; conversation→leads edge eliminated).
- `api/main.py` ≤ ~150; 3 god files split.
- `lead`→`debtor` rename complete in code+tool+storage INCLUDING sorelia heritage: `sorelia_leads→sorelia_debtors`, `lead_level→debtor_level` (values remapped), `upsert_lead→upsert_debtor`, dashboard SQL updated, dead columns dropped. EXCLUDE list (§4b) untouched.
- Migration applied with preflight + pg_dump backup; `debtor_data` additive for safe rollback.
- Zero behavior change except: the tool-name change (tested), the migration (correctness-preserving), and the removal of always-zero dashboard funnel filters (`CONTACT/QUALIFIED`).

## 10. Open Questions
- [ ] **`project_interest` column kept** (LIVE in dashboard `/stats top_projects`, `/conversations`) — it is a real-estate concept in a cobranza bot. Dissolve later with a dashboard redesign? (out of scope now — dropping it breaks `/stats`).
- [ ] **Out-of-repo ETL**: confirm NO scheduled job / dashboard-builder pulls `{schema}.sorelia_leads` from Postgres directly into BigQuery gold tables. Repo-internal sinks (Doris `bot_*`) are unaffected, but a PG→BQ ETL outside this repo would be a BLOCKER. Needs human confirmation (Angeles/Paola pipeline).
- [ ] **Deploy downtime tolerance**: slice 9's `RENAME/DROP` cannot be dual-read; accept brief dashboard 500s during atomic deploy, or schedule a maintenance window? (`sorelia_debtors` is dashboard-only, no chat hot-path).
- [ ] Confirm no tenant `tenant.config.json` references `get_lead_status` by name (skills override).
