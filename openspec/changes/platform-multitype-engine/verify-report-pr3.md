# Verify Report: platform-multitype-engine — PR3 (S5)

**Change**: platform-multitype-engine
**Branch**: platform/multitype-pr3-toolregistry-driven
**Slice**: S5 — ToolRegistry registry-driven gate + per-domain gate
**Verdict**: PASS — 0 CRITICAL, 0 WARNING, 2 SUGGESTION
**Date**: 2026-06-03
**Reviewer mode**: FRESH adversarial review
**Stacked on**: PR1 (PASS) + PR2 (PASS WITH WARNINGS — W-01 resolved in 0ff579b)

---

## Test Results

| Run | Command | Result |
|-----|---------|--------|
| Full suite | `uv run pytest tests/ -q` | **418 passed** in 1.66s |
| Baseline (pre-PR3) | 398 (post-PR2) | +20 new char tests |
| Regressions | 0 | ZERO |

---

## Task Completeness (S5)

| Task | Status |
|------|--------|
| 5.1 CHAR-TEST: tests/test_tool_registry.py (20 tests, RED first) | COMPLETE |
| 5.2 gated_tools param + _DEFAULT_GATED_TOOLS fallback + self._gated_tools | COMPLETE |
| 5.3 tools param + _tools filtering | COMPLETE |
| 5.4 Composition roots pass spec.gated_tools + spec.tools | COMPLETE |
| 5.5 Full suite 418 passed | COMPLETE |

S6+ tasks: NOT YET — correct for PR3 scope.

---

## GATE ZERO-BEHAVIOR (Axis A — Primary Check)

Pre-PR3 `_GATED_TOOLS` (git show platform/multitype-pr2-registry-wiring:apps/agent/api/tool_registry.py):

```python
_GATED_TOOLS = {
    "consultar_deuda", "registrar_reclamo", "emitir_certificado_no_adeudo",
    "enviar_documento", "enviar_info", "validar_comprobante",
}
```

Post-PR3 `COBRANZA_GATED_TOOLS` (features/cobranza/agent_type.py:35) and `_DEFAULT_GATED_TOOLS` (api/tool_registry.py:43):

```python
frozenset({
    "consultar_deuda", "registrar_reclamo", "emitir_certificado_no_adeudo",
    "enviar_documento", "enviar_info", "validar_comprobante",
})
```

**VERDICT: GATE ZERO-BEHAVIOR CONFIRMED.** Byte-for-byte identical: 6 tools, same names. `identificar_cliente` absent from all three. Locked by parametrized char tests `test_gated_tool_blocked_without_identity` (6 tests) and `test_ungated_tool_not_blocked_without_identity` (6 tests).

---

## TOOL SURFACE ZERO-BEHAVIOR (Axis B — Primary Check)

Pre-PR3 `_all_tools` keys: 12 tools — `get_debtor_status`, `navigate_page`, `suggest_quick_replies`, `collect_contact_info`, `identificar_cliente`, `consultar_deuda`, `registrar_reclamo`, `emitir_certificado_no_adeudo`, `enviar_documento`, `enviar_info`, `validar_comprobante`, `escalate_to_human`.

Post-PR3 `COBRANZA_TOOLS` (features/cobranza/agent_type.py:16): same 12 tools in same order.

When routers pass `tools=_agent_spec.tools` (= COBRANZA_TOOLS = all 12), the filtering at tool_registry.py:147 produces `_tools = _all_tools` (full set). Net effect: identical to pre-PR3 `tools=None` path.

**VERDICT: TOOL SURFACE ZERO-BEHAVIOR CONFIRMED.** No tool dropped. No tool added. `test_tools_param_full_cobranza_set_all_present` locks the full 12-tool surface.

---

## _DEFAULT_GATED_TOOLS Fallback Verdict

```python
# api/tool_registry.py:123
self._gated_tools: frozenset[str] = (
    gated_tools if gated_tools is not None else _DEFAULT_GATED_TOOLS
)
```

- All 3 production routers pass `spec.gated_tools` → fallback NEVER fires in production.
- Fallback fires only in unit tests constructing `ToolRegistry()` without args.
- `execute()` at line 161 uses `self._gated_tools` exclusively — module-level constant never read at runtime.

