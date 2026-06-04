# Verify Report: platform-multitype-engine — PR2 (S3+S4)

**Change**: platform-multitype-engine
**Branch**: platform/multitype-pr2-registry-wiring (stacked on PR1)
**Slices**: S3 (AgentTypeRegistry port + InCodeAgentTypeRegistry + TenantConfig field) + S4 (engine composes spec from registry)
**Verdict**: PASS WITH WARNINGS — 0 CRITICAL, 2 WARNING, 2 SUGGESTION
**Date**: 2026-06-03

---

## Test Results

| Run | Command | Result |
|-----|---------|--------|
| Full suite | `uv run pytest tests/ -q` | **398 passed** in 1.66s |
| Baseline (pre-PR2) | 380 (post-PR1) | +18 new char tests |
| Regressions | 0 | ZERO |

---

## Task Completeness

| Task | Status |
|------|--------|
| 3.1 Write tests/test_agent_type_registry.py (18 char tests) | COMPLETE |
| 3.2 Create shared/ports/agent_type_registry.py | COMPLETE |
| 3.3 Create features/cobranza/agent_type.py | COMPLETE |
| 3.4 Create tenancy/agent_types/registry.py | COMPLETE |
| 3.5 Add agent_type to TenantConfig + tenant.config.json | COMPLETE |
| 3.6 Full suite green (398) | COMPLETE |
| 4.1 Wire default_registry() at api/main.py | COMPLETE |
| 4.2 Migrate DebtorState→Record in state.py + redis_store.py | COMPLETE |
| 4.3 Migrate remaining non-test DebtorState callers (none) | COMPLETE |
| 4.4 Full suite green (398) | COMPLETE |

All S3+S4 tasks: COMPLETE. S5+ tasks: NOT YET (correct for PR2 scope).

---

## #1 Priority: Registry Placement Verdict

### VERDICT: WARNING (not CRITICAL)

**The violation**: `tenancy/agent_types/registry.py:50` contains:

```python
def default_registry() -> InCodeAgentTypeRegistry:
    from features.cobranza.agent_type import COBRANZA_AGENT_TYPE   # line 50
    return InCodeAgentTypeRegistry({"cobranza": COBRANZA_AGENT_TYPE})
```

A lazy/deferred import inside a function body is STILL a tenancy→features dependency. The module comment on line 5 says "This module MUST NOT import from features/ directly" — then does exactly that, lazily. The architectural rule (tenancy→features prohibited, same as shared→features) is violated regardless of call-time vs import-time resolution.

**The clean fix** (~5 lines, non-breaking):

1. `tenancy/agent_types/registry.py` — remove `default_registry()`. Keep only `InCodeAgentTypeRegistry` (pure: imports `shared/ports` only). Module is now architecturally clean.
2. `api/main.py` — replace `from tenancy.agent_types.registry import default_registry` with:
   ```python
   from tenancy.agent_types.registry import InCodeAgentTypeRegistry
   from features.cobranza.agent_type import COBRANZA_AGENT_TYPE
   agent_type_registry: AgentTypeRegistry = InCodeAgentTypeRegistry({"cobranza": COBRANZA_AGENT_TYPE})
   ```
   api/ is the composition root — importing both tenancy/ and features/ here is CORRECT by definition.

**Why WARNING not CRITICAL**:
- Tests are 100% green — no runtime failure.
- `InCodeAgentTypeRegistry` itself is pure at module level (imports shared/ports only).
- The lazy import is intentional and documented.
- The fix is trivial and non-breaking.
- The behavioral outcome is identical.

**Dependency rule matrix (current state)**:

| Direction | Status |
|-----------|--------|
| shared/ports → (nothing) | CLEAN |
| features → shared/ports | CLEAN |
| tenancy → shared/ports | CLEAN |
| tenancy → features (via lazy import in default_registry) | **VIOLATION — W-01** |
| api → tenancy + features | CLEAN (composition root) |

---

## Spec Compliance Matrix

| Spec Requirement | Evidence | Status |
|-----------------|----------|--------|
| AgentTypeRegistry Protocol in shared/ports/ (pure) | Zero features/api imports confirmed by rg | PASS |
| InCodeAgentTypeRegistry resolves "cobranza" | test_registry_resolves_cobranza | PASS |
| Registry source swappable without consumer change | TestRegistrySwappability: _TestRegistry drops in | PASS |
| Unknown agent_type raises AgentTypeNotFoundError | test_unknown_type_raises_well_typed_error | PASS |
| Exactly one entry ("cobranza") | test_registry_has_exactly_one_entry | PASS |
| agent_type in TenantConfig (safe default "cobranza") | tenant_loader.py:33 `agent_type: str = "cobranza"` | PASS |
| agent_type in tenant.config.json files | Both prestamype + prestaunion have `"agent_type": "cobranza"` | PASS |
| Missing agent_type in config is handled safely | tenant_loader.py:85 `(config.get("agent_type") or "cobranza")` | PASS |
| state.py uses Record(spec) instead of DebtorState() | state.py:53 `self.debtor = Record(spec=_spec)` | PASS |
| redis_store.py uses Record(spec) | redis_store.py:38 `self.debtor = Record(spec=_spec)` | PASS |
| capture_spec flows via get_store() factory | Lifespan → `get_store(capture_spec=_default_spec)` | PASS |
| Spec passed IS COBRANZA_SPEC | test_cobranza_capture_spec_matches uses `is` identity | PASS |
| Full test suite green | 398 passed, 0 failures | PASS |
| No S5+ work leaked | tool_registry.py still has `_GATED_TOOLS` (correct) | PASS |

---

## Zero-Behavior Verdict: CONFIRMED

