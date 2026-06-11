# Spec: Prestamype Cobranza — Scenario-Based Flows & Conversational Spec
# Change: prestamype-cobranza-flujos-escenarios
# Source: Naomi Ramos (nramos@prestamype.com) 2026-06-05 + proposal engram 12588
# Revised: 2026-06-06 — scope delta v2 (mibotair_results findings)
# Revised: 2026-06-10 — definiciones confirmadas por Naomi Ramos (definiciones-naomi-2026-06-10.md)
# Revised: 2026-06-11 — bot-owned compromiso, 1-intent SCR-02, ID-contrato+DNI auth (decisiones Ricky)

> **Terminology note — CRITICAL**: `credit_state` (`al_dia` / `por_vencer` / `vencido`) is the
> INPUT routing axis derived from the debt profile. Gestión typification codes (`n1`/`n2`/`n3`
> in `GENERAL.mibotair_results`) are an OUTPUT/external axis and are OUT OF SCOPE for this
> change — tipification homologation to client codes is a future external mapping layer.
> These two axes MUST NOT be conflated in code or config.

> Defaults usados y confirmaciones de negocio (fuente: definiciones-naomi-2026-06-10.md):
> - "Próxima a vencer" window: **5 días** (parametrizable via tenant config) — **CONFIRMADO Naomi 2026-06-10**
> - WhatsApp proactive templates: **OUT OF SCOPE** — owned by ChatHub (external outbound system); NOT a separate change here
> - Compromiso registration: **bot-owned** — stored in the bot's own `gestiones` table (`commitment_date` + `commitment_amount` columns, outcome `payment_commitment_registered`, journal event `commitment`). NO writes to `GENERAL.mibotair_results`. NO gestión-registration API call. NO n1/n2/n3. — **DECISIÓN Ricky 2026-06-11**: ≤2 días → register (bot-owned); >2 días → asesor; on failure → asesor, never confirm
> - Horario de atención: **Lun-Vie 9:00–18:30, refrigerio 13:00–14:00** (asesores NO disponibles durante refrigerio). Feriados: ver `feriados_peru_2026.json`. — **CONFIRMADO Naomi 2026-06-10**
> - ID-contrato uniqueness: **one contract ID = one credit**; multiple person-rows (titular+garante) collapse to the same credit — **CONFIRMADO Naomi 2026-06-10**
> - ⚠️ **CORRECCIÓN identificación (2026-06-10)**: la info de deuda se muestra ÚNICAMENTE a los involucrados del préstamo (titular y garante). Antes se asumía acceso sin distinción de rol — INCORRECTO. Ver IDC-01 y §3 de definiciones-naomi-2026-06-10.md.

---

## New Capabilities

---

### Capability: cobranza-scenario-routing

---

#### Requirement: SCR-01 — Credit State Classification

> **Note**: the states `al_dia` / `por_vencer` / `vencido` are the INPUT routing axis
> (derived from debt profile). They are DISTINCT from the gestión typification `n1`/`n2`/`n3`
> stored in `GENERAL.mibotair_results`, which is the OUTPUT recording axis.

The system MUST derive a credit state (`al_dia`, `por_vencer`, or `vencido`) from the verified debt profile before generating any response or option menu.

Classification rules (evaluated in order):
- `vencido`: `cuotas_vencidas >= 1` OR `days_overdue > 0`
- `por_vencer`: `cuotas_vencidas == 0` AND `days_until_next_due <= 5` (window is tenant-configurable)
- `al_dia`: all other verified profiles (credit is current, next due date is >5 days away)

If profile data is unavailable or verification has not completed, the system MUST NOT classify and MUST NOT expose credit-state-specific options.

##### Scenario: al_dia — credit is current

- GIVEN a verified user whose `cuotas_vencidas == 0` AND `days_until_next_due > 5`
- WHEN the conversation enters a cobranza flow
- THEN the system assigns credit state = `al_dia`
- AND presents the `al_dia` main menu: [Consultar cronograma, Subir comprobante, Hablar con un asesor]

##### Scenario: por_vencer — next due within window

- GIVEN a verified user whose `cuotas_vencidas == 0` AND `days_until_next_due <= 5` AND `days_until_next_due >= 0`
- WHEN the conversation enters a cobranza flow
- THEN the system assigns credit state = `por_vencer`
- AND presents the response: "Tu próxima cuota vence el {fecha} por S/{monto}"
- AND presents options: [Subir comprobante, Ver cuentas bancarias, Consultar cronograma, Hablar con un asesor]

