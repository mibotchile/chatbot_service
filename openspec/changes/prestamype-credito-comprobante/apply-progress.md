# Apply Progress — prestamype-credito-comprobante

## Slice A — Doris SQL first-unpaid balance (DONE, commit f57817b)
- [x] `_build_sql_window()` in `doris_debt_source.py`: pagos_sel + asig_sel CTEs with ROW_NUMBER
- [x] `_build_sql_legacy()` preserved for tenants without window blocks
- [x] `days_overdue` = GREATEST(DATEDIFF(...), 0) — not stale batch column
- [x] COALESCE(cuota_esperada_actualizada, cuota_esperada_mensual) in SQL
- [x] Config: pagos_selection + batch_selection blocks; cuenta_bancaria added
- [x] Tests: `tests/test_doris_sql_firstunpaid.py` (13), `tests/test_doris_schema.py` (updated 4)

## Slice B — Inversionista + cuenta_bancaria display (DONE, commit 4c60a6a)
- [x] `tools.py` consultar_deuda: inversionista + cuenta_bancaria in return dict
- [x] `frontend/widget.js` _debtCardHtml: Inversionista + Número de cuenta rows, null-guarded
- [x] `tenants/prestamype/responses.json`: donde_pagar + datos_pago + consulta_deuda updated
- [x] Tests: `tests/test_debt_card_display.py` (11)

## Slice C — Comprobante Liviano (DONE, commit 54f620e)
- [x] `validator.py`: lighter `validar_comprobante` signature (monto + optional inversionista/id_credito)
- [x] `tools_schema.py`: monto required; cci+nro_operacion removed
- [x] `tool_registry.py`: updated `_validar_comprobante` signature
- [x] HTTP endpoint: calls `validar_comprobante(profile, monto=monto)` only
- [x] `guardrails.md` + `faq.json`: updated to lighter flow
- [x] Tests: 22 pre-existing migrated + 14 new in `test_comprobante_liviano.py`

## Slice D — Dedup key: image SHA-256 (DONE, commit c98a167)
- [x] `routers/cobranza.py`: `hashlib.sha256(payload).hexdigest()` passed as `image_sha256`
- [x] `validator.py`: dedup by `(credito, image_sha256)`; `image_sha256` stored in audit record
- [x] New duplicate message references "misma imagen" (not monto)
- [x] Tool path (no image bytes) → `image_sha256=None` → dedup skipped → `dedup_ok=True`
- [x] Tests: 5 new hash-contract tests in `test_comprobante_liviano.py` (17 total)
- [x] Updated `test_cobranza_prestamype.py` + `test_frontend_branding_comprobante.py`

## Slice E — monto_vencido as PRIMARY debt display (DONE, commit d1dbc11)
- [x] `doris_debt_source.py`: pagos_agg CTE (SUM/COUNT overdue installments) + LEFT JOIN + explicit defaults in `_row_to_profile`
- [x] `tools.py`: `consultar_deuda` returns `monto_vencido`, `monto_vencido_formatted`, `cuotas_vencidas`
- [x] `shared/templates.py`: `build_variables()` adds monto_vencido, cuotas_vencidas, inversionista, cuenta_bancaria
- [x] `tenants/prestamype/responses.json`: consulta_deuda template leads with vencido+cuotas
- [x] `tenants/prestamype/mock/borrowers.json`: all 5 borrowers have monto_vencido + cuotas_vencidas
- [x] `frontend/widget.js`: vencido prominent section + al-día edge case
- [x] Tests: `tests/test_overdue_amount.py` (16 new), updated test_doris_schema.py + test_responses_engine.py

## Hotfix F — cuenta_bancaria DOUBLE→DECIMAL cast (DONE, commit a1b8f45)
- [x] Root cause: `numero_de_cuenta` is DOUBLE in Doris; window CTE promotes value to scientific notation `8.98348E+12`; digits lost
- [x] Fix: `CAST(a.numero_de_cuenta AS DECIMAL(38,0))` inside `asig_sel` CTE (base-table read, BEFORE window). Casting after window returns NULL (verified live).
- [x] `tenants/prestamype/tenant.config.json`: `cuenta_bancaria` gets `"cast": "id_number"`
- [x] `doris_debt_source.py` `_build_sql_window`: emits `CAST(<col> AS DECIMAL(38,0)) AS <field>` for `cast: id_number` columns on alias `a` (debt source)
- [x] `doris_debt_source.py`: `_STRING_FIELDS` loses `cuenta_bancaria`; new `_ID_NUMBER_FIELDS = {"cuenta_bancaria"}` added
- [x] `doris_debt_source.py` `_row_to_profile`: `_ID_NUMBER_FIELDS` formatted as `str(int(value))` — no scientific notation, no `.0`, None→None
- [x] cci (`codigo_de_cuenta_cci`) NOT affected — string column with leading zeros, passes through unchanged
- [x] Tests: `tests/test_cuenta_bancaria_cast.py` (8 RED→GREEN: SQL shape, outer select, cci no-cast, formatting int/float/None, cci no-change, e2e)

## Test Results
568 passed, 0 failures — `uv run pytest tests/ -q`

## Remaining (manual / production steps)
- [ ] 4.2 Live P04197 SSH verification (Doris cobranza_ro)
- [ ] 4.3 Widget smoke test
- [ ] 4.4 Bot smoke comprobante flow
- [ ] 4.6 Production deploy
