# Tasks: prestamype-doris-prod

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | 220–320 (code+Dockerfile+configs+tests) |
| 400-line budget risk | Medium |
| Chained PRs recommended | Yes |
| Suggested split | PR 1 (code track: validation+fall-through+flag+Dockerfile+tests) → PR 2 (ops: config flip after gates) |
| Delivery strategy | ask-on-risk |
| Chain strategy | stacked-to-main |

Decision needed before apply: Yes
Chained PRs recommended: Yes
Chain strategy: stacked-to-main
400-line budget risk: Medium

### Suggested Work Units

| Unit | Goal | Likely PR | Notes |
|------|------|-----------|-------|
| 1 | Format validation + fall-through fix + flag reader + Dockerfile smoke + all unit tests | PR 1 → main | TDD-safe, no prod impact; data_source stays mock |
| 2 | Config flip prestamype data_source→doris after B1+B2+B3 gates pass | PR 2 → main | BLOCKED until gates cleared; Ricky go required |

---

## Phase 1: Foundation — Config & Flag Files

- [x] 1.1 `tenants/prestamype/tenant.config.json` — add `"allow_fixture_fallback": false` (do NOT change `data_source` yet)
- [x] 1.2 `tenants/prestaunion/tenant.config.json` — add `"allow_fixture_fallback": true` (preserves demo behavior)
- [x] 1.3 `infrastructure/docker/Dockerfile.agent` — after `uv sync --frozen` line, add `RUN uv run python -c "import pymysql"` smoke gate

---

## Phase 2: Core Implementation — Code Track (RED first, then GREEN)

### 2a. DNI/RUC Format Validation in `_identificar_cliente`

- [x] 2.1 **RED** — `tests/test_dni_format_validation.py`: wrote 12 failing tests covering invalid_format cases (hola, 4-digit, 9-digit, empty, whitespace, 14-digit) + valid cases (8-digit, 11-digit, dots-normalized, spaces-normalized). Assert attempt counter NOT called on invalid inputs.
- [x] 2.2 **GREEN** — `apps/agent/api/tool_registry.py` `_identificar_cliente`: added normalize (`_DNI_CLEAN_RE.sub`) then length check (len∈{8,11}); on fail returns `{identified: False, reason: "invalid_format", message: "Necesito tu DNI (8 dígitos) o RUC (11). ¿Me lo confirmas?"}` BEFORE `_on_identification_attempt` and BEFORE `resolve_dni`. All 12 tests green.

### 2b. Doris Fall-Through Fix in `_resolve_dni_credits`

- [x] 2.3 **RED** — `tests/test_doris_fallthrough.py`: wrote 8 failing tests: (a) Doris OK+empty→[], fixture NOT called; (b) Doris OK+rows→mapped profiles, fixture NOT called; (c) Exception+flag=True→fixture; (d) Exception+flag=False→[], fixture NOT called.
- [x] 2.4 **GREEN** — `apps/agent/features/cobranza/doris_debt_source.py` `_resolve_dni_credits`: restructured to try/except; except branch: `if _allow_fixture_fallback(tenant_id): fixture else []`; happy path: `[_row_to_profile(r) for r in rows]`. All tests green.

### 2c. Per-Tenant Flag Reader

- [x] 2.5 **RED** — added test cases for `_allow_fixture_fallback`: prestamype→False, prestaunion→True, unknown tenant→False. Also lru_cache bleed test.
- [x] 2.6 **GREEN** — `apps/agent/features/cobranza/doris_debt_source.py`: added `_allow_fixture_fallback(tenant_id) -> bool` with `@lru_cache(maxsize=16)`, reads `tenant.config.json` directly (mirrors `_load_schema`), key `allow_fixture_fallback`, default `False`. Cache bleed: tests call `cache_clear()` before/after.

---

## Phase 3: Regression Guard

- [x] 3.1 Full suite ran: 445 passed (424 baseline + 21 new). Zero regressions. prestaunion mock path confirmed unchanged.
- [x] 3.2 Added `test_identificar_cliente_doris_down_flag_false_returns_safe_message` asserting identified=False + non-empty neutral message + no internal detail (Doris/Connection/Exception strings absent).

---

## Phase 4: Deploy Track — Gated on B1+B2+B3 (HARD GATES)

> **These tasks MUST NOT be checked until all three gates below are confirmed by Ricky.**

### Gate B1 — pymysql importable in rebuilt image
- [x] 4.1 **GATE B1** — rsync repo to `automation.mibot.cl`; rebuild image; run `docker exec <container> python -c "import pymysql"`; confirm exit 0. BLOCKS 4.3+.

### Gate B2 — Doris connection succeeds
- [x] 4.2 **GATE B2** — inside rebuilt container, run throwaway connect script: `pymysql.connect(...)` → `SELECT 1 FROM batch_asignacion_review_bronze LIMIT 1`; confirm query returns rows. BLOCKS 4.3+.

### Gate B3 — Doris has prestamype data (canonical source verification)
- [x] 4.3 **GATE B3** — Ricky runs: `SELECT COUNT(*) FROM batch_asignacion_review_bronze WHERE <prestamype portfolio filter>`; confirms non-zero count is plausible for real debtors. BLOCKS 4.4+. (verify-source: `batch_asignacion_review_bronze` is the canonical source — query it, do NOT assume populated.)

### Post-gate flip (requires Ricky go)
- [x] 4.4 **FLIP** — `tenants/prestamype/tenant.config.json`: set `data_source: "doris"`, confirm `allow_fixture_fallback: false`. Rebuild image. Commit as separate PR 2.
- [x] 4.5 **SMOKE PROD** — real DNI from prestamype portfolio → `identified: True`; bogus DNI → `identified: False` (no fixture); garbage input (e.g. "hola") → `invalid_format`; (optional) Doris-down simulation → `identified: False`, no fixture.

### Rollback verification
- [x] 4.6 Confirm rollback path works: revert `data_source` to `"mock"` and `allow_fixture_fallback` to `true` in prestamype config → no rebuild required → mock source re-active.

---

## Parallel / Sequential Map

```
1.1 ─┐
1.2 ─┤─ can run in parallel
1.3 ─┘
     ↓
2.1 → 2.2  (format validation RED→GREEN)  ┐
2.3 → 2.4  (fall-through RED→GREEN)       ├─ 2a/2b/2c run in parallel per sub-track
2.5 → 2.6  (flag reader RED→GREEN)        ┘
     ↓
3.1 → 3.2  (regression guard, sequential)
     ↓ [PR 1 merged]
4.1 ─┐
4.2 ─┤─ parallel (both need rebuilt image)
     ↓
4.3  (sequential, Ricky confirms data)
     ↓
4.4 → 4.5 → 4.6  (sequential, gated)
```
