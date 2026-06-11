# Design: Prestamype Cobranza — Scenario-Based Flows
# Revised: 2026-06-10 — definiciones confirmadas por Naomi Ramos (definiciones-naomi-2026-06-10.md)

## Technical Approach

Add a thin **credit-state classifier** that derives the credit's situation (`al_dia` / `por_vencer` / `vencido`) from the already-verified Doris debt profile, then thread that state into the existing 2-layer canned router (`route_layer1` → `_emit_intent`) so intents pick state-specific template variants. All new behavior is additive and reuses shipped infra: the Doris window SQL (`pagos_agg` CTE), the `_credit_brief` multi-credit shape, and the sticky-flow + `pending_intent` chaining in `agent.py`. Tenant-agnostic engine; prestamype copy stays in `responses.json`.

**TWO ORTHOGONAL AXES — do not conflate:**
- **Credit STATE** (INPUT, from debt data): `al_dia` / `por_vencer` / `vencido`. This is Naomi's "Nivel 1/2/3" reframed. The string "Nivel" never appears in code or to the user — it survives only as a doc reference to Naomi's xlsx.
- **Gestión TYPIFICATION** (OUTPUT, canonical): `n1` / `n2` / `n3` of the homologated typification tree in `GENERAL.mibotair_results`. These names are RESERVED EXCLUSIVELY for canonical gestiones (fronts C/D). Never use `n1/n2/n3` for credit state.

**This change is a 100% REACTIVE chatbot.** All proactive/outbound behavior (the 7 WhatsApp templates AND the compromiso reminder, xlsx row 10 / WA template 6) is **ChatHub's responsibility, implemented directly in ChatHub (external NestJS)** — NOT in this bot, and NOT as a separate SDD change here.

**`GENERAL.mibotair_results` (new ground truth)**: Doris DB `GENERAL` (NOT BigQuery), the CANONICAL cobranza gestiones table (human + voicebot + bot), ETL-fed (~72-min lag), filtered by `project_uid` (prestamype = `QUIdI0iwQY0l3pJwRKLB`). Columns: `project_uid, date, document, management, sub_management, n1, n2, n3, promise_date, promise_amount, agent_name, extra_data(json)`.

## Architecture Decisions