##### Scenario: vencido — one or more overdue installments

- GIVEN a verified user whose `cuotas_vencidas >= 1` OR `days_overdue > 0`
- WHEN the conversation enters a cobranza flow
- THEN the system assigns credit state = `vencido`
- AND presents the overdue installment list (N°, fecha vencimiento, días de atraso) for each overdue installment
- AND presents options: [Realizar pago, Pago parcial, Compromiso de pago, Ver alternativas, Hablar con un asesor]

##### Scenario: classification blocked — unverified session

- GIVEN a session where identity verification has not completed
- WHEN any cobranza flow is triggered
- THEN the system MUST NOT expose credit-state-specific content
- AND MUST redirect to the identity gate

---

#### Requirement: SCR-02 — Consulta de Deuda Main Response (flows 1–3)

The system MUST handle the single intent `consulta_deuda` ("¿cuánto debo?" / "consultar deuda") with
**internal branching on `session_state["credit_state"]`** — this is ONE intent, not three variants.
The response message and option menu differ per state; the intent binding in `responses.json` is
declared once and branches internally.

- `al_dia`: "Tu crédito está al día. ¿Qué deseas hacer?" + `al_dia` option menu
- `por_vencer`: "Tu próxima cuota vence el {fecha_venc} por S/{cuota}." + `por_vencer` option menu
- `vencido`: Enumerate overdue installments (N°, fecha_venc, days_overdue each) + `vencido` option menu

**Design note**: do NOT create separate intent keys `consulta_deuda_al_dia` / `consulta_deuda_por_vencer` /
`consulta_deuda_vencido` as top-level bindings. One binding, internal branch by `credit_state`.

##### Scenario: al_dia consulta deuda

- GIVEN credit state = `al_dia`
- WHEN user triggers `consulta_deuda` intent
- THEN bot replies "Tu crédito está al día." with `al_dia` option menu

##### Scenario: por_vencer consulta deuda

- GIVEN credit state = `por_vencer`
- WHEN user triggers `consulta_deuda` intent
- THEN bot replies "Tu próxima cuota vence el {fecha_venc} por S/{cuota}." with `por_vencer` option menu

##### Scenario: vencido consulta deuda — multiple overdue

- GIVEN credit state = `vencido` with 2 overdue installments
- WHEN user triggers `consulta_deuda` intent
- THEN bot lists both installments with their N°, vencimiento date, and days overdue
- AND presents `vencido` option menu

---

#### Requirement: SCR-03 — Consulta Deuda Total (flow: "Consultar deuda total")

The system MUST respond to "deuda total" intent by asking "¿Deseas pagar toda tu deuda?" with options [Sí → asesor with updated total, No → option menu]. This flow applies to all credit states.

##### Scenario: user wants to pay total debt

- GIVEN any verified credit state
- WHEN user triggers "deuda total" intent AND confirms Sí
- THEN bot escalates to asesor with the total outstanding amount

##### Scenario: user declines total payment

- GIVEN any verified credit state
- WHEN user triggers "deuda total" intent AND selects No
- THEN bot presents the credit-state-appropriate option menu

---

### Capability: cobranza-compromiso-pago

---

#### Requirement: CMP-01 — Capture, Validate, and Register Payment Commitment (vencido only)

> **DECISIÓN Ricky 2026-06-11**: compromiso is bot-owned. Stored in the bot's `gestiones` table
> (conversation-result-tracking change). NO gestión-registration API. NO `GENERAL.mibotair_results`
> writes. NO n1/n2/n3. Tipification homologation to client codes is a future external mapping layer,
> out of scope for this change.

The system MUST offer the "Compromiso de pago" option only when credit state = `vencido`. When selected, the system MUST ask "¿En qué fecha realizarás el pago?" and handle the response:
- If the provided date is same-day or within 2 days: write the commitment to the bot's `gestiones` table, then confirm to the user (CMP-02).
- If the provided date is more than 2 days away: escalate to asesor.

**Bot-owned storage** (written to `gestiones` snapshot row for this conversation):

| Field | Value |
|-------|-------|
| `commitment_date` | ISO date provided by user |
| `commitment_amount` | overdue amount from debt profile |
| outcome | `payment_commitment_registered` |
| journal event | `commitment` |

> **Constraints**:
> - MUST NOT write to `GENERAL.mibotair_results` (ETL-fed, no write perms).
> - MUST NOT call any external gestión-registration API.
> - MUST NOT use n1/n2/n3 typification codes — that mapping is out of scope.
> - On any write failure: MUST NOT confirm the commitment to the user; escalate to asesor.

