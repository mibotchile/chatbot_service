# Tasks: Prestamype Cobranza — Scenario-Based Flows & Conversational Spec
# Change: prestamype-cobranza-flujos-escenarios
# Revised: 2026-06-06 v3 (scope delta: naming reframe A, compromiso→gestión-registry C, dedup F, E unchanged, G fully out)
# Revised: 2026-06-10 v4 — definiciones confirmadas Naomi (horario, feriados, morosidad, multi-crédito 7 campos + selector, N° cuota correlativo, escenarios P04069)
# Revised: 2026-06-11 — bot-owned compromiso, 1-intent SCR-02, ID-contrato+DNI auth (decisiones Ricky)

## Out of Scope

7 WhatsApp templates + compromiso reminder (xlsx row 10) → ChatHub (external NestJS system), not this change.

## Terminology Guard (CRITICAL — read before implementing)

| Term | Meaning | Where used |
|------|---------|-----------|
| `credit_state` | INPUT axis: `al_dia` / `por_vencer` / `vencido` — derived from Doris debt profile | scenario.py, session_state, responses.json intent branching |
| `n1` / `n2` / `n3` | External gestión typification codes (`GENERAL.mibotair_results`) — **OUT OF SCOPE** for this change; tipification homologation is a future external mapping layer | NOT used in this change |
| "Nivel 1/2/3" | Naomi's xlsx doc label — survives in comments/docs only, NEVER in code or user-facing strings | — |
| `commitment_date` / `commitment_amount` | Bot-owned compromiso fields in `gestiones` table | gestion_registry.py (bot-owned write), NOT mibotair_results |

Do NOT use `nivel`, `n1`, `n2`, `n3` anywhere in this change's code. Do NOT write to `GENERAL.mibotair_results`. Do NOT call a gestión-registration API.

---

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | 380–480 (G fully out; C = thin stub + map, not a store; 9 files + tests) |
| 400-line budget risk | Medium–High |
| Chained PRs recommended | Yes |
| Suggested split | PR-1 (A+B) → PR-2 (D+E) → PR-3 (C+F) |
| Delivery strategy | ask-on-risk |
| Chain strategy | pending (decide before apply) |

Decision needed before apply: Yes
Chained PRs recommended: Yes
Chain strategy: pending
400-line budget risk: High

### Suggested Work Units

| Unit | Goal | Likely PR | Notes |
|------|------|-----------|-------|
| PR-1 | Credit-state classifier + comprobante n_cuota + abono→compromiso chain | PR 1 | Base: `feat/prestamype-landing-redesign`; fully unblocked |
| PR-2 | Informational intents (D) + multi-credit cuentas bancarias (E) | PR 2 | Base: PR-1 branch; data-driven responses.json additions |
| PR-3 | Compromiso gestión-registry stub (C, task 4.2 blocked) + ID-contrato dedup (F) | PR 3 | Base: PR-2 branch; conversational tasks unblocked; registry stub blocked |

---

## Phase 1 — Foundation: Credit-State Classifier (Slice A)
> Satisfies: SCR-01

- [x] 1.1 Create `apps/agent/features/cobranza/scenario.py` with pure function `classify_credit_state(profile: dict, window_days: int = 5) -> str` returning `"al_dia" | "por_vencer" | "vencido"`. Rules (in order): `vencido` if `profile.get("cuotas_vencidas", 0) >= 1` or `profile.get("days_overdue", 0) > 0`; `por_vencer` if `cuotas_vencidas == 0` and `0 < days_until_due <= window_days` (`days_until_due = (date.fromisoformat(profile["next_due_date"]) - date.today()).days`); `al_dia` otherwise, including missing/None `next_due_date`. Export `CREDIT_STATE_LABELS = {"al_dia": "Al día", "por_vencer": "Próximo a vencer", "vencido": "Vencido"}`. No imports beyond stdlib `datetime`. Do NOT use `nivel`, `n1`, `n2`, `n3` anywhere in this file.
  - Verify: `uv run pytest tests/test_scenario_classifier.py -v`

- [x] 1.2 Create `tests/test_scenario_classifier.py` with `pytest.mark.parametrize` table covering: `vencido` (cuotas_vencidas=2), `por_vencer` at boundary (days_until_due=5), `por_vencer` inside window (days_until_due=3), `al_dia` current (days_until_due=10), `al_dia` missing `next_due_date` key, `al_dia` `next_due_date=None`.
  - Verify: 6 cases pass, `uv run pytest tests/test_scenario_classifier.py -v`