| # | Decision | Choice | Alternative rejected | Rationale |
|---|----------|--------|----------------------|-----------|
| A | Where credit state is computed | New `cobranza/scenario.py::classify_credit_state(profile, window_days)`; called once after profile resolution; result stored on profile as `credit_state` + `credit_state_label` and in `session_state["credit_state"]`. | Compute inside each intent template; compute in SQL. | Single source of truth, pure function, trivially unit-testable, no SQL change. Router reads `session_state["credit_state"]` to select variants. |
| A | Thresholds | `vencido` if `cuotas_vencidas >= 1` (equiv. `days_overdue > 0`); else `por_vencer` if `0 < days_until_due <= WINDOW` (default 5); else `al_dia`. `days_until_due = (next_due_date - today).days`. Undetermined (no `next_due_date`) → `al_dia` (safe default = current behavior). | Use stale `dias_de_atraso`; reuse `n1/n2/n3` (collides with gestión axis). | Reuses derived `cuotas_vencidas`/`days_overdue` (already correct per balance spec). `al_dia` fail-safe preserves shipped behavior on rollback. State names stay distinct from the gestión `n1/n2/n3` axis. |
| B | Comprobante N° cuota | Add `n_cuota` capture field to the comprobante flow (free integer string). Pre-question `comprobante_proxima_cuota` (Sí→continue flow / No→asesor) gates entry. `n_cuota` is appended to the existing comprobante-liviano payload (passed through `validate_comprobante` caller, stored alongside foto+monto+inversionista+ID). | Infer cuota from amount. | Naomi requires explicit capture incl. partial payments; inference is unreliable. |
| B | Pago parcial → compromiso | When `tipo == "abono"` (partial, from `classify_tipo`), chain to compromiso intent via existing `pending_intent` mechanism after comprobante tool succeeds. | Separate manual step. | Reuses `_content_after_tool` chaining already used post-identify. |
| C | Compromiso persistence | **Register as a CANONICAL GESTIÓN via the platform's gestión-registration API** — the SAME path ChatHub/voicebot/human agents use; an ETL then lands it in `GENERAL.mibotair_results`. Conversational part (this repo): detect date, validate (>2 días → asesor), confirm to user (row 9). Payload: `n1="CONTACTO DIRECTO"`, `n2="CDP - COMPROMISO DE PAGO"`, `n3="COMPROMISO DE PAGO"`, `promise_date`, `promise_amount`, `document`, `project_uid`, `agent_name="BOT ADA"`, `extra_data=[{"MONTO COMPROMISO":…},{"FECHA COMPROMISO":…}]`. | (1) Local JSON — REJECTED (dead copy). (2) `/chat` ChatHub event — REJECTED/DROPPED (not the canonical path). (3) Direct-write to `GENERAL.mibotair_results` — REJECTED: `cobranza_rw` lacks write there (only `cobranza_analytics` Select+Load), table is ETL-fed (~72-min lag) → direct writes conflict. | The canonical system of record for gestiones is `GENERAL.mibotair_results`, fed only via the registration API + ETL. The bot must enter through the same door as every other channel. |
| C | >2 días rule | If `fecha - today > 2 days` → route to asesor (row 8), no registration. | Register anyway. | Matches spec; business gate. |
| C | Active-promise read (OPTIONAL, low pri) | READ active `promise_date` from `GENERAL.mibotair_results` for conversational context ("ya tenés un compromiso para el X"), with explicit ~72-min ETL-lag caveat (a just-made promise won't appear yet). Needs a read account with `GENERAL` access. | Skip the read. | Nice continuity touch; `cobranza_ro` is scoped to the project DB and CANNOT see `GENERAL`, so this needs a new read grant — defer unless cheap. |
| D | New informational intents | Data-driven in `responses.json` where answerable from the profile (deuda total, cuotas pagadas/pendientes, ya-pagué, no-puede-pagar, alternativas, domingo/feriado [`al_dia`/`por_vencer` only], fuera de horario, no-comprendida 1er/2do). Only **cronograma** + **fecha venc. contrato** + **N° cuotas pagadas/pendientes counts** need new Doris fields (see Interfaces). | Code every intent. | Minimizes code; zero invented numbers (answer from verified data only). |
| D | Bot as gestión channel | Bot registers key outcomes as canonical gestiones via the same registration API (front C). **Scope NOW: compromiso only.** A **typification mapping table** (bot outcome → `n1`/`n2`/`n3` + `management`/`sub_management`) lives in config and can grow later (derivación a asesor, confirmó pago/comprobante, etc.). | Hardcode the compromiso typification only. | One extension point; future outcomes add a mapping row, not new code paths. |
| D | 2-strike fallback | `session_state["misunderstood_count"]`; 1st → reask, 2nd → asesor. Reset on any handled intent. | LLM-only retry. | Deterministic, matches spec. |
| E | Multi-credit | Reuse `_resolve_dni_credits` + `_credit_brief`. Add a **credit selector** step when user has 2 credits. Display 7 fields per credit: valor de cuota, cuenta bancaria, CCI, inversionista, plazo, fecha de vencimiento, inicio del préstamo (1ª cuota). Add `cuentas_bancarias` intent rendering one block per credit. **ACTUALIZADO 2026-06-10**: antes solo 3 campos; ahora 7 + selector. Fuente: definiciones-naomi-2026-06-10.md §3. | New query. | Data already available; presentation + selector only. |
| F | ID-contrato identification | Plug into identity gate as a DNI alternative: new resolver `resolve_contrato(contrato_id, tenant)` querying `batch_asignacion_review_bronze` by contract column. **MUST dedup titular/garante rows**: one credit has multiple person-rows for the same contrato — `ROW_NUMBER() OVER (PARTITION BY id_credito ...)` pick rn=1 (we need the credit, not the person). Stored in `pending_intent`/identity path same as DNI. | No dedup (would return titular+garante as 2 hits). New gate. | Reuses identity gate; window dedup mirrors the existing batch-selection pattern. |
| G | Outbound (7 WhatsApp templates + compromiso reminder) | **OUT OF SCOPE — owned by ChatHub (external NestJS), built directly there.** Not in this bot, not a separate SDD change here. This change is 100% reactive. | Build inbound+outbound here; separate `prestamype-outbound-campaigns` change. | Outbound proactivo is ChatHub's domain; the reminder fires off the canonical compromiso gestión, not off this bot. |

## Open Questions — Resolved

Todas resueltas al 2026-06-10 (fuente: definiciones-naomi-2026-06-10.md).

1. **Window days** → **5 días — CONFIRMADO Naomi 2026-06-10**. Parametrizado via `tenant.config.json` `cobranza.proxima_vencer_window_days`.
2. **WhatsApp templates / outbound here?** → **No** — owned by ChatHub, built directly there. This bot is reactive-only.
3. **Compromiso persistence** → **register as a canonical gestión via the platform's gestión-registration API** (same path as ChatHub/voicebot/human; ETL lands it in `GENERAL.mibotair_results`). Local JSON, `/chat` event, and direct-write all rejected. **Regla CONFIRMADA**: ≤2 días → registrar; >2 días → asesor.
4. **Horario source** → **tenant config** `cobranza.horario` — **CONFIRMADO Naomi 2026-06-10**: Lun-Vie 9:00–18:30, refrigerio 13:00–14:00 (asesores NO disponibles). Feriados: `feriados_peru_2026.json` como fuente canónica. Ver task 1.4 para los valores exactos a configurar.
5. **ID-contrato uniqueness** → one contrato has **multiple person-rows** (titular + garante) but is **one credit**; `resolve_contrato` dedups via `PARTITION BY id_credito` → one credit. **CONFIRMADO Naomi 2026-06-10**. ⚠️ Matiz: acceso restringido a involucrados del préstamo (titular/garante) únicamente — ver decisión F y IDC-01.
6. **Escenarios testing** — Caso real `al_dia` = P04069 (CONFIRMADO). Caso real `por_vencer` = NO disponible (vencimientos junio hasta 12/06; regla a 5 días; validar con data sintética).

## Data Flow

    user msg ──→ resolve profile (Doris) ──→ classify_credit_state() ──→ session_state["credit_state"]
                                                                        │  (al_dia/por_vencer/vencido)
    route_layer1 / _emit_intent ──reads credit_state──→ pick template variant in responses.json
                                                                        │
    comprobante flow ─(n_cuota, pre-question)→ validate_comprobante ─abono→ pending_intent=compromiso
    compromiso intent ─(fecha)→ >2d? asesor : confirm(row 9) + register canonical gestión
                                                          │  (n1/n2/n3 + promise_date/amount, agent="BOT ADA")
                                            gestión-registration API → ETL (~72-min) → GENERAL.mibotair_results
                                                          │
                                                ChatHub reads gestión → schedules reminder (out of scope)

## File Changes

| File | Action | Description |
|------|--------|-------------|
| `apps/agent/features/cobranza/scenario.py` | Create | `classify_credit_state(profile, window_days)` pure fn + labels (`al_dia`/`por_vencer`/`vencido`) |
| `apps/agent/features/cobranza/gestion_registry.py` | Create | thin client for the gestión-registration API + typification mapping table (bot outcome → n1/n2/n3) |
| `apps/agent/features/cobranza/doris_debt_source.py` | Modify | Expose cronograma rows + `cuotas_pagadas`/`cuotas_pendientes` counts; `resolve_contrato` (dedup); `fecha_venc_contrato`; OPTIONAL active-promise read from `GENERAL.mibotair_results` |
| `apps/agent/features/cobranza/tools.py` | Modify | comprobante `n_cuota` passthrough; `registrar_compromiso` tool (validate + build canonical-gestión payload → gestion_registry); `consultar_cronograma`; cuentas-bancarias multi-credit render |
| `apps/agent/features/conversation/responses.py` | Modify | credit-state-aware variant selection; new intents; 2-strike fallback |
| `apps/agent/features/conversation/agent.py` | Modify | thread `credit_state` into session_state; abono→compromiso chain via pending_intent |
| `tenants/prestamype/responses.json` | Modify | new intents copy, credit-state variants, ID-contrato, multi-credit, horario/feriado |
| `tenants/prestamype/tenant.config.json` | Modify | `proxima_vencer_window_days: 5`, `horario: {dias: lunes-viernes, hora_inicio: 09:00, hora_fin: 18:30, refrigerio: {inicio: 13:00, fin: 14:00}, feriados_source: "feriados_peru_2026.json"}`, cronograma column_map, contrato column, `project_uid`, gestión typification mapping. **Valores confirmados Naomi 2026-06-10.** |

## Interfaces / Contracts

```python
# scenario.py
def classify_credit_state(profile: dict, window_days: int = 5) -> str:
#   "al_dia" | "por_vencer" | "vencido"

# doris_debt_source.py additions
def resolve_contrato(contrato_id: str, tenant_id: str) -> dict | None
#   dedup titular/garante: ROW_NUMBER() OVER (PARTITION BY id_credito ORDER BY ...) → rn=1
def get_cronograma(account_id: str, tenant_id: str) -> list[dict]  # [{n_cuota, fecha_venc, monto, estado}]
def get_active_promise(document: str) -> dict | None  # OPTIONAL: read promise_date from GENERAL (ETL-lagged)
# profile gains: cuotas_pagadas:int, cuotas_pendientes:int, fecha_venc_contrato:str|None

# gestion_registry.py — canonical gestión registration (same API as ChatHub/voicebot/human)
TIPIFICATION_MAP = {
  "compromiso_pago": {
    "n1": "CONTACTO DIRECTO",
    "n2": "CDP - COMPROMISO DE PAGO",
    "n3": "COMPROMISO DE PAGO",
  },
  # future: "derivacion_asesor", "confirmo_pago", "confirmo_comprobante", ...
}
async def register_gestion(outcome: str, *, document: str, project_uid: str,
                           promise_date: str | None = None, promise_amount: float | None = None,
                           agent_name: str = "BOT ADA", extra_data: list | None = None) -> dict:
#   POST to the platform's gestión-registration API; ETL lands it in GENERAL.mibotair_results.
#   returns {registered: bool, gestion_id: str | None}

# tools.py
async def registrar_compromiso(profile: dict, fecha: str, n_cuota: str | None, monto: float | None) -> dict
#   validate >2d → {escalate: True}; else call register_gestion("compromiso_pago", ...)
#   extra_data=[{"MONTO COMPROMISO": monto}, {"FECHA COMPROMISO": fecha}]
#   returns {escalate: bool, registered: bool}

# resolve_contrato dedup sketch:
#   ROW_NUMBER() OVER (PARTITION BY id_credito ORDER BY creado_el DESC) AS rn ... WHERE contrato = %s ... rn=1
```

Cronograma sourced from `batch_pagos_v2_bronze` (one row per installment): `cuotas_pagadas` = count where `fecha_de_pago_del_cliente IS NOT NULL`; `cuotas_pendientes` = complement. `fecha_venc_contrato` = MAX(`fecha_de_pago_esperada_original`).

**N° cuota (confirmado Naomi 2026-06-10)**: correlativo 1, 2, 3… coincide con columna "Nro Cuotas" del archivo de pagos. La función `get_cronograma` DEBE retornar el mismo correlativo que el cliente ve en su cronograma.

**Morosidad — fórmulas exactas (confirmadas Naomi 2026-06-10, fuente: definiciones-naomi-2026-06-10.md §1)**:
```python
# scenario.py or tools.py — moratoria calculation (vencido only)
def calcular_penalidad(saldo_capital_inicial: float, semana: int) -> float:
    # semana 1 (dias_overdue 1-7): 0.008% × saldo_capital_inicial, redondear hacia arriba al 0.1
    # semana 2 (dias_overdue 8-14): 0.016% × saldo_capital_inicial
    tasa = 0.00008 if semana == 1 else 0.00016
    raw = saldo_capital_inicial * tasa
    if semana == 1:
        import math
        return math.ceil(raw * 10) / 10  # round up to nearest 0.10 — ej. 5.66 → 5.70
    return raw

def calcular_interes_compensatorio(
    amortizacion_cuota: float,
    tasa_interes_mensual: float,  # as decimal, e.g. 0.03 for 3%
    dias_transcurridos: int,       # (fecha_pago_estimada - fecha_vencimiento_cuota).days
) -> float:
    return amortizacion_cuota * (tasa_interes_mensual / 30) * dias_transcurridos
```

**Canonical gestión table** `GENERAL.mibotair_results` is ETL-fed only (~72-min lag); the bot NEVER direct-writes it (`cobranza_rw` has no write grant there; only `cobranza_analytics` Select+Load). The optional active-promise read needs a read account with `GENERAL` access — `cobranza_ro` is scoped to the project DB and cannot see `GENERAL`.

## Testing Strategy

| Layer | What | Approach |
|-------|------|----------|
| Unit | `classify_credit_state` thresholds (al_dia/por_vencer/vencido, undetermined→al_dia, window boundary) | pure-fn table tests |
| Unit | compromiso >2d escalation; canonical-gestión payload (n1/n2/n3 + promise_date/amount + extra_data) | pure-fn over profile+fecha; mock registry |
| Unit | `resolve_contrato` dedups titular+garante → one credit | fixture with duplicate person-rows |
| Integration | credit-state-specific response for real cases P03638/P03700/P03871/P03886 | profile→router→template |
| Integration | comprobante n_cuota + pre-question routing; abono→compromiso chain → register_gestion called | session_state walk; stubbed registry |

## Migration / Rollout

No data migration. All fronts additive behind the credit-state/intent layer. Classifier defaults to `al_dia` (current behavior) when state undetermined → safe rollback per `git revert`. The compromiso gestión registration is a thin stub behind the gestión-registration API integration: until the endpoint/auth/payload mapping is agreed, the conversational part (detect/validate/confirm) ships and the registration call is a no-op stub.

## Open Questions (need Naomi/business sign-off, non-blocking)

~~- [ ] Confirm "próxima a vencer" window = 5 days.~~ ✅ CONFIRMADO 2026-06-10.
~~- [ ] Confirm horario de atención values for config.~~ ✅ CONFIRMADO 2026-06-10: Lun-Vie 9:00–18:30, refrigerio 13:00–14:00, feriados en `feriados_peru_2026.json`.

**Pendiente aún**:
- [ ] Caso real "cuota próxima a vencer" — no disponible en datos actuales. Validar con data sintética (testing limitation).

## Coordination Dependency (gestión-registration API)

- [ ] **Platform / ChatHub team**: identify and agree the gestión-registration API — endpoint, auth, and payload mapping for `n1/n2/n3` + `promise_date`/`promise_amount` + bot agent (`BOT ADA`) + `extra_data`. This is the SAME path ChatHub/voicebot/human agents use; the ETL then lands the row in `GENERAL.mibotair_results`. Until agreed, the conversational part ships and `register_gestion` is a thin stub. (Supersedes the obsolete `/chat` ChatHub-event dependency.)
- [ ] **(Optional, low pri)** Read grant on a `GENERAL`-scoped account to enable the active-promise read for conversational context.