##### Scenario: commitment within 2-day window — registered bot-owned

- GIVEN credit state = `vencido` and user selects "Compromiso de pago"
- WHEN user provides a date that is today or within 2 days
- THEN the system writes `commitment_date` and `commitment_amount` to the bot's `gestiones` table with outcome `payment_commitment_registered` and journal event `commitment`
- AND on success, proceeds to CMP-02 confirmation

##### Scenario: commitment beyond 2-day window

- GIVEN credit state = `vencido` and user selects "Compromiso de pago"
- WHEN user provides a date more than 2 days from today
- THEN the system MUST NOT write to `gestiones`
- AND escalates to asesor with the user's stated date as context

##### Scenario: gestiones write failure

- GIVEN the bot's `gestiones` write returns an error
- WHEN the bot attempts to register the commitment
- THEN the system MUST NOT confirm the commitment to the user
- AND escalates to asesor with context (date, amount)

##### Scenario: commitment — invalid date input

- GIVEN credit state = `vencido` and user is prompted for commitment date
- WHEN user provides an unparseable or past date
- THEN the system asks again once
- AND if still invalid, escalates to asesor

##### Scenario: commitment option blocked for al_dia / por_vencer

- GIVEN credit state = `al_dia` or `por_vencer`
- WHEN user attempts to access "Compromiso de pago" (e.g., via direct text)
- THEN the system MUST NOT present or process the compromiso flow
- AND redirects to the credit-state-appropriate option menu

---

#### Requirement: CMP-02 — Compromiso Confirmation Message (xlsx row 9)

After CMP-01 writes to `gestiones` successfully, the bot MUST confirm the commitment to the user in the same conversational turn. The confirmation MUST include the fecha provided. Reminder scheduling and proactive outbound notification are owned by ChatHub — out of scope here.

##### Scenario: confirmation displayed immediately after bot-owned write

- GIVEN the `gestiones` write in CMP-01 succeeded
- THEN bot displays: "Registramos tu compromiso de pago para el {fecha}. Te enviaremos un recordatorio ese día."
- AND returns to the credit-state-appropriate option menu

---

#### Requirement: CMP-03 — Read Active Promise (OPTIONAL — OUT OF SCOPE for this change)

> **Priority: OUT OF SCOPE. Source for a future read is TBD — the bot now owns commitments in its
> own `gestiones` table; reading from `GENERAL.mibotair_results` is no longer the design intent.
> A future change may surface the active promise from `gestiones` directly (no ETL lag, no read
> grant needed). Do NOT implement this requirement in this change.**

The system MAY, in a future change, read the most recent active promise from the bot's `gestiones`
table to surface context to the user (e.g., "Ya tienes un compromiso registrado para el {fecha}").

##### Scenario: active promise found — surface as context (FUTURE)

- GIVEN credit state = `vencido` and an active commitment row exists in `gestiones` for this user
- WHEN user enters the cobranza flow
- THEN bot MAY display: "Tienes un compromiso de pago registrado para el {promise_date}."
- AND presents the `vencido` option menu normally

##### Scenario: gestiones unavailable — silent skip (FUTURE)

- GIVEN the read from `gestiones` fails or times out
- THEN the system proceeds without surfacing promise context (no error shown to user)

---

### Capability: cobranza-informational-intents

---

#### Requirement: INF-01 — Cronograma de Pagos

The system MUST respond to "cronograma" intent by displaying the installment schedule derived from Doris (`batch_pagos_v2_bronze`). The schedule MUST show N°, fecha_vencimiento, and monto per installment. All data MUST come from the verified profile — no invented numbers.

##### Scenario: cronograma available

- GIVEN a verified user with installment schedule data in Doris
- WHEN user triggers "cronograma" intent
- THEN bot displays the full installment list (N°, fecha_venc, monto) for the credit

##### Scenario: cronograma unavailable

- GIVEN a verified user but Doris schedule is empty or unavailable
- WHEN user triggers "cronograma" intent
- THEN bot escalates to asesor with a message indicating schedule is unavailable

---

#### Requirement: INF-02 — Fecha de Vencimiento del Contrato

The system MUST respond to "fecha vencimiento contrato" intent with the contract end date from the verified debt profile.

##### Scenario: contract end date available

- GIVEN a verified user
- WHEN user triggers "fecha venc. contrato" intent
- THEN bot responds with the contract end date from Doris profile

---

#### Requirement: INF-03 — N° Cuotas Pagadas y Pendientes

