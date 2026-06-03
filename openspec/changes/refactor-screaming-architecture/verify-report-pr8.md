# Verify Report — PR8 / Phase 14
# Dependency Inversion (ToolRegistryPort + NullToolRegistry) + Dashboard HTTP Test

**Branch**: refactor/screaming-arch-pr8-toolregistry-di (stacked on PR7)
**Date**: 2026-06-03
**Verdict**: PASS — CLEAN (0 CRITICAL, 0 WARNING, 1 SUGGESTION)

---

## Test Results

```
366 passed in 1.57s
```

STRICT TDD gate: GREEN. +1 from PR7 baseline (new dashboard behavioral test).

---

## Full Dependency Matrix (Adversarially Verified)

| Direction | Grep Count | Verdict |
|---|---|---|
| shared → features | 0 real imports (2 docstring lines only) | PASS — ZERO violations |
| shared → api | 0 | PASS — ZERO violations |
| features → api | 0 | PASS — ZERO violations |
| tenancy → features | 0 | PASS — ZERO violations |
| cross-feature | 0 | PASS — ZERO violations |
| features → shared | OK (allowed) | — |
| features → tenancy | OK (allowed) | — |
| api → features | OK (allowed) | — |
| api → shared | OK (allowed) | — |
| tools layer | DISSOLVED — concrete ToolRegistry in api/tool_registry.py | — |

**Dependency matrix is 100% clean. No disallowed edges. PR8's whole purpose: achieved.**

---

## Port Purity

**PASS.** `apps/agent/shared/ports/tool_registry.py` imports ONLY stdlib:
- `from __future__ import annotations`
- `from typing import Protocol, runtime_checkable`

Zero imports from features/ or api/.

`ToolRegistryPort` is a `@runtime_checkable` Protocol with `has_tool(name) -> bool`
and `async execute(name, args) -> dict`.

`NullToolRegistry` is a genuine no-op:
- `has_tool` → always `False`
- `execute` → `{"error": "no tool registry configured"}`

`isinstance(NullToolRegistry(), ToolRegistryPort)` → `True` (confirmed at runtime).

---

## Structural Checks

| Check | Result |
|---|---|
| api/tool_registry.py exists | PASS |
| shared/tool_registry.py deleted | PASS — file gone, zero references remain |
| agent.py imports from shared.ports.tool_registry | PASS — line 18 |
| agent.py default = NullToolRegistry() | PASS — line 57: `self.tool_registry = tool_registry or NullToolRegistry()` |
| getattr(_identity_verified/_debt_context) safe against NullToolRegistry | PASS — getattr with False/None defaults, never raises |
| All api/routers + tests import from api.tool_registry | PASS — all updated |

---

## NullToolRegistry Default — Regression Hunt

**PASS — NO REGRESSION.**

The `test_analytics_doris` test creates `SoreliaAgent(provider=_UsageProvider())`.
`_UsageProvider.complete()` always returns `LLMResponse(tool_calls=[])` — zero tool
calls are emitted by the LLM mock. The agent never reaches `tool_registry.execute()`.

The `tools_called=["consultar_deuda"]` argument in that test is passed directly to
`analytics_sink.record_interaction()` as a raw data string — it is row data, not a
dispatch through the registry. The test asserts `result["tools_called"] == []`,
confirming the agent never called a tool.

No agent path that relied on a functional default ToolRegistry now silently degrades
to NullToolRegistry returning error dicts.

---

## Dashboard Test Quality

**PASS — BEHAVIORAL, NOT SOURCE INSPECTION.**

`test_dashboard_leads_reads_app_state_visitor_memory` in
`tests/test_api_endpoints_characterization.py:291`:

- Uses real `TestClient(m.app, raise_server_exceptions=False)` — actual HTTP stack.
- Sets `m.app.state.visitor_memory = _FakeVM()` (simulates a successful lifespan run).
- Hits `GET /api/v1/dashboard/leads` with auth header.
- Asserts `status_code == 503` with `"Database not available"` in detail.

The test distinguishes two failure modes:
- **Broken wiring** (reverted to module-global import) → `500 AttributeError` → test FAILS.
- **Correct wiring** (reads from `request.app.state`) → `503 HTTPException` → test PASSES.

This is genuine behavioral coverage that would catch any regression to the
`from api.main import visitor_memory` pattern that was PR7's WARNING-2.

---

## Zero-Behavior Verdict

**PASS.** The move to `api/tool_registry.py` was a mechanical `git mv`. Method
signatures (`has_tool`, `execute`), the identity gate, `_GATED_TOOLS` set, and all
dispatch logic are identical to the original `shared/tool_registry.py`. No tool
dispatch behavior was altered.

---

## Runtime Smoke

```
has_tool: False
isinstance ToolRegistryPort: True
agent.tool_registry type: NullToolRegistry
ALL IMPORTS OK
```

---

## Git Hygiene

**PASS.** 2 conventional commits on PR8:
- `76bf377` refactor(shared): invert ToolRegistry dep via Port (DI pattern)
- `b634753` test(dashboard): lock app.state.visitor_memory wiring via HTTP test

No CLAUDE.md, .env, pycache, or secrets in tracked files.

---

## Issues

### CRITICAL
None.

### WARNING
None. PR7 WARNING-1 (shared→features) CLOSED. PR7 WARNING-2 (dashboard HTTP test) CLOSED.

### SUGGESTION

**SUGGESTION-1**: `apps/agent/knowledge/` contains only `_schema.md` (domain schema
asset, not Python). Add a README.md to avoid orphan confusion in future maintenance.
Low priority, non-blocking.

---

## PR8 Purpose Verdict

The entire point of PR8 was a 100% clean dependency matrix via DI inversion.

**Result: 100% CLEAN. Every disallowed edge is ZERO. The Port lives in shared/ with
zero feature imports. The concrete impl lives in api/ where feature imports are
allowed. The agent depends on the Port, defaults to NullToolRegistry, and is
injectable from above. The matrix is proven, not assumed.**

---

## 8-PR Refactor — Final Archive Readiness

| Goal | Status |
|---|---|
| Screaming-arch layout | DONE |
| Dead code removal | DONE |
| Full lead→debtor rename | DONE |
| Prod-safe data migration | DONE |
| features→api violations | ZERO |
| shared→features violations | ZERO |
| Legacy dirs removed | DONE |
| ToolRegistry DI inversion | DONE |
| Dashboard HTTP behavioral test | DONE |
| Test suite | 366 passed — GREEN throughout |

**ARCHIVE READINESS: YES — unconditional. Ready for sdd-archive.**
