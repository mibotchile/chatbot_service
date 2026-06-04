# Verification Report: platform-multitype-engine — PR5 (S8, Final)

> Branch: platform/multitype-pr5-remove-shim
> Commit: 7229821 feat(platform): delete DebtorState shim + close transient dependency (S8)
> Verifier: sdd-verify executor (claude-sonnet-4-6)
> Date: 2026-06-04
> Mode: Strict TDD

---

## 1. Test Results

| Command | Result |
|---------|--------|
| `uv run pytest tests/ -q` | **424 passed in 1.80s — PASS** |

Zero regressions. Contract met. Baseline was 423 (PR4); W1 adds exactly 1 new test.

---

## 2. Shim Deletion + Dependency Matrix

### Shim gone
- `apps/agent/features/conversation/debtor_state.py` — CONFIRMED DELETED (ls returns ENOENT).
- `git diff --name-only HEAD~1 HEAD` includes it as deleted.

### DebtorState references remaining
All surviving "DebtorState" strings are **comments/docstrings only** — no live import, no usage:
- `apps/agent/features/conversation/record.py:7,27,43` — docstring prose
- `apps/agent/features/cobranza/debtor.py:31` — comment
- `tests/test_record_char.py:4`, `tests/test_debtor_state_level.py:5` — module docstrings

**ZERO live DebtorState imports or instantiations anywhere.**

### Dependency matrix (final)

| Direction | Status | Evidence |
|-----------|--------|---------|
| features/cobranza → features/conversation (Record) | ALLOWED per design | debtor.py:13 — design Decision A sanctions this |
| features/conversation → features/cobranza | ZERO | grep returns empty |
| shared → features | ZERO | grep hits are docstring prose only |
| tenancy → features | ZERO | grep hit is a comment |
| features → api | ZERO | grep returns empty |
| api → features+tenancy+shared | ALLOWED | composition root only |

**Matrix is 100% clean.**

---

## 3. Zero-Behavior Verdict

- DebtorState shim was `Record(COBRANZA_SPEC)` — identical to what production uses directly post-PR2.
- Deleting it removes dead code; no production path calls it.
- **ZERO-BEHAVIOR: CONFIRMED.**

---

## 4. capture_spec=None Safety

- `ConversationState.__init__` (state.py:43-46): raises ValueError immediately when `capture_spec is None`.
- `RedisConversationState.__init__` (redis_store.py:30-33): same ValueError guard.
- `StateStore.__init__` (state.py:103-128): does NOT construct ConversationState eagerly — stores spec on self, constructs lazily only when `get_or_create` is called.
- `state.py:195 store = StateStore()`: constructs bare StateStore (no ConversationState) — SAFE at import time.
- `api/main.py:106 store = get_store()`: composition-root wrapper injects `_COBRANZA_SPEC` — SAFE.
- Lifespan replaces store with real DB pool + spec before any conversation is served.

**No production path constructs ConversationState with capture_spec=None. CONFIRMED SAFE.**

---

## 5. get_store() Wrapper in api/main.py

```python
# api/main.py:36-47
def get_store(redis_url=None, db_pool=None, db_schema="dev", capture_spec=None):
    """Composition-root wrapper: defaults capture_spec to COBRANZA_SPEC."""
    return _get_store_impl(
        ...,
        capture_spec=capture_spec if capture_spec is not None else _COBRANZA_SPEC,
    )
```

- Imports `get_store as _get_store_impl` (no circular reference).
- Imports `COBRANZA_SPEC as _COBRANZA_SPEC` — api/ is the composition root, the only place allowed to import both feature domains.
- No double-wrap. **CLEAN AND CORRECT.**

---

## 6. Test Rewrite Quality

### test_record_char.py (14 tests)
Asserts: empty→VISITOR, partial contact→VISITOR, 2 interest→PRE_DEBTOR, 1 interest→VISITOR, full contact→DEBTOR, contact+1 enrichment→DEBTOR, contact+2 enrichment→DEBTOR_VERIFIED, transition callbacks (prev/new level tuples), get_status structure, to_dict/from_dict roundtrip. **Threshold/level changes will be caught. NOT weakened.**

### test_debtor_state_level.py (10 tests)
Asserts: all four level strings under precise input conditions, _CONTACT_LEVELS set membership, transition callback new-vocabulary strings. **NOT weakened.**

---

## 7. W1 Test Quality

`test_ensure_tables_projection_table_none_skips_projection` (tests/test_storage_migration.py, section G):

**Nature: STATIC SOURCE INSPECTION** — reads `ensure_tables` source and asserts the conditional guard exists (parameter in signature + `if projection_table` branch present).

**Judgment**: The test suite has no real-DB harness; source inspection is the pragmatic fallback. The guard is present and verified. **Acceptable as-is.**

SUGGESTION: If a real-DB integration harness is added in future, replace with a behavioral test (call `ensure_tables(projection_table=None)` on a live DB, verify no extra table created).

---

## 8. Runtime Import

`python -c "import api.main, features.conversation.record, features.conversation.persistence.state, features.conversation.persistence.redis_store, features.cobranza.debtor"` → **ALL IMPORTS OK**

---

## 9. Scope

`git diff --name-only HEAD~1 HEAD` — exactly 8 files, all S8 targets per tasks artifact. No out-of-scope changes.

---

## 10. Git Hygiene

- debtor_state.py deleted in HEAD commit.
- No CLAUDE.md, lockfiles, __pycache__ committed.
- No secrets.
- Conventional commit format: `feat(platform): delete DebtorState shim + close transient dependency (S8)`.

---

## Issues

### CRITICAL — None

### WARNING — None

### SUGGESTION
- `tests/test_storage_migration.py` (W1 test): source-inspection guard test. Upgrade to behavioral test if real-DB harness is ever added.

---

## Spec Compliance Matrix (PR5 / S8)

| Requirement | Status | Evidence |
|-------------|--------|---------|
| Shim deleted | PASS | ENOENT confirmed |
| Dependency matrix 100% clean | PASS | All forbidden directions ZERO |
| capture_spec=None raises ValueError | PASS | state.py:43-46, redis_store.py:30-33 |
| 424 tests pass | PASS | 424 passed in 1.80s |
| Zero behavior change | PASS | All 424 green, no logic touched |
| Test rewrites retain coverage | PASS | 14+10 behavioral asserts on thresholds/levels |
| W1 guard test | PASS (source-inspection) | Conditional guard in ensure_tables confirmed |

---

## Overall Platform Refactor Assessment (PR1–PR5)

**Coherence**: 5 PRs, disciplined stacked chain, each independently green (380→398→418→423→424). Design decisions applied consistently.

**Zero-behavior**: PR1-4 live in production (prestamype on olimpo), zero incidents. PR5 is code-only cleanup.

**Dependency cleanliness**: Transient shim violation closed definitively in PR5. `features/cobranza → features/conversation` direction sanctioned by design.

**Open ops item**: S6.7 deploy (DROP sorelia_* + rsync + rebuild) is an ops action, flagged as "PENDING — Ricky go" since PR4. Not a code issue.

---

## Final Verdict

**PASS** — Zero CRITICAL, zero WARNING, one SUGGESTION.
The entire platform-multitype-engine change is **archive-ready**.

**Next**: sdd-archive