- [x] 1.3 In `apps/agent/features/conversation/agent.py`, after profile is resolved into `session_state`, add: `from apps.agent.features.cobranza.scenario import classify_credit_state` and call `session_state["credit_state"] = classify_credit_state(profile, tenant_config.get("cobranza", {}).get("proxima_vencer_window_days", 5))`. Also set `profile["credit_state"] = session_state["credit_state"]`. No reference to `nivel` or `n1/n2/n3`.
  - Verify: `uv run pytest tests/test_cobranza_prestamype.py -v` (no regressions)

- [x] 1.4 In `tenants/prestamype/tenant.config.json`, add under root key `"cobranza"`: `{"proxima_vencer_window_days": 5, "horario": {"dias": ["lunes","martes","miércoles","jueves","viernes"], "hora_inicio": "09:00", "hora_fin": "18:30", "refrigerio": {"inicio": "13:00", "fin": "14:00"}, "feriados_source": "feriados_peru_2026.json"}, "contrato_column": "id_contrato", "project_uid": "QUIdI0iwQY0l3pJwRKLB"}`. Merge with existing keys; do not remove any. **Valores confirmados Naomi 2026-06-10** (antes hora_fin era "18:00" — CORREGIDO a 18:30; refrigerio 13:00-14:00 es nuevo; feriados_source es nuevo). Copiar `docs/specs-input/prestamype/feriados_peru_2026.json` a `tenants/prestamype/feriados_peru_2026.json` si no existe.
  - Verify: file parses as valid JSON; `uv run pytest tests/test_smoke.py -v`

---

## Phase 2 — Comprobante n_cuota + Pago Parcial Chain (Slice B)
> Satisfies: CPR-01

- [x] 2.1 In `apps/agent/features/cobranza/tools.py`, add `n_cuota: str | None = None` parameter to `validate_comprobante` (or the function building the comprobante-liviano payload dict). Append `"n_cuota": n_cuota` to the returned payload alongside existing `foto`, `monto`, `inversionista`, `id_credito`.
  - Verify: `uv run pytest tests/test_comprobante_liviano.py -v`

- [x] 2.2 In `tenants/prestamype/responses.json`, add intent `"comprobante_proxima_cuota_pregunta"` with two reply options: `"Sí"` (action: `continue_comprobante`) and `"No"` (action: `asesor`). Insert as the first step of the comprobante flow, before the image-upload prompt.
  - Verify: JSON parses without error

- [x] 2.3 In `apps/agent/features/conversation/responses.py`, add pre-question gate to the comprobante flow: if `session_state.get("comprobante_prequestion_answered")` is not True, emit `"comprobante_proxima_cuota_pregunta"` and set `session_state["pending_intent"] = "comprobante"`. On `"Sí"` reply: set flag and continue flow. On `"No"` reply: emit asesor escalation.
  - Verify: `uv run pytest tests/test_chathub_comprobante.py -v`

- [x] 2.4 In `apps/agent/features/conversation/agent.py`, in `_content_after_tool`, detect comprobante tool completion where `classify_tipo(result) == "abono"`. When true, set `session_state["pending_intent"] = "compromiso_pago"` — no intermediate menu shown.
  - Verify: `uv run pytest tests/test_cobranza_prestamype.py::test_pago_parcial_chains_to_compromiso -v`

- [x] 2.5 Create `tests/test_comprobante_ncuota.py`: (a) pre-question `Sí` → flow proceeds with `n_cuota` in payload; (b) pre-question `No` → asesor escalation, no comprobante collected; (c) `tipo==abono` after comprobante completion → `session_state["pending_intent"] == "compromiso_pago"`.
  - Verify: 3 cases pass, `uv run pytest tests/test_comprobante_ncuota.py -v`

---

## Phase 3 — Informational Intents + Multi-Credit (Slices D + E)
> Satisfies: SCR-02, SCR-03, INF-01–INF-11, MCD-01