The system MUST respond to "cuántas cuotas pagué / cuántas me faltan" intent with the count of paid and pending installments derived from the Doris schedule. Values MUST be calculated, not hardcoded.

##### Scenario: cuotas pagadas

- GIVEN a verified user
- WHEN user asks "¿Cuántas cuotas he pagado?"
- THEN bot responds with the count of paid installments from the schedule

##### Scenario: cuotas pendientes

- GIVEN a verified user
- WHEN user asks "¿Cuántas cuotas me faltan?"
- THEN bot responds with the count of pending installments from the schedule

---

#### Requirement: INF-04 — Cuentas Bancarias para Pago

The system MUST respond to "cuentas bancarias" intent with the bank account information for each credit: inversionista name, account number, and CCI. Multi-credit users MUST see per-credit account data (see cobranza-multicredit-display). This intent applies to all credit states.

##### Scenario: single credit — cuentas bancarias

- GIVEN a verified user with one active credit
- WHEN user triggers "cuentas bancarias" intent
- THEN bot displays: inversionista, cuenta bancaria, CCI for that credit

##### Scenario: multi-credit — cuentas bancarias

- GIVEN a verified user with multiple active credits
- WHEN user triggers "cuentas bancarias" intent
- THEN bot displays per-credit rows with inversionista, cuenta bancaria, CCI
- AND labels each row with the credit identifier

---

#### Requirement: INF-05 — "Ya Pagué" Intent

When a user indicates they have already paid, the system MUST prompt them to upload a comprobante. This intent applies to all credit states and is the entry point to the comprobante flow.

##### Scenario: ya pagué — redirects to comprobante

- GIVEN a verified user (any credit state)
- WHEN user triggers "ya pagué" intent
- THEN bot asks user to upload their payment proof (comprobante)
- AND the comprobante flow proceeds (see cobranza-comprobante requirement)

---

#### Requirement: INF-06 — No Puede Pagar Intent

The system MUST respond to "no puedo pagar" intent by asking for the reason, then escalating to asesor. This intent applies to all credit states.

##### Scenario: no puede pagar

- GIVEN a verified user (any credit state)
- WHEN user triggers "no puedo pagar" intent
- THEN bot asks: "¿Cuál es el motivo por el que no puedes pagar?"
- THEN escalates to asesor with user's stated reason

---

#### Requirement: INF-07 — Alternativas Intent

The system MUST respond to "alternativas" intent by escalating directly to asesor. This intent applies to all credit states.

##### Scenario: alternativas — escalate

- GIVEN a verified user (any credit state)
- WHEN user triggers "alternativas" / "reclamo" / "asesor" intent
- THEN bot escalates to asesor

---

#### Requirement: INF-08 — Fecha Cae en Domingo o Feriado (al_dia / por_vencer only)

> **Feriados confirmados (Naomi 2026-06-10)**: fuente canónica es `feriados_peru_2026.json`. El flujo debe consultarlo para determinar si una fecha cae en feriado o domingo. Fuente: definiciones-naomi-2026-06-10.md §5.

The system MUST respond to "qué pasa si la fecha vence en domingo/feriado" intent with the applicable business rule about due date shifts. This intent is only surfaced for credit states `al_dia` and `por_vencer`. The system MUST use `feriados_peru_2026.json` as the authoritative calendar for Peruvian national holidays.

##### Scenario: al_dia / por_vencer — due date on holiday

- GIVEN credit state = `al_dia` or `por_vencer`
- WHEN user asks about domingo or feriado due date
- THEN bot responds with the applicable business rule (e.g., next business day)

##### Scenario: vencido — this intent is not surfaced

- GIVEN credit state = `vencido`
- WHEN user asks about domingo or feriado
- THEN bot redirects to the `vencido` option menu (overdue context makes this irrelevant)

---

#### Requirement: INF-09 — Fuera de Horario

> **Horario CONFIRMADO Naomi 2026-06-10** (definiciones-naomi-2026-06-10.md §5): Lun-Vie 9:00–18:30. Refrigerio 13:00–14:00 = asesores NO disponibles. Feriados nacionales 2026: ver `feriados_peru_2026.json`.

The system MUST detect out-of-hours sessions (based on tenant-configured business hours) and respond with a message indicating the service hours and offering to leave a message or contact during hours. The system MUST also treat the refrigerio window (13:00–14:00) as out-of-hours for asesor escalation. Payments falling on a feriado or domingo must consult `feriados_peru_2026.json` to determine the correct business rule (see INF-08).

