# Design: prestamype → Doris/prod (identity gate hardening)

## Technical Approach

Two tracks. **CODE** (TDD, fixture-testable) lands first behind a flag without
touching the live source: format validation, fall-through fix, per-tenant
`allow_fixture_fallback`. **OPS** flips `data_source` mock→doris only after the
rebuilt image is verified to import pymysql AND Doris is confirmed populated.
The flag is the kill-switch; `data_source: mock` is the master rollback.

## Architecture Decisions

### Decision: Format validation lives in `_identificar_cliente`, runs BEFORE anti-enumeration counter
**Choice**: Normalize (`re.sub(r"\D","",dni)`), then `len==8` (DNI) or `len==11` (RUC); else reject `{identified:False, reason:"invalid_format", message:<neutral>}` and return — do NOT call `_on_identification_attempt`, do NOT call `resolve_dni`.
**Alternatives**: (a) validate after the attempt counter; (b) validate inside each debt-source.
**Rationale**: Garbage input ("hola", "123") is not an enumeration probe — counting it would let a clumsy real user trip the rate-limit, and would leak that "8 digits matters". Reject earliest, cheapest, no source touched. Placing it in the dispatcher entry point keeps both mock and doris backends covered with one rule.

### Decision: Fall-through fix — restructure control flow, gate fixture on tenant flag
**Choice**: Doris OK (no exception) → return mapped rows or `[]`. Fixture fires ONLY in the `except` branch AND only when `allow_fixture_fallback` is true.
**Alternatives**: Remove fixture fallback entirely.
**Rationale**: prestaunion demo (and resolve_token) still need fixture on Doris-down. Keep the FALLBACK CONTRACT but make it opt-in and exception-only. New flow:

```python
def _resolve_dni_credits(dni, tenant_id):
    norm = _normalize_dni(dni)
    if not norm:
        return []
    try:
        rows = _query_dni(norm, tenant_id)
    except Exception:                      # Doris down / driver error
        if _allow_fixture_fallback(tenant_id):
            p = mock_debt_source.resolve_dni(norm, tenant_id=tenant_id)
            return [p] if p else []
        return []                          # prod: fail closed
    return [_row_to_profile(r) for r in rows]   # OK + empty → []
```

### Decision: Flag read via a local config reader, NOT via TenantConfig
**Choice**: Add `_allow_fixture_fallback(tenant_id)` in `doris_debt_source.py`, `@lru_cache`, reading the same `tenant.config.json` already opened by `_load_schema`. Default **false**.
**Alternatives**: Import `tenancy.tenant_loader.TenantConfig`.
**Rationale**: `doris_debt_source` is tenant-agnostic and depends only on settings + mock_debt_source. `TenantConfig` pulls soul/responses engine — a heavy upward dependency and a cycle risk. The module already reads the config file directly (`_load_schema`), so a sibling reader is the established local pattern. Safe default false → prod is secure unless explicitly opted in.

### Decision: pymysql image fix = rebuild, not new code
**Choice**: No pyproject change needed for pymysql. Verified: `pymysql>=1.1.0` is in `pyproject.toml:21` AND resolved in `uv.lock` (lines 203/231/1010). The `override-dependencies` no-op ONLY `mysqlclient`/`sqlalchemy(-utils)` (pydoris transitive), NOT pymysql.
**Rationale**: The container `ModuleNotFoundError` is a STALE image built before pymysql entered the lock. `uv sync --frozen` in `Dockerfile.agent:13` will install it on rebuild. Fix = rebuild + an import smoke gate. (If `--frozen` errors on lock drift, run `uv lock` and commit — but lock already contains it, so unlikely.)

## Data Flow