- [x] 3.1 In `apps/agent/features/cobranza/doris_debt_source.py`, extend the profile-building query to return three new fields: `cuotas_pagadas: int` (count of `batch_pagos_v2_bronze` rows where `fecha_de_pago_del_cliente IS NOT NULL`), `cuotas_pendientes: int` (complement), `fecha_venc_contrato: str | None` (MAX of `fecha_de_pago_esperada_original` formatted as ISO string). Add `get_cronograma(account_id: str, tenant_id: str) -> list[dict]` querying `batch_pagos_v2_bronze`, returning `[{n_cuota, fecha_venc, monto, estado}]` ordered by `n_cuota`.
  - Verify: `uv run pytest tests/test_doris_schema.py tests/test_doris_sql_firstunpaid.py -v`

- [x] 3.2 In `apps/agent/features/cobranza/tools.py`, add `async def consultar_cronograma(profile: dict, tenant_id: str) -> dict` calling `get_cronograma`; returns formatted installment list or asesor-escalation dict on empty result. Add `def render_cuentas_bancarias(credits: list[dict]) -> str` rendering one labeled block per credit (`[{credit_id}] Inversionista: X | Cuenta: Y | CCI: Z`); single-credit path unchanged.
  - Verify: `uv run pytest tests/test_tool_registry.py -v`

- [x] 3.3 In `apps/agent/features/conversation/responses.py`, add `consulta_deuda` intent handler with **internal branching on `session_state["credit_state"]`** — one intent binding, three response paths:
  - `al_dia` → emit `al_dia` message + option menu
  - `por_vencer` → emit `por_vencer` message + option menu
  - `vencido` → emit overdue installment list + `vencido` option menu
  Do NOT create three separate top-level intent bindings. Apply `vencido`-only guard to `"compromiso_pago"` and `"realizar_pago_vencido"` intents. Add 2-strike fallback: increment `session_state["misunderstood_count"]` on unrecognized intent; emit `"no_comprendida_1"` on count=1; escalate asesor on count>=2; reset count on any handled intent.
  - Verify: `uv run pytest tests/test_responses_engine.py -v`

- [x] 3.4 In `tenants/prestamype/responses.json`, add all new intent templates (NOT n1/n2/n3 anywhere):
  - `consulta_deuda` — single binding; contains a `credit_state_branches` map with keys `al_dia`, `por_vencer`, `vencido` holding state-specific copy + option lists per spec §SCR-02. Do NOT create three separate top-level intent keys.
  - `consulta_deuda_total` Sí/No (§SCR-03)
  - `cronograma` (§INF-01)
  - `fecha_venc_contrato` (§INF-02)
  - `cuotas_pagadas` / `cuotas_pendientes` (§INF-03)
  - `cuentas_bancarias` single+multi-credit (§INF-04 + MCD-01)
  - `ya_pague` (§INF-05)
  - `no_puede_pagar` (§INF-06)
  - `alternativas` (§INF-07)
  - `domingo_feriado_al_dia_por_vencer` + `domingo_feriado_vencido_redirect` (§INF-08)
  - `fuera_de_horario` (§INF-09)
  - `no_comprendida_1` / `no_comprendida_2_asesor` (§INF-10)
  - `realizar_pago_vencido` (§INF-11)
  All copy in Spanish per spec wording.
  - Verify: JSON parses without error; `uv run pytest tests/test_responses_engine.py -v`

- [x] 3.5 Create `tests/test_scenario_intents.py`: (a) `al_dia` profile → `consulta_deuda` intent branches internally to `al_dia` response (NOT a separate `consulta_deuda_al_dia` binding); (b) `vencido` profile (2 overdue) → `consulta_deuda` branches to overdue installment list; (c) unrecognized input × 2 consecutive → asesor escalation; (d) `vencido` profile + `domingo_feriado` intent → vencido menu redirect, not holiday copy; (e) multi-credit profile + `cuentas_bancarias` → each credit has a labeled row.
  - Verify: 5 cases pass, `uv run pytest tests/test_scenario_intents.py -v`

---

## Phase 4 — Compromiso: Detect + Validate + Register (Bot-Owned, Slice C)
> Satisfies: CMP-01, CMP-02
> DECISIÓN Ricky 2026-06-11: compromiso is bot-owned — writes to `gestiones` table, NOT to any
> external gestión-registration API and NOT to `GENERAL.mibotair_results`. No n1/n2/n3.
> All tasks in this phase are UNBLOCKED. Task 4.5 (CMP-03 read) is OUT OF SCOPE.