**Configured hours** (store in tenant config — source: definiciones-naomi-2026-06-10.md):
- Days: Lunes a Viernes
- Start: 09:00
- End: 18:30
- Refrigerio: 13:00–14:00 (asesor unavailable)
- Holidays: `feriados_peru_2026.json` (canonical calendar source)

##### Scenario: message received outside business hours

- GIVEN the current time is outside Lun-Vie 9:00–18:30 OR falls within 13:00–14:00 refrigerio
- WHEN any message is received from a user
- THEN bot responds with service hours and a callback option
- AND MUST NOT process any cobranza flow actions requiring an asesor

##### Scenario: payment due date falls on feriado or domingo

- GIVEN a user with credit state `al_dia` or `por_vencer` asking about a due date that falls on a feriado (per `feriados_peru_2026.json`) or domingo
- WHEN user triggers INF-08 intent or the due date check runs
- THEN bot applies the applicable business rule (next business day) — see INF-08

---

#### Requirement: INF-10 — No Comprendida (2-strike fallback)

The system MUST handle unrecognized input with a 2-strike escalation:
- Strike 1: "No entendí bien. ¿Puedes reformular tu consulta?"
- Strike 2 (same session, same consecutive unrecognized turn): escalate to asesor.

##### Scenario: first unrecognized input

- GIVEN a verified user
- WHEN user sends a message that does not match any known intent
- THEN bot responds with the "no comprendida" rephrasing prompt

##### Scenario: second consecutive unrecognized input

- GIVEN a verified user who already received a strike-1 response
- WHEN user sends another message that does not match any known intent
- THEN bot escalates to asesor

---

#### Requirement: INF-11 — Realizar Pago Cuota (vencido only)

When a user selects "Realizar pago" (exact cuota amount) from the `vencido` menu, the system MUST escalate to asesor with the exact overdue amount and applicable concepts (interest, fees). The system MUST NOT present a payment link or self-service payment.

---

#### Requirement: INF-12 — Cálculo de Conceptos Moratorios (vencido only)

> **NUEVO — definiciones confirmadas por Naomi 2026-06-10 (definiciones-naomi-2026-06-10.md §1).** Antes TBD.

When displaying overdue amounts to a user with credit state = `vencido`, the system MUST calculate and show the applicable moratoria concepts using the exact formulas below. All values MUST come from the verified debt profile — no invented numbers.

**Data source (validado 2026-06-10, Doris project_QUIdI0iwQY0l3pJwRKLB)**:

| Variable de la fórmula | Columna Doris | Tabla | Notas |
|---|---|---|---|
| `saldo_capital_inicial` | `saldo_por_cancelar` | `batch_pagos_v2_bronze` | ⚠️ **Mapeo a confirmar**: `saldo_por_cancelar` es el saldo PENDIENTE, no necesariamente el capital INICIAL. Verificar con negocio/Naomi cuál de los dos pide la fórmula. |
| `amortizacion_cuota` | `amortizacion_esperada_original` | `batch_pagos_v2_bronze` | `double`, 0.2% null. NO usar `amortizacion_esperada_actualizado` (95% null). |
| `tasa_interes_mensual` | `tasa_de_interes` | `batch_asignacion_review_bronze` | Varchar `"X.XX%"` → `CAST(REPLACE(tasa_de_interes,'%','') AS DOUBLE)/100`. JOIN `batch_pagos_v2_bronze.codigo_contrato = batch_asignacion_review_bronze.id_credito` (cobertura 99.8%). |

**Penalidades** (sobre saldo capital inicial) — regla inductiva:
- La penalidad crece **0.008% por cada semana de atraso** (progresión aritmética, sin tope conocido). Para la semana `N` de atraso: `penalidad = N × 0.008% × saldo_capital_inicial`.
  - 1ª semana (N=1): `0.008% × saldo_capital_inicial`
  - 2ª semana (N=2): `0.016% × saldo_capital_inicial`
  - 3ª semana (N=3): `0.024% × saldo_capital_inicial`, y así sucesivamente.
- `N = ceil(dias_overdue / 7)` (días 1–7 → semana 1; 8–14 → semana 2; 15–21 → semana 3; …).
- El resultado se redondea **hacia arriba (ceil) al décimo de sol más cercano** (ej. S/5.66 → S/5.70). **CONFIRMADO Ricky 2026-06-10.**

> ⚠️ **Assumption pendiente de validar con Naomi**: la regla inductiva (0.008% por semana, sin tope, sin cambio de tasa a partir de la 3ª semana) es una extrapolación de Onbotgo del patrón semana 1 / semana 2 que dio Naomi. El correo solo especificó las 2 primeras semanas. Confirmar con Naomi si existe tope, cambio de tasa, o capitalización a partir de la 3ª semana.

