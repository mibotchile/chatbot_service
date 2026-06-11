# Tasks: tracking-intent-binding
# Bind Tracking Capability Catalog to Real Tenant Intents

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | 350–480 (catalog rewrite ~80, derivation ~60, wiring ~60, responses.json ~100, tests rewrite ~180) |
| 400-line budget risk | Medium–High |
| Chained PRs recommended | Yes |
| Suggested split | PR 1: catalog + derivation + tests (P1–P3) → PR 2: responses.json + wiring + integration (P4–P6) + template (P7) |
| Delivery strategy | ask-on-risk |
| Chain strategy | pending |

Decision needed before apply: Yes
Chained PRs recommended: Yes
Chain strategy: pending
400-line budget risk: High

### Suggested Work Units

| Unit | Goal | Likely PR | Notes |
|------|------|-----------|-------|
| 1 | Catalog vocabulary + accessor + derivation (pure logic, no tenant data) | PR 1 | Base: `feature/tracking-intent-binding`; self-contained, DB tests optional |
| 2 | responses.json annotation + wiring + integration + template | PR 2 | Base: PR 1 branch; requires GESTION_TEST_PG_DSN for integration tests |

---

## Phase 1 — Catalog Vocabulary (WI-1 → B1)

- [x] 1.1 [RED] `tests/test_gestion_catalog.py`: assert `INTENT_TO_CAPABILITY`, `TERMINAL_SIGNALS`, `INTENT_TO_REASON` do NOT exist on the module; assert `Capability`, `TerminalSignal`, `OutcomeReason` enums ARE present with correct members (per design enum lists); assert none of the 16 prestamype intent names appear as string literals in the module source.
  - Verify: `uv run pytest tests/test_gestion_catalog.py -v` (expect RED on existing code)

- [x] 1.2 [GREEN] `apps/agent/features/analytics/gestion_catalog.py`: remove `INTENT_TO_CAPABILITY`, `TERMINAL_SIGNALS`, `INTENT_TO_REASON` dicts; add `Capability(str, Enum)`, `TerminalSignal(str, Enum)`, `OutcomeReason(str, Enum)` with all members per design; keep `Outcome`, `EventType`, `SCHEMA_VERSION` untouched.
  - Verify: `uv run pytest tests/test_gestion_catalog.py -v` (expect GREEN)

---

## Phase 2 — Single Accessor (WI-3 → B3, B4)

- [x] 2.1 [RED] `tests/test_gestion_catalog.py`: add parametrized tests for `intent_binding(intent_name, responses_cfg)` — annotated intent resolves correct tuple (B2-1, B3-1, B3-2, B3-3); invalid `capability` value → coerced to `None` (not raises); `None` intent → `(None, None, None)`; `responses_cfg=None` → `(None, None, None)`.
  - Fixture: `ResponsesSpec` built inline with a dict of real intent keys (no file I/O).
  - Verify: `uv run pytest tests/test_gestion_catalog.py::test_intent_binding -v` (expect RED)

- [x] 2.2 [GREEN] `apps/agent/features/analytics/gestion_catalog.py`: implement `intent_binding(intent_name, responses_cfg)` per design contract; import `ResponsesSpec` under `TYPE_CHECKING`; coerce each field with the inner `_ok(val, enum)` helper; never raise.
  - Verify: `uv run pytest tests/test_gestion_catalog.py -v` (all GREEN)

---

## Phase 3 — Signal-Driven Derivation (WI-4 → B5)

- [x] 3.1 [RED] `tests/test_gestion_derivation.py`: rewrite tests using new `derive_outcome` signature `(*, session_state, resolved_intent, terminal_signal, was_escalated, identity_failed, escalation_reason)`; parametrize all 6 `TerminalSignal` values → expected `Outcome`; assert `identity_failed=True` takes priority over any `terminal_signal` (B5-5); assert `None` signal → `Outcome.unresolved` (B4-1); assert no reference to `TERMINAL_SIGNALS`/`INTENT_TO_REASON`.
  - Verify: `uv run pytest tests/test_gestion_derivation.py -v` (expect RED)

- [x] 3.2 [GREEN] `apps/agent/features/analytics/gestion_derivation.py`: update `derive_outcome` to new signature; implement priority-ordered signal dispatch per design table; remove all references to `INTENT_TO_CAPABILITY`, `TERMINAL_SIGNALS`, `INTENT_TO_REASON`; keep function pure (no I/O).
  - Verify: `uv run pytest tests/test_gestion_derivation.py -v` (all GREEN)

---