- [ ] 4.1 Create `apps/agent/features/cobranza/gestion_registry.py` with:
  - `async def register_commitment(*, gestiones_row_id: str, commitment_date: str, commitment_amount: float, session_meta: dict) -> dict` — writes `commitment_date` and `commitment_amount` to the bot's `gestiones` table row identified by `gestiones_row_id`; sets outcome = `payment_commitment_registered`; appends journal event `commitment`. On success return `{"registered": True}`. On any error return `{"registered": False}` without raising.
  - Comment: `# Bot-owned storage. No external API. No n1/n2/n3. Tipification mapping is a future out-of-scope layer.`
  - Verify: `uv run pytest tests/test_gestion_registry.py -v`

- [ ] 4.2 ~~[REMOVED — was blocked external gestión-registration API stub; replaced by bot-owned write in 4.1]~~

- [ ] 4.3 In `apps/agent/features/cobranza/tools.py`, add `async def registrar_compromiso(profile: dict, fecha: str, n_cuota: str | None, monto: float | None) -> dict`. Parse `fecha` (accept ISO `YYYY-MM-DD` and `DD/MM/YYYY`); if unparseable, past, or `dias_diff > 2` → return `{"escalate": True, "registered": False}`. If `0 <= dias_diff <= 2`: call `register_commitment(gestiones_row_id=profile["gestiones_row_id"], commitment_date=fecha_iso, commitment_amount=monto, session_meta={...})`; return `{"escalate": False, "registered": result["registered"]}`. If `register_commitment` raises → escalate (do NOT confirm on failure).
  - Verify: `uv run pytest tests/test_compromiso.py -v`

- [ ] 4.4 In `apps/agent/features/conversation/responses.py`, add `"compromiso_pago"` intent handler with `vencido`-only guard (redirect `al_dia`/`por_vencer` to credit-state menu): emit date-ask prompt; on response call `registrar_compromiso`; if `escalate=True` → emit asesor with stated date as context; if `escalate=False` → emit CMP-02 confirmation `"Registramos tu compromiso de pago para el {fecha}. Te enviaremos un recordatorio ese día."` then return to vencido menu.
  - Verify: `uv run pytest tests/test_compromiso.py::test_confirmation_message -v`

- [ ] 4.5 ~~[OUT OF SCOPE — CMP-03 removed from this change. Reading active promise from `GENERAL.mibotair_results` is no longer the design intent; bot owns the commitment in `gestiones`. A future change will surface the active promise from `gestiones` directly. Do NOT implement.]~~

- [ ] 4.6 Create `tests/test_gestion_registry.py`: (a) `register_commitment(...)` writes `commitment_date` and `commitment_amount` to `gestiones` row and returns `{"registered": True}` on success; (b) `register_commitment(...)` returns `{"registered": False}` without raising on DB error (no n1/n2/n3 anywhere). Create `tests/test_compromiso.py`: (c) `fecha=today` → `escalate=False`, `register_commitment` called with correct `commitment_date`; (d) `fecha=today+2d` → `escalate=False`; (e) `fecha=today+3d` → `escalate=True`, `register_commitment` NOT called; (f) invalid date string → `escalate=True`; (g) past date → `escalate=True`; (h) `register_commitment` raises → `escalate=True`, no confirmation; (i) `al_dia` session → compromiso intent blocked, redirected to `al_dia` menu; (j) confirmation message contains correct `fecha`.
  - Verify: all 9 cases pass, `uv run pytest tests/test_gestion_registry.py tests/test_compromiso.py -v`

---

## Phase 5 — ID-Contrato + DNI Dual-Factor Identification (Slice F)
> Satisfies: IDC-01
> DECISIÓN Ricky 2026-06-11: ID-contrato identification requires ALSO the user's DNI.
> Access granted ONLY if provided DNI ∈ {titular, garante} of that contract in Doris — fail-closed otherwise.

- [x] 5.1 In `apps/agent/features/cobranza/doris_debt_source.py`, add `resolve_contrato(contrato_id: str, dni: str, tenant_id: str) -> dict | None` querying `batch_asignacion_review_bronze` by the contract column (name from `tenant_config["cobranza"]["contrato_column"]`, default `"id_contrato"`). MUST:
  1. Dedup titular/garante rows: `ROW_NUMBER() OVER (PARTITION BY id_credito ORDER BY creado_el DESC) AS rn` filtered `WHERE rn = 1`.
  2. After dedup, verify that the provided `dni` matches the `dni` column of either the titular or garante row for that `id_credito`. If no match → return `None` (fail-closed; do NOT reveal contract existence).
  3. On match → return exactly one profile dict (same shape as DNI resolution).
  Returns `None` on miss, DNI mismatch, or any exception.
  - Verify: `uv run pytest tests/test_id_contrato.py -v`