```
user DNI ─→ _identificar_cliente
              │ 1. normalize+format (8/11)  ── invalid → reject (no attempt, no source)
              │ 2. on_identification_attempt ── rate violation → reject
              │ 3. resolve_dni ─→ debt_source._backend(tenant)
              │                      ├ mock  → mock_debt_source (fixture = PRIMARY)
              │                      └ doris → doris_debt_source._resolve_dni_credits
              │                                   try Doris → rows | [] (OK+empty)
              │                                   except → flag? fixture : []
              └ profile? open gate : reject (dni_not_found)
```
prestaunion stays `data_source:mock` → fixture is the PRIMARY source (unchanged). The flag only governs fixture-as-FALLBACK inside the doris path; mock mode never reads it. Zero delta for prestaunion.

## File Changes

| File | Action | Description |
|------|--------|-------------|
| `apps/agent/api/tool_registry.py` | Modify | `_identificar_cliente`: format-validate + early reject before attempt counter |
| `apps/agent/features/cobranza/doris_debt_source.py` | Modify | Restructure `_resolve_dni_credits`; add `_allow_fixture_fallback` reader |
| `tenants/prestamype/tenant.config.json` | Modify | `allow_fixture_fallback:false`; (ops step) `data_source` mock→doris |
| `tenants/prestaunion/tenant.config.json` | Modify | `allow_fixture_fallback:true` (preserve demo on Doris-down; harmless in mock) |
| `infrastructure/docker/Dockerfile.agent` | Modify | Add post-sync pymysql import smoke: `RUN uv run python -c "import pymysql"` |

`debt_source.py` needs no change (dispatcher already correct).

## Interfaces / Contracts

- `_identificar_cliente` reject shape: `{identified:False, reason:"invalid_format", message:"Necesito tu DNI (8 dígitos) o RUC (11). ¿Me lo confirmas?"}` — neutral, no internal detail.
- `_allow_fixture_fallback(tenant_id) -> bool`, default False, `@lru_cache`.

## Testing Strategy (strict TDD — RED first, baseline 424)

| Layer | What | Approach |
|-------|------|----------|
| Unit | Format: `"hola"`/`"1234567"`/`""`→invalid_format; `"12345678"`→DNI ok; 11-digit→RUC ok; spaces/dots tolerated | Test `_identificar_cliente` with stubbed `resolve_dni`; assert resolve NOT called + attempt NOT counted on invalid |
| Unit | Fall-through: Doris OK+empty→`None`; Exception+flag true→fixture; Exception+flag false→`None`; Doris rows→profile | Monkeypatch `_query_dni` to return `[]`, raise, return rows; toggle `_allow_fixture_fallback` |
| Unit | Flag reader: prestamype→False, prestaunion→True, missing key→False | Fixture tenant dirs / monkeypatch config path |
| Regression | prestaunion mock path unchanged | Existing suite stays green; mock never calls flag |

## Migration / Rollout (staged, gated)

1. **Code+Dockerfile** → rsync + rebuild. `data_source` STILL mock. Suite green.
2. **VERIFY (no flip)**: in rebuilt image — `python -c "import pymysql"`; connect via pymysql to Doris; `SELECT count(*) FROM batch_asignacion_review_bronze`>0; `resolve_dni(<real DNI>,"prestamype")` returns profile, `resolve_dni(<bogus>)` returns None. Use a temporary `data_source:doris` in a throwaway shell, NOT committed.
3. **FLIP**: prestamype `data_source:doris` + `allow_fixture_fallback:false` → rebuild.
4. **SMOKE**: real DNI identifies; bogus DNI → dni_not_found; format garbage → invalid_format; (optional) Doris-down → fail-closed reject, not fixture.

**Rollback per step**: step1 git revert; step3 set `data_source:mock` (instant kill-switch, single-file). Image: redeploy prior tag.

## Open Questions

- [ ] BLOCKER (ops, not design): is `batch_asignacion_review_bronze` populated with real prestamype debtors? Step 2 MUST confirm before step 3. Unverifiable from repo.
- [ ] Decision 2 (proposal): exact bot copy when Doris down + fixture disabled. Suggested: "No puedo verificar tu identidad ahora, te derivo con un asesor." Tasks phase wires this into the `dni_not_found`/fail-closed message.
- [ ] Second identity factor — explicitly OUT (separate change).
