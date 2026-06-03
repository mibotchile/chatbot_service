# Verify Report — PR6 / Slice 10: api/main.py Split + Final Cleanup

**Change**: refactor-screaming-architecture
**Branch**: refactor/screaming-arch-pr6-api-split
**Date**: 2026-06-03
**Verdict**: PASS WITH WARNINGS
**Strict TDD**: ACTIVE — 365 passed, 0 failed

---

## Test Evidence

```
365 passed in 1.53s
```

All 343 baseline tests + 22 new characterization tests green.

---

## CRITICAL Issues

**None.**

---

## WARNING Issues

### WARNING-1 — features→api violation: `apps/agent/features/analytics/dashboard.py:82`

```python
from api.main import visitor_memory
```

**Classification**: Architecture rule violation. Pre-existing debt, NOT introduced by PR6.

**Spec rule**: "Features → shared/ and tenancy/ only."

**Root cause**: `visitor_memory` is a mutable singleton in `api/main.py` (lifespan-owned). `dashboard.py` needs its DB pool. Since the singleton lives in `api/`, the feature must import `api/` to get it — breaking the contract.

**Full features→api audit result**: This is the ONE AND ONLY violation. All other features import only shared/ and tenancy/. Audit is exhaustive (grep confirmed).

### WARNING-2 — ToolRegistry stays in `tools/` (design task 11.2 deferred)

`apps/agent/tools/__init__.py:45` — `class ToolRegistry`

Design specified: move to `api/tool_registry.py`. Correctly deferred: `features/conversation/agent.py:18` does `from tools import ToolRegistry`. Moving it to `api/` would force features→api (forbidden). The design task was self-contradictory; the apply correctly documented the deferral.

### WARNING-3 — Empty legacy dirs not deleted

`apps/agent/core/`, `apps/agent/integrations/`, `apps/agent/prompts/` contain only stale `__pycache__` — zero Python files. Spec "core/ Fully Dissolved" is satisfied at the code level; the empty dirs with pycache are cosmetic noise.

---

## SUGGESTION Items

### SUGGESTION-1 — Relocate singletons and ToolRegistry to shared/ (concrete plan)

**Root cause of both deviations**: shared singletons live in `api/`, forcing features that need them to import `api/`.

**Recommended fix**:
1. Move `ToolRegistry` → `shared/tool_registry.py`. Both `features/conversation` and `api/routers/webhooks` can then import from `shared/` (allowed for both).
2. Replace `visitor_memory` module-level import in `dashboard.py` with a FastAPI `Depends` that reads from `request.app.state.visitor_memory`. The lifespan writes to `app.state` instead of module globals, eliminating the cross-layer import entirely.
3. `tools/` directory can then be removed.

**Blast radius**: 3 files (`agent.py:18`, `webhooks.py:198`, `dashboard.py:82`), ~5 lines. Low risk.

### SUGGESTION-2 — Delete empty dirs

Remove `apps/agent/core/`, `apps/agent/integrations/`, `apps/agent/prompts/` to make the screaming-arch tree visually clean.

### SUGGESTION-3 — main.py line count exceeds spec target

232 lines vs spec ≤150. Delta (+82 lines) is entirely the re-export block (`from api.middleware import ...`, `from api.wiring import ...`) needed so routers can do `import api.main as m; m.X`. No business logic hidden. Acceptable as documented debt given singleton architecture.

---

## Dependency Rule Full Matrix

| Direction | Status | Notes |
|---|---|---|
| features → api | 1 WARNING | `dashboard.py:82` only, pre-existing |
| features → shared | OK | allowed |
| features → tenancy | OK | allowed |
| features → tools | OK | `ToolRegistry` import; tools/ is legacy layer (SUGGESTION-1 to move) |
| shared → features | CLEAN | `templates.py` comment only, not an import |
| tenancy → features | CLEAN | 0 violations |
| cross-feature | CLEAN | 0 violations |
| api → features | OK | allowed; api orchestrates |

---

## ToolRegistry Adjudication