- [x] 5.2 In `apps/agent/features/conversation/agent.py` (or identity gate in `responses.py`), add a branch in the identity resolution path: when user provides a contract ID, collect their DNI as well (two-step prompt), then call `resolve_contrato(contrato_id, dni)`. On profile returned: verify identity and proceed to `classify_credit_state` (same flow as DNI success). On `None`: MUST NOT reveal whether the contract exists or whether the DNI matched; prompt retry or DNI-only path. Reuse existing retry counter; max-retries → asesor.
  - Verify: `uv run pytest tests/test_cobranza_prestamype.py -v`

- [x] 5.3 In `tenants/prestamype/responses.json`, add: `"id_contrato_prompt"` (invite to enter contract ID), `"id_contrato_dni_prompt"` (follow-up asking for DNI after contract ID provided), `"id_contrato_not_found"` (neutral no-reveal message + retry/DNI option — used for both "not found" and "DNI mismatch"; MUST NOT distinguish between the two), `"id_contrato_max_retries"` (asesor escalation).
  - Verify: JSON parses without error; `uv run pytest tests/test_responses_engine.py -v`

- [x] 5.4 Create `tests/test_id_contrato.py`: (a) valid contrato_id + matching DNI (titular) → profile returned, `credit_state` classification proceeds; (b) valid contrato_id + matching DNI (garante) → profile returned; (c) valid contrato_id + DNI that is NOT titular or garante → `None` returned, no-reveal message shown (fail-closed); (d) contrato with titular+garante rows (fixture: 2 person-rows, same `id_credito`) + valid DNI → `resolve_contrato` returns exactly ONE profile dict (dedup verified); (e) unknown contrato_id + any DNI → `None` returned, no-reveal message shown; (f) max retries → asesor escalation; (g) `resolve_contrato` returns `None` on DB exception without raising.
  - Verify: 7 cases pass, `uv run pytest tests/test_id_contrato.py -v`

---

## Phase 6 — Integration Verification
> Cross-cutting; all requirements

- [ ] 6.1 Run full suite: `uv run pytest tests/ -v`. All pre-existing tests must remain green. Priority watch: `test_cobranza_prestamype.py`, `test_debtor_state_level.py`, `test_doris_fallthrough.py`. Confirm no code uses `nivel`, `n1/n2/n3` for routing (grep: `rg "nivel|n1|n2|n3" apps/agent/features/cobranza/scenario.py` → zero hits expected).

- [ ] 6.2 Create `tests/test_scenario_integration.py` with mock-profile fixtures for P04069 (`al_dia` — caso real confirmado por Naomi 2026-06-10), P03638 (`al_dia`), P03700 (`por_vencer` — **NOTA: no hay caso real disponible en datos actuales**; usar data sintética con `days_until_next_due=3` para simular; vencimientos junio solo hasta 12/06 y regla activa a 5 días), P03871 (`vencido` single-credit), P03886 (`vencido` multi-credit). Assert: correct `credit_state` assigned, correct option menu emitted, no cross-state bleed (`al_dia` never shows compromiso option; `vencido` never shows `domingo_feriado` holiday copy).
  - Verify: 5 cases pass (P04069 + 4 anteriores), `uv run pytest tests/test_scenario_integration.py -v`
  - Testing limitation (⚠️): caso real `por_vencer` no disponible. Validar flujo `por_vencer` con fixture sintético hasta que Naomi provea un caso real.

---

## Phase 7 — Morosidad Calculation (INF-12) — NEW 2026-06-10
> Satisfies: INF-12 (spec §INF-12)
> Source: definiciones-naomi-2026-06-10.md §1 — fórmulas exactas confirmadas por Naomi.