**Verified routers:**
- `conversations.py:269`: `gated_tools=_agent_spec.gated_tools` (from `m.agent_type_registry.get(_agent_type)`)
- `webhooks.py:268`: `gated_tools=_wa_agent_spec.gated_tools` (from `m.agent_type_registry.get("cobranza")`)
- `chathub.py:255`: `gated_tools=_chathub_agent_spec.gated_tools` (from `m.agent_type_registry.get(tenant_config.agent_type or "cobranza")`)

**VERDICT: FALLBACK CORRECTLY CONSTRAINED.**

---

## Characterization Test Quality

**20 tests in tests/test_tool_registry.py — HIGH quality, behavioral, adversarially strong.**

| Group | Tests | Behavioral | Gate contract |
|-------|-------|-----------|---------------|
| `test_gated_tool_blocked_without_identity` | 6 | execute() → checks blocked | Locks each of 6 gated tools individually |
| `test_ungated_tool_not_blocked_without_identity` | 6 | execute() → checks NOT blocked | Locks identificar_cliente + 5 generic |
| Custom/empty/default gate param tests | 3 | execute() on constructed registries | Tests gate override paths |
| tools param tests | 2 | has_tool() checks | Locks tool surface |
| Spec integration tests | 3 | spec equality, containment, parallel execution | Locks COBRANZA_AGENT_TYPE contract |

Tests would catch: adding/removing any tool from COBRANZA_GATED_TOOLS (equality test breaks), changing tool surface (has_tool test breaks), spec-driven path diverging from default (parallel execution test breaks).

---

## gate_model: Consumed vs Decorative

`gate_model="hard_dni"` is stored in `AgentTypeSpec` and `COBRANZA_AGENT_TYPE`. Referenced in ToolRegistry docstring. **NOT consumed by any conditional logic** — gate operates purely on `gated_tools` frozenset.

This is by design for the current slice (only one gate model exists). Architecture is forward-compatible: `gate_model="none"` with `gated_tools=frozenset()` already produces open-gate behavior. `gate_model` would be consumed when a future model requires different logic, not just a different tool set.

---

## Dependency Check

- `shared/ports/agent_type_registry.py`: zero `from features/api/tenancy` imports — PURE.
- W-01 from PR2 (tenancy→features in default_registry()): resolved in commit `0ff579b` before PR3 — clean.
- No new layer violations in PR3.
- `api/tool_registry.py` in `api/` imports `features/` — allowed.

---

## Scope Discipline

- `sorelia_*` table names still in persistence.py, visitor_memory.py, dashboard.py — S6 scope, correct.
- `debtor_state.py` shim not deleted — S8 scope, correct.
- No projection-table creation — S7 scope, correct.
- PR3 touches exactly 8 files within S5 scope.

---

## Runtime Import Check

```
python -c "import api.main, api.tool_registry, api.routers.conversations, api.routers.webhooks, api.routers.chathub"
```
**Result: OK** — clean startup.

---

## Git Hygiene

- 1 commit: `0f7ca34 feat(registry): make ToolRegistry registry-driven + per-domain gate (S5)`
- 8 files changed: 277 insertions, 12 deletions
- No CLAUDE.md, lockfiles, pycache, secrets.

---

## Issues

### CRITICAL
None.

### WARNING
None.

### SUGGESTION

- **S-03**: `test_tool_registry.py:131` — typo `"navegate_page_fake"` (should be `"navigate_page_fake"`). No functional impact.
- **S-04**: `gate_model` stored but not consumed. Add a `# NOTE: consumed when second gate model added` comment to ToolRegistry constructor for future clarity.

---

## Final Verdict

**PASS — 0 CRITICAL, 0 WARNING, 2 SUGGESTION**

**READY TO MERGE.**

All zero-behavior axes confirmed against git history. 418 tests green. Char tests are behavioral and adversarially strong. Dependency rules clean. Scope discipline clean.

Next: PR4 (S6+S7) — Persistence neutral names — DEPLOY UNIT. Requires explicit Ricky go before apply.