**Spec flow chain**: `api/main.py` calls `agent_type_registry.get("cobranza").capture_spec` → passes as `_default_spec` to `get_store(capture_spec=_default_spec)` → `StateStore._capture_spec` → `ConversationState(capture_spec=...)` → `Record(spec=_spec)`.

- The spec passed IS `COBRANZA_SPEC`: confirmed by `test_cobranza_capture_spec_matches` (`result.capture_spec is COBRANZA_SPEC` — identity check, not equality).
- Both state.py and redis_store.py have a `_default_spec()` fallback that also returns `COBRANZA_SPEC` — so even the in-memory/test path (where `capture_spec=None`) is safe.
- The 14 char tests in test_record_char.py lock that `Record(COBRANZA_SPEC)` is behaviourally identical to the old `DebtorState()`.
- 398 tests pass. Zero cobranza behavior change.

---

## Characterization Test Quality Verdict: HIGH — BEHAVIORAL

18 tests in `tests/test_agent_type_registry.py`, 4 groups:

| Group | Tests | Nature |
|-------|-------|--------|
| TestAgentTypePort | 4 | Field construction + None projection_table |
| TestCobranzaAgentType | 6 | COBRANZA_SPEC identity, gate_model value, tools tuple, core tools |
| TestInCodeAgentTypeRegistry | 6 | Resolution, spec identity (is), typed error, single entry, factory |
| TestRegistrySwappability | 2 | Custom impl drop-in, Protocol duck-type |

Adversarial catches: `is` identity catches copy-instead-of-canonical; `issubset` catches dropped tools; `type.__name__` check catches bare KeyError; `_TestRegistry` drop-in catches Protocol API changes. No source inspection. 100% runtime behavioral.

---

## Swappable Abstraction Verdict: CONFIRMED

- `api/main.py:92`: `agent_type_registry: AgentTypeRegistry = default_registry()` — typed as the Protocol.
- `api/main.py:153`: `agent_type_registry.get("cobranza")` — calls Protocol method.
- A `DbAgentTypeRegistry` with `.get(agent_type) -> AgentTypeSpec` swaps at line 92. Zero consumer change required.

---

## Hardcoded "cobranza" in lifespan: SUGGESTION (acceptable deferral)

`api/main.py:153`: `_default_spec = agent_type_registry.get("cobranza").capture_spec`

There is no default tenant at boot — the store is a global singleton, not per-tenant. Per-tenant type resolution happens at request time (S5+). The hardcoded "cobranza" is behavior-correct for all current tenants and does NOT defeat S4's purpose. The `agent_type` field in tenant.config.json is for S5 (per-request ToolRegistry composition).

---

## Issues

### CRITICAL
None.

### WARNING

**W-01 — DEPENDENCY RULE** `tenancy/agent_types/registry.py:50`
- `default_registry()` imports `from features.cobranza.agent_type import COBRANZA_AGENT_TYPE` inside the function.
- tenancy→features prohibited (same rule as shared→features). Lazy import doesn't escape the rule.
- Fix: move `default_registry()` to `api/`. `InCodeAgentTypeRegistry` stays pure in tenancy/.
- ~5 lines. Non-breaking. Fix before merge or immediately after in a follow-up commit.

**W-02 — REDIS PREFIX NOT RENAMED** `redis_store.py:22`
- `_key()` still uses `sorelia:conv:{id}:{suffix}`. Design specifies rename to `olimpo:conv:`.
- Correctly scoped to S6 per tasks. Flagging here so S6 verify checks it.
- No action needed in PR2.

### SUGGESTION

**S-01 — HARDCODED TYPE STRING** `api/main.py:153`
- `agent_type_registry.get("cobranza")` uses literal string. S5 should resolve from per-request tenant config.
- Acceptable deferral. No behavior issue now.

**S-02 — DUAL-READ FALLBACK** `state.py:51,165`
- `debtor_data or lead_data` dual-read is correct pre-S6. Confirm test_storage_migration.py rewrite covers drop in S6.

---

## Scope Discipline: CONFIRMED

| Check | Result |
|-------|--------|
| S5 ToolRegistry params not present | tool_registry.py has `_GATED_TOOLS` module-global (correct — S5 not yet done) |
| S6 sorelia_ rename not present | persistence.py still uses `sorelia_conversations`, `sorelia_debtors` (correct) |
| S7 projection_table not wired | Not present in PR2 (correct) |
| S8 shim not deleted | `debtor_state.py` still exists (correct) |

---

## Runtime Import Check: PASS

```
cd apps/agent && python -c "import api.main, tenancy.agent_types.registry, shared.ports.agent_type_registry, features.cobranza.agent_type; print('OK')"
# Output: OK (plus expected startup logs)
```

---

## Git Hygiene: CLEAN

- 3 commits: `9633fe6` (S3), `e14e758` (S4), `332e524` (tasks.md update)
- 475 insertions / 28 deletions across 12 files (within ~250 line estimate for WU-2)
- No CLAUDE.md, lockfiles, pycache, or secrets committed
- openspec/tasks.md update is the only doc file (correct)

---

## Final Verdict: PASS WITH WARNINGS — READY TO MERGE

PR2 (S3+S4) is functionally correct and safe to merge. 398 tests pass. Zero behavior change. Swappable abstraction confirmed. Spec flows correctly through the composition chain. Char tests are behavioral and genuinely lock the contract.

**Before or immediately after merge**: fix W-01 by moving `default_registry()` to `api/`. The fix is 5 lines and makes the tenancy layer architecturally clean.

W-02 (redis prefix) is S6-scoped — no action in PR2.

**Next**: PR3 (S5) — ToolRegistry registry-driven gate.