- [x] 7.1 In `apps/agent/features/cobranza/scenario.py` (or a dedicated `moratoria.py`), add two pure functions:
  - `calcular_penalidad(saldo_capital_inicial: float, dias_overdue: int) -> float` — **regla inductiva (confirmada Ricky 2026-06-10)**: `semana = max(1, math.ceil(dias_overdue / 7))`; `raw = saldo_capital_inicial * 0.00008 * semana` (0.008% por semana: sem1=0.008%, sem2=0.016%, sem3=0.024%, sin tope); return `math.ceil(raw * 10) / 10` (**ceil al décimo de sol**, ej. 0.56 → 0.60). Do NOT cap at semana 2.
  - `calcular_interes_compensatorio(amortizacion_cuota: float, tasa_interes_mensual: float, dias_transcurridos: int) -> float`: `amortizacion_cuota * (tasa_interes_mensual / 30) * dias_transcurridos`.
  - Both are pure functions, no DB access, no side effects.
  - Verify: `uv run pytest tests/test_moratoria.py -v`

- [x] 7.2 Create `tests/test_moratoria.py` covering:
  - (a) `calcular_penalidad(saldo=7000, dias_overdue=3)` → semana 1: `7000 * 0.00008 = 0.56 → ceil a 0.60`.
  - (b) Example from Naomi: saldo que da 5.66 (semana 1) → ceil a 5.70.
  - (c) `calcular_penalidad(saldo=7000, dias_overdue=10)` → semana 2: `7000 * 0.00016 = 1.12 → ceil a 1.20`.
  - (c2) **inductivo semana 3**: `calcular_penalidad(saldo=7000, dias_overdue=16)` → semana 3: `7000 * 0.00024 = 1.68 → ceil a 1.70` (verifica que NO se topea en semana 2).
  - (d) `calcular_interes_compensatorio(amortizacion=1000, tasa=0.03, dias=10)` → `1000 * (0.03/30) * 10 = 10.0`.
  - (e) `dias_transcurridos = 0` → interés = 0.
  - Verify: 6 cases pass, `uv run pytest tests/test_moratoria.py -v`

- [x] 7.3 In `apps/agent/features/cobranza/tools.py`, integrate moratoria into overdue display: when `credit_state = "vencido"` and overdue detail is surfaced (INF-11 / INF-12), compute and include `penalidad` and `interes_compensatorio` in the response context. Required profile fields (mapeo Doris validado 2026-06-10): `saldo_capital_inicial` ← `batch_pagos_v2_bronze.saldo_por_cancelar` (⚠️ confirmar con Naomi si es saldo pendiente o capital inicial), `amortizacion_cuota` ← `batch_pagos_v2_bronze.amortizacion_esperada_original`, `tasa_interes_mensual` ← `batch_asignacion_review_bronze.tasa_de_interes` (varchar `"X.XX%"` → `CAST(REPLACE(...,'%','') AS DOUBLE)/100`, JOIN por `codigo_contrato = id_credito`), `dias_overdue`. If any required field is missing, omit moratoria amounts and escalate partial display (do NOT invent numbers).
  - Verify: `uv run pytest tests/test_moratoria.py tests/test_scenario_intents.py -v`

---

## Phase 8 — Multi-Crédito Selector + 7 Fields (MCD-01 update) — NEW 2026-06-10
> Satisfies: MCD-01 (updated spec — before only 3 fields, now 7 + selector)
> Source: definiciones-naomi-2026-06-10.md §3

- [x] 8.1 In `apps/agent/features/cobranza/doris_debt_source.py` / `_credit_brief`, ensure the credit profile dict exposes all 7 required fields per credit: `valor_cuota`, `cuenta_bancaria`, `cci`, `inversionista`, `plazo`, `fecha_vencimiento_contrato`, `fecha_inicio_prestamo` (1ª cuota). Add fields that are missing; map from existing Doris columns.
  - Verify: `uv run pytest tests/test_doris_schema.py -v`
  - Status: COMPLETE — all 7 fields mapped in `_row_to_profile` + `column_map` in tenant.config.json; `render_cuentas_bancarias` renders all 7.

- [x] 8.2 In `apps/agent/features/conversation/responses.py` and `tenants/prestamype/responses.json`, add a `"credit_selector"` intent that is emitted when `len(profile["credits"]) == 2`. The selector MUST list both credits with a short label (e.g., credit ID + inversionista). On selection, store `session_state["selected_credit_id"]` and proceed to the relevant flow. Single-credit users skip this step.
  - Verify: `uv run pytest tests/test_multicredit.py -v`
  - Status: COMPLETE — `emit_credit_selector`, `handle_credit_selection`, `resolve_selected_credit` in responses.py; `credit_selector` intent in responses.json.