## Phase 4 — responses.json Annotation (WI-2 → B7)

- [x] 4.1 [GREEN] `tenants/prestamype/responses.json`: add `capability`, `terminal_signal`, `escalation_reason` fields to each of the 16 intents per the design annotation block; unannotated intents (`saludo`, `despedida`, `elegir_canal`) receive no new fields.
  - Verify: `python -c "import json; json.load(open('tenants/prestamype/responses.json'))" && echo OK`

---

## Phase 5 — Wiring Hook (WI-5 → B6, B3)

- [x] 5.1 [RED] `tests/test_gestion_wiring.py`: rewrite tests to use REAL prestamype intent names; provide a fixture `ResponsesSpec` loaded from a temp dir containing a minimal `responses.json` with annotated intents (or mock `ResponsesSpec.from_dir`); assert `_emit_gestion` with a terminal intent (`consulta_deuda`) produces `capabilities_used=["consulta_deuda"]` and `outcome="info_provided"` (B6-1, B7-1); assert unannotated `saludo` produces no capability entry (B6-2); assert multi-turn deduplicates `capabilities_used`.
  - Verify: `uv run pytest tests/test_gestion_wiring.py -v` (expect RED)

- [x] 5.2 [GREEN] `apps/agent/api/wiring.py`: update `_emit_gestion` to call `ResponsesSpec.from_dir(_tenant_dir(conv.tenant_id))`, resolve binding via `intent_binding(result.metadata.intent, spec)`, derive `is_terminal` from the resolved signal, call `derive_outcome(terminal_signal=..., escalation_reason=...)`, accumulate `capabilities_used` from each turn's binding (deduped, order-preserving); no fallback to old dicts.
  - Verify: `uv run pytest tests/test_gestion_wiring.py -v` (all GREEN)

---

## Phase 6 — Integration + Template (WI-6, WI-7 → B8, B9)

- [x] 6.1 [GREEN] `tenants/_template/responses.json`: create minimal file with one annotated intent example (e.g. `example_info_intent` with `capability`, `terminal_signal`) and one unannotated example; inline comments or a `_docs` key documenting the 3 binding fields and valid vocabulary values.
  - Verify: `python -c "import json; json.load(open('tenants/_template/responses.json'))" && echo OK`

- [x] 6.2 [RED] `tests/test_gestion_integration.py`: add/update end-to-end test driving a full conversation with real prestamype intents (`consulta_deuda`, `derivar_asesor`, `comprobante_resultado`); assert `gestiones` journal capabilities match `capabilities_used` from binding; assert `outcome` reflects correct terminal signal (B7-1, B7-2, B7-3, B7-4); requires `GESTION_TEST_PG_DSN`.
  - Verify: `GESTION_TEST_PG_DSN=postgresql://test:test@localhost:55432/test uv run pytest tests/test_gestion_integration.py -v` (expect RED)

- [x] 6.3 [GREEN] Wire integration test fixture so `ResponsesSpec.from_dir` resolves against the real `tenants/prestamype/` dir (not a mock) to confirm the annotated `responses.json` round-trips correctly.
  - Verify: `GESTION_TEST_PG_DSN=postgresql://test:test@localhost:55432/test uv run pytest tests/test_gestion_integration.py -v` (all GREEN)

---

## Phase 7 — Full Regression + Lint (WI-6 → B9)

- [x] 7.1 Run complete test suite; confirm zero failures, zero errors, zero new `mibotair_results` writes, zero `n1`/`n2`/`n3` column references in executed SQL (B9-1, B9-2).
  - Verify: `GESTION_TEST_PG_DSN=postgresql://test:test@localhost:55432/test uv run pytest tests/ -v`

- [x] 7.2 Run ruff on all touched files: `gestion_catalog.py`, `gestion_derivation.py`, `wiring.py`, and their test counterparts.
  - Verify: `uv run ruff check apps/agent/features/analytics/gestion_catalog.py apps/agent/features/analytics/gestion_derivation.py apps/agent/api/wiring.py tests/test_gestion_catalog.py tests/test_gestion_derivation.py tests/test_gestion_wiring.py tests/test_gestion_integration.py`

- [x] 7.3 Confirm non-gestion test count and pass rate unchanged vs pre-change baseline (B9-3).
  - Verify: `GESTION_TEST_PG_DSN=postgresql://test:test@localhost:55432/test uv run pytest tests/ -v --ignore=tests/test_gestion_catalog.py --ignore=tests/test_gestion_derivation.py --ignore=tests/test_gestion_wiring.py --ignore=tests/test_gestion_integration.py`