**Interés compensatorio**:
```
interes_compensatorio = amortizacion_cuota × (tasa_interes_mensual / 30) × dias_transcurridos
```
donde `dias_transcurridos` = días entre fecha de vencimiento de la cuota y fecha de pago estimada.

##### Scenario: vencido — moratoria penalidad (regla inductiva, cualquier semana)

- GIVEN credit state = `vencido` with `dias_overdue >= 1`
- WHEN user views overdue detail or selects "Realizar pago"
- THEN bot computes `N = ceil(dias_overdue / 7)` and `penalidad = ceil_decimo(N × 0.008% × saldo_capital_inicial)` (ceil al décimo de sol)
- AND displays it alongside the base amount
- EXAMPLES: N=1 (días 1–7) → 0.008%; N=2 (días 8–14) → 0.016%; N=3 (días 15–21) → 0.024%

##### Scenario: vencido — interés compensatorio

- GIVEN credit state = `vencido`
- WHEN user views overdue amount
- THEN bot computes `interes_compensatorio = amortizacion_cuota × (tasa_interes_mensual / 30) × dias_transcurridos` where `dias_transcurridos` = (fecha_pago_estimada − fecha_vencimiento_cuota).days
- AND displays the computed interest alongside penalidad and base amount

##### Scenario: realizar pago cuota — vencido

- GIVEN credit state = `vencido`
- WHEN user selects "Realizar pago" from the `vencido` menu
- THEN bot escalates to asesor, providing context: overdue amount + concepts

---

### Capability: cobranza-id-contrato-identification

---

#### Requirement: IDC-01 — Contract ID + DNI as Dual-Factor Identifier

> ⚠️ **CORRECCIÓN vs. supuesto previo (Naomi 2026-06-10)**: la info de deuda se muestra ÚNICAMENTE
> a los involucrados del préstamo — titular y garante.
>
> **DECISIÓN Ricky 2026-06-11**: identificación por ID-contrato requiere TAMBIÉN el DNI del usuario.
> El acceso se concede ÚNICAMENTE si ese DNI pertenece al titular o garante del crédito en Doris.
> Sin DNI matching, no se revela ninguna información del crédito — fail-closed.

The system MUST require BOTH a contract ID (ID de contrato / ID crédito) AND the user's DNI to
identify via this path. One contract ID corresponds to exactly one credit. A Doris credit may have
multiple person-rows (titular and garante); the system MUST resolve them to the single credit.

**Access rule**: after the user provides (contract_id, DNI), the system MUST verify that the provided
DNI matches the titular or garante of that contract in Doris. If the DNI is NOT in `{titular, garante}`,
the system MUST fail-closed — no credit info revealed — and offer retry or asesor escalation.

##### Scenario: successful identification — contract ID + matching DNI

- GIVEN an unverified user
- WHEN user provides a valid contract ID AND a DNI that matches the titular or garante of that contract in Doris
- THEN the system verifies identity and loads the debt profile for that contract
- AND proceeds to credit state classification (SCR-01)

##### Scenario: contract ID valid but DNI does not match titular or garante

- GIVEN an unverified user who provides a valid contract ID and a DNI
- WHEN the provided DNI is NOT in the titular or garante rows for that contract in Doris
- THEN the system MUST NOT reveal that the contract exists
- AND MUST NOT reveal any credit information
- AND fails-closed: prompts retry or asesor escalation

##### Scenario: contract ID resolves titular and garante to same credit

- GIVEN a contract ID whose Doris record has both a titular row and a garante row
- WHEN the system verifies a DNI that matches either row
- THEN the system MUST return exactly one credit profile (not two separate sessions)
- AND MUST NOT expose person-role distinctions (titular vs. garante) to the chatbot flow

##### Scenario: contract ID not found

- GIVEN an unverified user
- WHEN user provides a contract ID that does not match any Doris record
- THEN the system MUST NOT reveal whether the ID exists
- AND prompts user to retry or use DNI

##### Scenario: contract ID + DNI fails after max retries

- GIVEN an unverified user who has reached the max retry limit via this path
- WHEN user provides another attempt
- THEN the system escalates to asesor (same behavior as DNI fail-closed gate)

---

## Modified Capabilities

---

### Capability: cobranza-comprobante (delta)

---

#### Requirement: CPR-01 — Comprobante with N° Cuota and Pre-Question

