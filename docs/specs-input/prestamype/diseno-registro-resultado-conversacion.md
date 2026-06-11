# Diseño — Registro de resultado de conversación (bot-owned, tenant-agnostic)

**Fecha:** 2026-06-10
**Contexto:** El bot registra el resultado terminal de cada conversación en su propia BD (Postgres)
y lo replica a Doris (analítico). NO escribe a `GENERAL.mibotair_results` (ETL-fed, canónica,
owned por otro app). La homologación bot→tipificación-cliente (n1/n2/n3 u otra) es una capa
externa **por tenant**, fuera de este registro.

## Principios (definidos por Ricky 2026-06-10)
1. **Genérico / estándar**: vocabulario de cobranza, no de Prestamype.
2. **Agnóstico al tenant**: el mismo catálogo sirve para cualquier cliente del chatbot de cobranza.
3. **Atinente a las capacidades del bot**: cada outcome corresponde a algo que el bot realmente hace.
4. **Escalable**: enums versionados (`schema_version`); agregar un valor nuevo no rompe consumidores.

---

## Catálogo de OUTCOME (terminal — categoría estándar)

| outcome | Significado | Capacidad del bot que lo produce |
|---|---|---|
| `identified` | Identidad verificada, sin acción terminal posterior (consulta y cierre) | Identificación DNI / ID contrato |
| `identification_failed` | No se pudo verificar identidad (fail-closed / max retries) | Gate de identidad |
| `info_provided` | Se entregó información solicitada y la conversación cerró ahí | Consulta deuda, cronograma, cuotas, cuentas bancarias, fecha venc. |
| `payment_proof_submitted` | El cliente subió un comprobante | Flujo comprobante (con n_cuota) |
| `payment_commitment_registered` | Se registró un compromiso de pago dentro de ventana (≤2 días) | Compromiso de pago |
| `escalated_to_agent` | Derivado a asesor humano | Cualquier derivación |
| `not_understood` | Fallback agotado (2-strike) sin comprender | No comprendida |
| `unresolved` | La conversación terminó sin desenlace claro (idle/abandono) | Default si nada terminal ocurrió |

## Catálogo de OUTCOME_REASON (sub-motivo estándar, opcional)

Detalla el "por qué", sobre todo para `escalated_to_agent`:

| outcome_reason | Aplica a |
|---|---|
| `cannot_pay` | escalated_to_agent (no puede pagar → motivo) |
| `requested_alternatives` | escalated_to_agent (alternativas/reclamo) |
| `commitment_beyond_window` | escalated_to_agent (fecha > 2 días) |
| `wants_full_payment` | escalated_to_agent (deuda total → Sí) |
| `pay_installment` | escalated_to_agent (realizar pago cuota — vencido) |
| `proof_other_installment` | escalated_to_agent (comprobante pre-pregunta = No) |
| `explicit_agent_request` | escalated_to_agent (pidió asesor directo) |
| `out_of_hours` | escalated_to_agent (fuera de horario / feriado) |
| `fallback_exhausted` | escalated_to_agent o not_understood (2do strike) |
| `max_identification_retries` | identification_failed |
| `null` | outcome auto-explicativo (no requiere motivo) |

## Eje CAPABILITIES_USED (lista, no terminal — analítica de uso)

Qué capacidades ejerció el cliente en la sesión (array, multivalor):
`identificacion`, `consulta_deuda`, `deuda_total`, `cronograma`, `cuotas`,
`cuentas_bancarias`, `fecha_vencimiento`, `comprobante`, `compromiso`,
`pago`, `multicredito`, `horario_feriado`.

---

## Estructura del registro

Postgres `{schema}.gestiones` (source of truth bot-side) + Doris `cobranza_analytics.bot_gestiones` (réplica):