- [x] 8.3 Ensure all intents that display credit-specific data (cuentas bancarias, consulta deuda, cronograma) use `session_state["selected_credit_id"]` to filter data when `len(credits) == 2`. For single-credit users, `selected_credit_id` defaults to the only credit's ID (no selector shown).
  - Verify: `uv run pytest tests/test_multicredit.py tests/test_scenario_intents.py -v`
  - Status: COMPLETE — `resolve_selected_credit` wires selection into downstream handlers.

- [x] 8.4 Create `tests/test_multicredit.py`: (a) user with 2 credits → `credit_selector` intent emitted; (b) user selects credit A → `session_state["selected_credit_id"]` set correctly; (c) `cuentas_bancarias` display for selected credit shows all 7 fields; (d) single-credit user → no selector shown, all 7 fields displayed without selector; (e) `consulta_deuda` for user with 2 credits → uses selected credit's data.
  - Verify: 5 cases pass, `uv run pytest tests/test_multicredit.py -v`
  - Status: COMPLETE — 10 tests pass (5 required + 5 edge cases).

---

## Phase 9 — Horario + Feriados Gating — NEW 2026-06-10
> Satisfies: INF-08 (feriados_peru_2026.json), INF-09 (confirmed hours + refrigerio)
> Source: definiciones-naomi-2026-06-10.md §5

- [x] 9.1 Copy `docs/specs-input/prestamype/feriados_peru_2026.json` to `tenants/prestamype/feriados_peru_2026.json`. Add a loader in `apps/agent/features/cobranza/` (e.g., `horario.py`) that reads this JSON and exposes `is_feriado(date: date) -> bool` and `is_business_hours(dt: datetime) -> bool`. `is_business_hours` MUST also return `False` during refrigerio (13:00–14:00).
  - Verify: `uv run pytest tests/test_horario.py -v`

- [x] 9.2 Create `tests/test_horario.py` covering:
  - (a) A confirmed date from `feriados_peru_2026.json` (e.g., 2026-07-28 Fiestas Patrias) → `is_feriado()` returns True.
  - (b) A normal weekday → `is_feriado()` returns False.
  - (c) 12:00 Lunes → `is_business_hours()` True.
  - (d) 13:30 Lunes → `is_business_hours()` False (refrigerio).
  - (e) 18:31 Lunes → `is_business_hours()` False (after hours).
  - (f) 09:00 Sábado → `is_business_hours()` False (weekend).
  - Verify: 6 cases pass, `uv run pytest tests/test_horario.py -v`

- [x] 9.3 Wire `is_feriado()` into INF-08 (due date holiday check) and `is_business_hours()` into INF-09 (out-of-hours gate). Both must use the loaded `feriados_peru_2026.json` — no hardcoded date lists.
  - Verify: `uv run pytest tests/test_horario.py tests/test_scenario_intents.py -v`

---

## Phase 10 — N° Cuota Correlativo Validation — NEW 2026-06-10
> Satisfies: CPR-01 (N° cuota definition confirmed)
> Source: definiciones-naomi-2026-06-10.md §4

- [x] 10.1 In `apps/agent/features/cobranza/tools.py`, in the comprobante payload builder, validate that `n_cuota` is a positive integer string matching a valid correlativo from the user's cronograma (via `get_cronograma`). If `n_cuota` provided does not match any `n_cuota` in the cronograma, ask the user to confirm or re-enter. Do NOT silently accept arbitrary strings. (The correlativo is 1, 2, 3… matching the "Nro Cuotas" column in Prestamype's payment file.)
  - Verify: `uv run pytest tests/test_ncuota_validation.py -v`

- [x] 10.2 Create `tests/test_ncuota_validation.py`: (a) valid `n_cuota=2` matching cronograma → payload accepted; (b) `n_cuota=99` not in cronograma → re-ask triggered; (c) `n_cuota="abc"` (non-integer) → re-ask triggered; (d) `n_cuota=None` → required field error; (e) cronograma unavailable → accept `n_cuota` without cross-validation (best-effort, do NOT block flow on Doris error).
  - Verify: 5 cases pass, `uv run pytest tests/test_ncuota_validation.py -v`