**Correct home**: `shared/tool_registry.py`

`api/` is wrong (would force features→api). `tools/` is a legacy technical layer with no place in screaming-arch. `shared/` is the only layer importable by both `features/conversation` and `api/routers/` without rule violation. Concrete migration is SUGGESTION-1 above.

---

## Characterization Test Quality

**Verdict**: GENUINELY BEHAVIORAL — not source inspection.

Tests make real HTTP requests via `TestClient`. Assertions cover:
- HTTP status codes
- Response body shape and field presence
- Response headers (`X-CSRF-Token`)
- Cookies (`csrf_token`)
- Real file I/O via `tmp_path` fixtures (certificate, reclamos)
- Cryptographic verification (`_verify_session_token`)

This is a solid behavioral contract. The split has real coverage on all extracted endpoints.

---

## Zero-Behavior Verdict

**PASS** — all paths and methods match exactly.

| Endpoint | Original line | Router file | Match |
|---|---|---|---|
| GET /api/v1/security/csrf-token | :816 | security.py:16 | EXACT |
| GET /api/v1/security/session-token | :824 | security.py:24 | EXACT |
| POST /api/v1/chat | :888 | conversations.py:83 | EXACT |
| POST /api/v1/conversations/messages | :1318 | conversations.py:521 | EXACT |
| GET /api/v1/conversations/{id}/messages | :1660 | conversations.py:526 | EXACT |
| POST /api/v1/page-context | :1697 | conversations.py:556 | EXACT |
| GET /api/v1/cobranza/certificate/{fn} | :1324 | cobranza.py:20 | EXACT |
| GET /api/v1/cobranza/reclamos | :1637 | cobranza.py:36 | EXACT |
| GET /api/v1/tenant/{id}/branding | :1431 | cobranza.py:146 | EXACT |
| POST /api/v1/comprobante | :1499 | cobranza.py:217 | EXACT |
| POST /api/v1/webhooks/whatsapp | :1721 | webhooks.py:18 | EXACT |

---

## Shared Global State / Singleton Correctness

**PASS**. `store`, `visitor_memory`, `email_service`, `whatsapp_service` are module-level globals in `api/main.py`, written by the lifespan via `global`. Routers access them via late `import api.main as m` inside request handlers. Python module cache guarantees single instance. No duplication.

---

## Leftover Tree State

| Dir | Status |
|---|---|
| `apps/agent/core/` | Empty (no .py files). Spec satisfied. Dir not deleted. |
| `apps/agent/integrations/` | Empty (no .py files). |
| `apps/agent/prompts/` | Empty (no .py files). |
| `apps/agent/tools/` | Contains `ToolRegistry` — intentional documented deviation. |

---

## Git / Hygiene

- No CLAUDE.md, secrets, or .pyc committed.
- `uv.lock` commits expected (dependency updates).
- 3 clean PR6 commits on top of PR5 base.

---

## Task Completion Status

- Phase 11 (Slice 10): ALL tasks COMPLETE.
- Phase 12 (verify): COMPLETE.
- All phases 0–12: DONE.

---

## Overall 6-PR Refactor Coherence

The full PR1–PR6 refactor is **coherent and complete**:

| Goal | Status |
|---|---|
| Screaming-arch layout (5 features, shared/, tenancy/, api/) | DONE |
| Dead code removal (opportunity_detector) | DONE |
| Domain rename lead→debtor (code + tool contract + storage) | DONE |
| Prod-safe data migration (atomic deploy, dual-read) | DONE |
| Tests green throughout all slices | CONFIRMED (365 passed) |
| Git history via git mv | CONFIRMED |
| Zero observable behavior change (slices 0–8) | CONFIRMED |
| api/main.py split into routers | DONE |

The two documented deviations are self-consistent — the design task 11.2 had a dependency contradiction, and the apply correctly identified it rather than silently breaking the opposite rule.

**Ready for sdd-archive: YES** (features→api debt tracked as SUGGESTION-1 follow-up).