| Columna | Tipo | Origen |
|---|---|---|
| `conversation_id` | TEXT | router |
| `tenant_id` | TEXT | request |
| `project_uid` | TEXT | tenant.config |
| `channel` | TEXT | request |
| `document` | TEXT | debt_context.dni |
| `account_id` | TEXT | debt_context.account_id |
| `credit_state` | TEXT | session_state (al_dia/por_vencer/vencido) |
| `outcome` | TEXT (enum) | derivado al cierre |
| `outcome_reason` | TEXT (enum, nullable) | derivado al cierre |
| `capabilities_used` | JSONB / ARRAY | acumulado por turno |
| `escalated` | BOOL | was_escalated() |
| `commitment_date` | DATE (nullable) | flujo compromiso |
| `commitment_amount` | DECIMAL (nullable) | flujo compromiso |
| `selected_credit_id` | TEXT (nullable) | multicrédito |
| `schema_version` | SMALLINT | constante (=1) |
| `created_at` | TIMESTAMP | primer turno |
| `closed_at` | TIMESTAMP (nullable) | intent terminal |

## Derivación del outcome (regla, al cierre)

Prioridad (primer match gana):
1. identity gate falló → `identification_failed` / `max_identification_retries`
2. compromiso registrado → `payment_commitment_registered`
3. comprobante subido → `payment_proof_submitted`
4. `was_escalated()` → `escalated_to_agent` + `outcome_reason` según el intent que disparó la derivación
5. 2do strike fallback → `not_understood` (`fallback_exhausted`)
6. entregó info y cerró → `info_provided`
7. identificado sin más → `identified`
8. nada de lo anterior → `unresolved`

## Trigger (decidido 2026-06-10) — AMBOS

- **Terminal inmediato**: al detectar un intent terminal (compromiso registrado, comprobante
  subido, `was_escalated()`, 2º strike de fallback, identity fail) → set `outcome` +
  `outcome_reason` + `closed_at` en el snapshot, y emite el evento terminal al journal.
- **Barrido por inactividad**: un job/worker cierra como `unresolved` (set `outcome` + `closed_at`)
  las conversaciones sin actividad por N minutos (TTL configurable por tenant). Cubre abandonos.

## Modelo de datos — 3 capas

| Capa | Qué | Construir |
|---|---|---|
| **1 — Ping-pong** | mensajes user/bot | **REUSA**: Postgres `conversations.history` (JSONB) + Doris `bot_interactions` (por turno) |
| **2 — Costos LLM** | input/output tokens + costo | **REUSA**: Doris `bot_llm_usage` (`input_tokens`, `output_tokens`, `cost_usd`) por `interaction_id` |
| **3 — Gestión / resultado** | outcome + qué hizo el usuario | **NUEVO**: journal + snapshot (abajo) |

Las 3 capas se unen por `conversation_id` / `session_id` (join en Doris para BI: pingpong + costo + outcome).

### Journal + snapshot (decidido — NO SCD2)

**`gestion_events`** (append-only journal — la fuente histórica de "qué hizo el usuario"):

| Columna | Tipo |
|---|---|
| `event_id` | BIGINT / UUID PK |
| `conversation_id` | TEXT |
| `ts` | TIMESTAMP |
| `event_type` | TEXT (enum: `capability_used`, `credit_state_set`, `terminal`, `escalation`, `commitment`, `proof`) |
| `intent` | TEXT (nullable) |
| `capability` | TEXT (nullable — del eje CAPABILITIES_USED) |
| `payload` | JSONB |

**`gestiones`** (snapshot CURRENT — upsert, 1 fila por conversación; ver tabla "Estructura del registro" abajo).
Derivable desde el journal; el journal es la verdad histórica, el snapshot es la conveniencia de consulta.

Ambas tablas: **Postgres** `{schema}` (source of truth bot-side) + réplica a **Doris** `cobranza_analytics`
vía el `analytics_sink._async_write()` que ya existe.

> **Por qué journal en vez de SCD2**: el journal append-only ya es histórico por naturaleza y captura la
> secuencia exacta de acciones; desde él se deriva cualquier snapshot en el tiempo T. Menos complejidad que
> versionar la fila (valid_from/valid_to/is_current) y mejor encaje con un fact append-only en Doris/BI.

## Homologación a tipificación cliente (FUERA de este slice)

Tabla/config aparte, **por tenant**: `(outcome, outcome_reason) → {n1, n2, n3}` (Prestamype/mibotair) o
el esquema que cada cliente requiera. Se aplica en un paso ETL/mapeo posterior, no en el bot.
Esto materializa "agnóstico al tenant" + "capacidad de escalar".
