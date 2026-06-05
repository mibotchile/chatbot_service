# Apply Progress — prestamype-credito-comprobante

## Slice A — Doris SQL first-unpaid balance (DONE, commit f57817b)

- [x] `_build_sql_window()` in `doris_debt_source.py`: pagos_sel + asig_sel CTEs with ROW_NUMBER
- [x] `_build_sql_legacy()` preserved for tenants without window blocks
- [x] `days_overdue` = GREATEST(DATEDIFF(...), 0) — not stale batch column
- [x] COALESCE(cuota_esperada_actualizada, cuota_esperada_mensual) in SQL
- [x] Config: pagos_selection + batch_selection blocks; cuenta_bancaria added; principal_original removed
- [x] Tests: `tests/test_doris_sql_firstunpaid.py` (13), `tests/test_doris_schema.py` (updated 4)

## Slice B — Inversionista + cuenta_bancaria display (DONE, commit 4c60a6a)

- [x] `tools.py` consultar_deuda: inversionista + cuenta_bancaria in return dict
- [x] `frontend/widget.js` _debtCardHtml: Inversionista + Número de cuenta rows, null-guarded
- [x] `tenants/prestamype/responses.json`: donde_pagar + datos_pago + consulta_deuda updated
- [x] Tests: `tests/test_debt_card_display.py` (11)

## Slice C — Comprobante Liviano (DONE, commit 54f620e)

- [x] `validator.py`: `validar_comprobante(profile, monto, *, inversionista=None, id_credito=None)` — server-side CCI, mismatch=WARN not reject, anti-dup by (credito, monto), tipo=pago_cuota|abono|cancelacion
- [x] `tools_schema.py`: monto required; inversionista+id_credito optional; cci+nro_operacion removed
- [x] `tool_registry.py`: `_validar_comprobante(monto, *, inversionista=None, id_credito=None)`
- [x] HTTP endpoint (`routers/cobranza.py`): calls `validar_comprobante(profile, monto=monto)` only
- [x] `guardrails.md` + `faq.json`: updated to lighter flow
- [x] 22 pre-existing tests migrated to new contract
- [x] `tests/test_comprobante_liviano.py`: 14 new Slice C tests

## Slice D — Dedup key: image SHA-256 (DONE, commit c98a167)

- [x] **Decision**: dedup by SHA-256 of uploaded image bytes, NOT by (credito, monto). Same image re-uploaded = true duplicate (hard block). Different images, same monto = both accepted. Tool-invoked path (no image bytes) → image_sha256=None → dedup skipped → dedup_ok=True.
- [x] `routers/cobranza.py`: `import hashlib`; compute `hashlib.sha256(payload).hexdigest()` after magic-byte sniff; pass as `image_sha256=` to `validar_comprobante`.
- [x] `validator.py`: `validar_comprobante` gains `image_sha256: str | None = None` param. Dedup condition: `r.get("credito") == credito and r.get("image_sha256") == sha` (scoped per credito). `image_sha256` stored in audit record. New duplicate message: "Ya registramos ese comprobante... misma imagen dos veces".
- [x] Tests: 5 new hash-contract tests (17 total in test_comprobante_liviano.py)

## Slice E — monto_vencido as PRIMARY debt display (DONE, commit d1dbc11)

**Goal**: Lead "cuánto debo" with what the borrower owes to GET CURRENT (overdue installments), not the total remaining loan balance. Confirmed live P04197: monto_vencido=28,127.64, cuotas_vencidas=4.

- [x] `doris_debt_source.py`:
  - Added `pagos_agg` CTE: `SUM(cuota_esperada_mensual) AS monto_vencido, COUNT(*) AS cuotas_vencidas` WHERE `fecha_de_pago_del_cliente IS NULL AND fecha_de_pago_esperada_original <= CURDATE()` GROUP BY codigo_contrato
  - LEFT JOIN pagos_agg into window query (no rows = 0 overdue)
  - `_row_to_profile` always sets `monto_vencido` (float, default 0.0) and `cuotas_vencidas` (int, default 0)
  - `monto_vencido` removed from `_NUMERIC_FIELDS` (handled explicitly with default)