The comprobante upload flow MUST be extended:

1. Before accepting the comprobante image, the system MUST ask: "¿El comprobante corresponde al pago de tu próxima cuota?"
   - Sí → continue with the comprobante flow
   - No → escalate to asesor (the comprobante is for a different installment or purpose)

2. The comprobante popup / data collection MUST capture: foto, inversionista, monto, **N° cuota** (new), ID crédito (optional).
   **N° cuota definition (confirmado Naomi 2026-06-10)**: correlativo 1, 2, 3… — coincide con la columna "Nro Cuotas" del archivo de pagos de Prestamype y con lo que el cliente ve en su cronograma. El bot debe mostrar/aceptar el mismo correlativo que aparece en el cronograma. Fuente: definiciones-naomi-2026-06-10.md §4.

3. When the comprobante flow is reached via "Pago parcial" (credit state = `vencido`), after successful submission, the system MUST chain directly to the Compromiso de pago flow.

(Previously: comprobante captured foto + monto + inversionista + ID crédito optional; no pre-question; no n_cuota; no pago-parcial chain)

##### Scenario: comprobante — pre-question Sí

- GIVEN a user entering the comprobante flow (any nivel)
- WHEN user is asked "¿El comprobante corresponde al pago de tu próxima cuota?"
- AND user answers Sí
- THEN bot proceeds to collect foto, inversionista, monto, N° cuota, ID crédito (optional)

##### Scenario: comprobante — pre-question No

- GIVEN a user entering the comprobante flow
- WHEN user answers No to the pre-question
- THEN bot escalates to asesor

##### Scenario: comprobante — n_cuota captured

- GIVEN a user who answered Sí to the pre-question
- WHEN bot collects comprobante data
- THEN N° cuota MUST be a required field in the submission payload

##### Scenario: pago parcial chains to compromiso

- GIVEN credit state = `vencido` and user selected "Pago parcial"
- WHEN user completes the comprobante upload
- THEN the system MUST automatically enter the Compromiso de pago flow (CMP-01)
- AND MUST NOT present any intermediate option menu between comprobante and compromiso

---

### Capability: cobranza-multicredit-display (delta)

---

#### Requirement: MCD-01 — Per-Credit Detail Display (multi-crédito)

> **Actualizado 2026-06-10 (Naomi)**: un cliente puede tener hasta 2 créditos en paralelo. El display multi-crédito ya no es solo cuentas bancarias — es detalle completo diferenciado por crédito. Fuente: definiciones-naomi-2026-06-10.md §3.

When a user has multiple active credits (up to 2), the system MUST:
1. Present a **credit selector** so the user can choose which credit to view/operate on.
2. Display the following 7 fields **per credit**, labeled by credit identifier:
   - Valor de cuota
   - Cuenta bancaria
   - CCI
   - Inversionista
   - Plazo
   - Fecha de vencimiento (del contrato)
   - Inicio del préstamo (fecha de 1ª cuota)

(Previously: bank account display was single-credit only — inversionista + cuenta + CCI for one credit. Now expanded to full 7-field per-credit detail with credit selector.)

##### Scenario: multi-credit user — credit selector presented

- GIVEN a verified user with 2 active credits
- WHEN the conversation enters any flow requiring credit-specific data
- THEN bot presents a credit selector listing both credits
- AND user selects one before the flow proceeds

##### Scenario: multi-credit user — per-credit detail display

- GIVEN a verified user with 2 active credits who has selected a credit
- WHEN the system displays credit detail or payment info
- THEN bot displays all 7 fields for the selected credit: valor de cuota, cuenta bancaria, CCI, inversionista, plazo, fecha de vencimiento, inicio del préstamo

##### Scenario: single-credit user — no selector, no change

- GIVEN a verified user with exactly one active credit
- WHEN credit detail or bank account info is requested
- THEN bot displays the single credit's info without presenting a selector (behavior unchanged)

---

## Open Questions

Todas resueltas al 2026-06-10. Fuente: definiciones-naomi-2026-06-10.md.

