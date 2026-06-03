# Proposal: Refactor to Screaming Architecture (move + split god files)

## Intent

Current layout is LAYERED (`api/core/integrations/tools/config`) — it screams "FastAPI app", not "cobranza domain". Pain: `core/` is a 26-file junk drawer mixing engine, whatsapp, leads, persistence, rate-limit, email, webhooks; 3 god files (`api/main.py` 1905, `tools/cobranza.py` 870, `core/responses.py` 764); no feature boundaries; everything coupled through a flat `core/`. Reorganize so the structure screams the domain and split the god files by responsibility.

## Scope

### In Scope
- Reorganize to feature-first: `features/` + `shared/` (kernel) + `tenancy/` + thin `api/`.
- Split the 3 god files by responsibility into their owning feature.
- Re-map all absolute imports (`core.X` → `features/shared.X`) including the 18 test files.
- Enforce dependency rules: features→shared/tenancy (never reverse); features cross only via explicit ports; shared knows no feature; api only orchestrates.

### Out of Scope
- Any behavior change (PURE refactor — tests are the contract).
- New features, new tenants, API contract changes.
- Replacing the absolute-import convention with relative imports.

## Capabilities

### New Capabilities
None — pure refactor, no new behavior.

### Modified Capabilities
None — no spec-level requirement changes. Tests assert unchanged behavior.

## Approach

Slice-by-feature migration via `git mv` (preserve history), one feature per commit/slice, full suite (`uv run pytest tests/ -v`) green after every slice. Order: lowest-coupling leaf first (safest). For god-file splits where coverage has gaps, write characterization tests FIRST (STRICT TDD active), then split.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `apps/agent/core/` | Removed | Dissolved into features + shared + tenancy |
| `apps/agent/features/*` | New | conversation, cobranza, comprobantes, leads, messaging, analytics |
| `apps/agent/shared/` | New | llm, persistence, rate_limit, webhooks, config |
| `apps/agent/tenancy/` | New | tenant_loader, soul, pricing |
| `apps/agent/api/main.py` | Modified | 1905 → ~150 lines; thin routers under `api/routers/` |
| `tests/*` (18 files) | Modified | Import re-map only |

## Open Decisions (for Ricky — surface, do NOT decide)

1. Kernel name: `shared` vs `kernel` vs `platform`.
2. `messaging` as one feature vs split `whatsapp` / `chathub`.
3. `comprobantes` separate feature vs subfolder of `cobranza`.
4. Slice order — recommend lowest-coupling leaf first (e.g. `analytics` or `leads`) as safest, hottest path (`conversation`) last.

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Broken imports after move | High | Re-map + run full suite per slice |
| God-file split changes behavior | Med | Characterization tests first |
| Coverage gaps hide regressions | Med | Add tests before splitting |
| PR too large to review (>400 lines) | High | Chained PRs, one feature per slice |
| Circular deps when enforcing rules | Med | Extract ports; surface in design phase |

## Rollback Plan

Each slice is its own commit/PR. Revert with `git revert <sha>` per slice — `git mv` keeps history intact. Suite stays green at every commit, so any red revert restores the last good slice.

## Dependencies

- STRICT TDD active; runner `uv run pytest tests/ -v`; 18 existing tests are the safety net.
- Delivery: ask-on-risk → expect chained PRs.

## Success Criteria

- [ ] Full suite green after every slice and at the end.
- [ ] `core/` no longer exists; features/shared/tenancy/api in place.
- [ ] `api/main.py` ≤ ~150 lines; 3 god files split by responsibility.
- [ ] Dependency rules hold (no feature→feature except via ports; shared→no feature).
- [ ] Zero behavior change (no test modified beyond import paths).