- [x] `tools.py`: `consultar_deuda` returns `monto_vencido`, `monto_vencido_formatted`, `cuotas_vencidas`; `balance` (total) kept as secondary
- [x] `shared/templates.py`: `build_variables()` adds `monto_vencido`, `cuotas_vencidas`, `inversionista`, `cuenta_bancaria` as template vars
- [x] `tenants/prestamype/responses.json`: `consulta_deuda` template leads with vencido+cuotas, saldo total secondary, inversionista included
- [x] `frontend/widget.js` `_debtCardHtml`: when cuotas_vencidas > 0 → "Vencido / a regularizar" PROMINENT + cuotas label + "Saldo total del préstamo" secondary; when cuotas_vencidas == 0 → "Estás al día" + saldo total (no scary vencido)
- [x] `tenants/prestamype/mock/borrowers.json`: all 5 borrowers have monto_vencido + cuotas_vencidas (0/0 for al día, positive for en mora)
- [x] `tests/test_overdue_amount.py`: 16 new Slice E tests (RED→GREEN confirmed)
- [x] Updated `tests/test_doris_schema.py`: expected profile shape includes monto_vencido + cuotas_vencidas
- [x] Updated `tests/test_responses_engine.py`: 3 tests updated for new template wording

## Hotfix F — cuenta_bancaria DOUBLE→DECIMAL cast (DONE, commit a1b8f45)

**Root cause**: `numero_de_cuenta` is a DOUBLE column in Doris. A direct SELECT returns it as a proper integer string, BUT when Doris wraps the read inside a window CTE (SELECT *, ROW_NUMBER() OVER ...) it promotes the value to scientific notation "8.98348E+12". Once in scientific form, digits are lost permanently. Casting inside/after the window CTE returns NULL — the cast MUST be applied at the SOURCE (base-table read in the assignment CTE, before the window).

**Fix** (config-driven, general, not a one-off):
- `tenants/prestamype/tenant.config.json`: `cuenta_bancaria` column_map entry gets `"cast": "id_number"`
- `doris_debt_source.py` `_build_sql_window`: for `cast: id_number` cols on alias `a` (debt source), emits `CAST(a.<col> AS DECIMAL(38,0)) AS <field>` inside the asig_sel CTE. Outer SELECT references the already-cast alias — no re-cast after window.
- `doris_debt_source.py`: `_STRING_FIELDS` loses `cuenta_bancaria`; new `_ID_NUMBER_FIELDS = frozenset({"cuenta_bancaria"})` defined with comment explaining the DOUBLE→DECIMAL upstream caveat (leading zeros lost at ETL).
- `doris_debt_source.py` `_row_to_profile`: `_ID_NUMBER_FIELDS` entries formatted as `str(int(value))` — no scientific notation, no trailing `.0`. None/empty → None.
- `cci` (`codigo_de_cuenta_cci`) is NOT affected — it is a string column with leading zeros ('00389801347991694443'), passes through unchanged.
- `tests/test_cuenta_bancaria_cast.py`: 8 RED→GREEN tests covering SQL shape (CAST at source), outer select no re-cast, cci no-cast, _row_to_profile formatting (int, float, None), cci leading-zeros preservation, e2e resolve_dni.

## TDD Cycle Evidence (Hotfix F)

| Task | RED | GREEN | REFACTOR |
|------|-----|-------|----------|
| SQL emits CAST at source in asig_sel | FAIL (no CAST) | PASS (CAST inside asig_sel) | — |
| _row_to_profile int→clean string | FAIL (returns int) | PASS (str(int(value))) | extracted _ID_NUMBER_FIELDS set |
| _row_to_profile float→clean string | FAIL (returns float) | PASS | — |
| e2e resolve_dni cuenta_bancaria clean | FAIL | PASS | — |

## Test Results

568 passed, 0 failures (full suite). Test runner: `uv run pytest tests/ -q`
(was 560 before Hotfix F; +8 new tests)

## Remaining (Phase 4 manual steps)

- [ ] 4.2 Live P04197 SSH verification (Doris cobranza_ro) — confirm monto_vencido=28127.64, cuotas_vencidas=4
- [ ] 4.3 Widget smoke test — verify "Vencido / a regularizar" section prominent, "Estás al día" for al-día borrower; verify cuenta_bancaria shows full number (not scientific notation)
- [ ] 4.4 Bot smoke comprobante flow
- [ ] 4.6 Production deploy

## Commits (branch: feat/prestamype-credito-comprobante)

- f57817b — feat(doris): first-unpaid balance + latest-batch window CTE (Slice A)
- 4c60a6a — feat(display): inversionista + cuenta_bancaria in debt card + templates (Slice B)
- 54f620e — feat(comprobante): lighter flow — monto-only, server-side CCI, inversionista as warn (Slice C)
- c98a167 — feat(comprobante): image SHA-256 dedup (Slice D)
- d1dbc11 — feat(cobranza): surface monto_vencido as primary debt display (Slice E)
- a1b8f45 — fix(doris): cast numero_de_cuenta DECIMAL(38,0) at source to prevent scientific notation (Hotfix F)