| # | Question | Resolution | Status |
|---|----------|------------|--------|
| 1 | "Próxima a vencer" window (days) | **5 días** — CONFIRMADO Naomi 2026-06-10 | ✅ Resuelto |
| 2 | WhatsApp proactive templates | OUT OF SCOPE — ChatHub outbound (external system) | ✅ Resuelto |
| 3 | Compromiso registration | **Bot-owned** in `gestiones` table (`commitment_date`, `commitment_amount`, outcome `payment_commitment_registered`, journal `commitment`). NO gestión-registration API. NO `mibotair_results`. ≤2 días → register; >2 días → asesor — **DECISIÓN Ricky 2026-06-11** | ✅ Resuelto |
| 4 | Horario de atención | Lun-Vie 9:00–18:30, refrigerio 13:00–14:00. Feriados: `feriados_peru_2026.json` — CONFIRMADO Naomi 2026-06-10 | ✅ Resuelto |
| 5 | ID-contrato uniqueness + auth | One contract ID = one credit; titular+garante collapse to same credit — CONFIRMADO Naomi 2026-06-10. **DECISIÓN Ricky 2026-06-11**: ID-contrato identification ALSO requires DNI; access granted only if DNI ∈ {titular, garante} — fail-closed otherwise | ✅ Resuelto |
| 6 | Caso real "cuota próxima a vencer" | No disponible aún (vencimientos junio solo hasta 12/06; regla activa a 5 días) — validar con data sintética | ⚠️ Limitación testing |
| 7 | Caso real "cliente al día" | **P04069** — CONFIRMADO Naomi 2026-06-10 | ✅ Resuelto |

---

## Coverage Summary

> Credit state column uses: `al_dia` / `por_vencer` / `vencido` / `All`.
> `n1`/`n2`/`n3` in this table refer to gestión typification in `mibotair_results` (output axis), NOT credit state.

| Xlsx Flow / Row | Requirement | Credit State | Status |
|-----------------|-------------|--------------|--------|
| Consulta deuda (1 intent, branch al_dia) | SCR-02 | al_dia | In scope |
| Consulta deuda (1 intent, branch por_vencer) | SCR-02 | por_vencer | In scope |
| Consulta deuda (1 intent, branch vencido) | SCR-02 | vencido | In scope |
| Subir comprobante (pre-pregunta Sí) | CPR-01 | All | In scope |
| Subir comprobante (pre-pregunta No → asesor) | CPR-01 | All | In scope |
| Realizar pago cuota | INF-11 | vencido | In scope |
| Realizar pago parcial → compromiso | CPR-01 + CMP-01 | vencido | In scope |
| Compromiso de pago (≤2 días) → bot-owned gestiones — row 8 | CMP-01 | vencido | In scope |
| Compromiso confirmación conversacional — row 9 | CMP-02 | vencido | In scope |
| Recordatorio compromiso de pago — row 10 | — | vencido | OUT OF SCOPE — ChatHub outbound |
| Leer compromiso activo desde gestiones | CMP-03 | vencido | OUT OF SCOPE — future change (source is now gestiones, not mibotair_results) |
| Consultar deuda total → Sí asesor | SCR-03 | All | In scope |
| Consultar deuda total → No menú | SCR-03 | All | In scope |
| Consultar cronograma | INF-01 | All | In scope |
| Fecha vencimiento contrato | INF-02 | All | In scope |
| N° cuotas pagadas | INF-03 | All | In scope |
| N° cuotas pendientes | INF-03 | All | In scope |
| Cuentas bancarias (inversionista+cuenta+CCI) | INF-04 | All | In scope |
| Ya pagué → pedir comprobante | INF-05 | All | In scope |
| No puede pagar → motivo → asesor | INF-06 | All | In scope |
| Alternativas / reclamo / asesor | INF-07 | All | In scope |
| Fecha cae domingo/feriado | INF-08 | al_dia / por_vencer | In scope |
| Fuera de horario | INF-09 | All | In scope |
| No comprendida 1er intento | INF-10 | All | In scope |
| No comprendida 2do intento → asesor | INF-10 | All | In scope |
| ID de contrato + DNI como identificación dual | IDC-01 | All | In scope |
| Multi-credit per-credit display | MCD-01 | All | In scope |
| WA Recordatorio preventivo (5d antes) | — | al_dia / por_vencer | OUT OF SCOPE — ChatHub outbound |
| WA Recordatorio preventivo (2d antes) | — | al_dia / por_vencer | OUT OF SCOPE — ChatHub outbound |
| WA Cuota vencida (1d post) | — | vencido | OUT OF SCOPE — ChatHub outbound |
| WA Cuota vencida (3d post) | — | vencido | OUT OF SCOPE — ChatHub outbound |
| WA Confirmación recepción comprobante | — | All | OUT OF SCOPE — ChatHub outbound |
| WA Derivación a asesor | — | All | OUT OF SCOPE — ChatHub outbound |
| WA Compromiso / recordatorio compromiso | — | vencido | OUT OF SCOPE — ChatHub outbound |
