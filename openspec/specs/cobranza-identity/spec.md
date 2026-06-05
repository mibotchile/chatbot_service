# cobranza-identity Specification

## Purpose

Governs the identity gate that controls whether a cobranza chatbot session can
proceed with debt exposure. This spec covers DNI/RUC format validation, data
source selection per tenant, fixture fallback policy, and the hard blockers
required before activating a production data source.

---

## Requirements

### Requirement: DNI/RUC Format Validation

The system MUST normalize (strip all non-digit characters) and then validate
the length of any DNI or RUC supplied by the user BEFORE querying any debt
source. A DNI MUST be exactly 8 digits after normalization. A RUC MUST be
exactly 11 digits after normalization. Any value that does not satisfy either
length constraint MUST be rejected immediately with `identified: False` and
MUST NOT trigger a query to any debt source.

#### Scenario: Valid 8-digit DNI is accepted for lookup

- GIVEN the user provides the string "12345678"
- WHEN `_identificar_cliente` normalizes and validates the input
- THEN the normalized value is "12345678", validation passes
- AND the debt source is queried with "12345678"

#### Scenario: Valid 11-digit RUC is accepted for lookup

- GIVEN the user provides the string "12345678901"
- WHEN `_identificar_cliente` normalizes and validates the input
- THEN the normalized value is "12345678901", validation passes
- AND the debt source is queried with "12345678901"

#### Scenario: DNI with formatting characters is normalized then accepted

- GIVEN the user provides "12.345.678" (dots present)
- WHEN `_identificar_cliente` normalizes (strips non-digits) and validates
- THEN the normalized value is "12345678" (8 digits), validation passes
- AND the debt source is queried with "12345678"

#### Scenario: Too-short input is rejected before source query

- GIVEN the user provides "1234" (4 digits after normalization)
- WHEN `_identificar_cliente` normalizes and validates the input
- THEN validation fails
- AND the tool returns `identified: False` with a format-error reason
- AND NO debt source query is made

#### Scenario: Too-long input (not 8 or 11 digits) is rejected

- GIVEN the user provides "123456789" (9 digits after normalization)
- WHEN `_identificar_cliente` normalizes and validates the input
- THEN validation fails (neither 8 nor 11 digits)
- AND the tool returns `identified: False`
- AND NO debt source query is made

#### Scenario: Non-numeric input after normalization is rejected

- GIVEN the user provides "ABCDEFGH"
- WHEN `_identificar_cliente` normalizes (strips non-digits → empty string) and validates
- THEN validation fails
- AND the tool returns `identified: False`
- AND NO debt source query is made

---

### Requirement: Doris Fall-Through Fix

When the Doris debt source is queried and Doris responds successfully (HTTP
200 / no exception) but returns zero rows, the system MUST return an empty
list `[]` — indicating the debtor was not found. The fixture fallback MUST NOT
be invoked in this case. The fixture fallback MUST be invoked ONLY when Doris
raises an Exception (connection failure, timeout, driver error, or any
unhandled exception).

#### Scenario: Doris reachable, DNI not found → empty result, no fixture

- GIVEN Doris is reachable and `_resolve_dni_credits` is called with a valid DNI
- WHEN Doris responds with zero rows for that DNI
- THEN the function returns `[]`
- AND the fixture fallback is NOT consulted

#### Scenario: Doris reachable, DNI found → real credits returned

- GIVEN Doris is reachable and `_resolve_dni_credits` is called with a valid DNI
- WHEN Doris responds with one or more rows
- THEN the function returns those rows as credit objects
- AND the fixture fallback is NOT consulted

#### Scenario: Doris raises Exception → fixture consulted only if allowed

- GIVEN Doris raises an Exception (e.g. connection refused)
- WHEN `_resolve_dni_credits` catches the exception
- THEN the function defers to the tenant's `allow_fixture_fallback` policy
- AND if `allow_fixture_fallback` is True the fixture is consulted
- AND if `allow_fixture_fallback` is False the exception is propagated or `[]` is returned (see Fixture Fallback Policy requirement)

---

### Requirement: Per-Tenant Fixture Fallback Policy

Each tenant's configuration MUST declare an `allow_fixture_fallback` boolean
flag in `tenant.config.json`. When this flag is `false` and the debt source
raises an Exception, the system MUST NOT identify the user against fixture
data; instead it MUST respond with a safe degradation message. When this flag
is `true` and the debt source raises an Exception, the system MAY fall back to
fixture data (demo/testing behavior).

The default safe degradation message when fixture fallback is disabled MUST be:
"No puedo verificar tu identidad en este momento; intenta más tarde o te
derivo con un asesor."

#### Scenario: Prod tenant, Doris down → safe degradation, no fixture

- GIVEN a tenant with `allow_fixture_fallback: false` (e.g. prestamype)
- AND Doris raises an Exception
- WHEN the identity gate handles the failure
- THEN the user is NOT identified
- AND the bot responds with the safe degradation message
- AND NO fixture data is exposed

#### Scenario: Demo tenant, source down → fixture fallback allowed

- GIVEN a tenant with `allow_fixture_fallback: true` (e.g. prestaunion)
- AND the debt source raises an Exception
- WHEN the identity gate handles the failure
- THEN the fixture fallback is consulted
- AND the user MAY be identified against demo fixture data

#### Scenario: prestaunion demo behavior is unchanged

- GIVEN the prestaunion tenant (data_source: mock, allow_fixture_fallback: true)
- WHEN any identity operation is performed
- THEN behavior is identical to pre-change (mock source + fixture fallback)
- AND all 424 baseline tests remain green

---

### Requirement: Tenant Data Source Activation (prestamype → Doris)

The prestamype tenant's `data_source` field MUST be set to `"doris"` in
`tenants/prestamype/tenant.config.json`. This change MUST NOT be deployed to
production unless both hard blockers (pymysql image validation AND Doris data
confirmation) have been cleared.

#### Scenario: prestamype routes identity to Doris after activation

- GIVEN prestamype `data_source` is "doris" and blockers have been cleared
- WHEN a user provides a valid DNI
- THEN `_identificar_cliente` queries the Doris source (not the mock)
- AND a DNI present in `batch_asignacion_review_bronze` is identified
- AND a DNI absent from Doris returns `identified: False` (no fixture used)

#### Scenario: Non-existent DNI is rejected in prod (regression guard)

- GIVEN prestamype is live on Doris
- WHEN a user provides a DNI that does not exist in `batch_asignacion_review_bronze`
- THEN the bot returns `identified: False`
- AND no debt information is revealed

---

### Requirement: pymysql Image Availability (Hard Blocker)

The prestamype container image MUST have `pymysql` installed and importable
before the `data_source` flip is applied. A `ModuleNotFoundError` for `pymysql`
MUST block deployment of the Doris activation. The blocker is cleared when a
test connection to Doris succeeds inside the rebuilt image.

#### Scenario: Image build includes pymysql and driver imports cleanly

- GIVEN the Dockerfile is updated to include pymysql
- WHEN the image is rebuilt and `python -c "import pymysql"` is run
- THEN the import succeeds with exit code 0

#### Scenario: Doris connection test passes inside rebuilt image

- GIVEN the rebuilt image with pymysql
- WHEN a test connection to Doris using the configured credentials is executed
- THEN the connection succeeds and a query against `batch_asignacion_review_bronze` returns results

---

### Requirement: Doris Data Confirmation (Hard Blocker)

The Doris table `batch_asignacion_review_bronze` MUST be confirmed to contain
prestamype's real debtors BEFORE the `data_source` is flipped to "doris".
Confirmation requires a verification query executed by Ricky (or an authorized
operator) that shows a non-zero row count for prestamype's portfolio. Without
this confirmation the `data_source` change MUST NOT be deployed.

#### Scenario: Verification query confirms data present

- GIVEN pymysql is importable and Doris credentials are configured
- WHEN a count query is run against `batch_asignacion_review_bronze` filtered to prestamype's portfolio
- THEN the result is a non-zero row count
- AND Ricky (or authorized operator) explicitly confirms the count is plausible
- AND this confirmation is recorded before `data_source` is flipped

#### Scenario: Zero rows blocks activation

- GIVEN the verification query returns 0 rows
- THEN the `data_source` flip MUST NOT proceed
- AND the blocker remains open until data is confirmed populated

---

### Requirement: Rollback Kill-Switch

The system MUST support instant rollback of the Doris activation by reverting
`data_source` to `"mock"` and `allow_fixture_fallback` to `true` in
`tenants/prestamype/tenant.config.json` without a code change or image rebuild.
Config revert alone MUST be sufficient to restore the pre-change identity
behavior for prestamype.

#### Scenario: Config revert restores mock behavior

- GIVEN prestamype is running on Doris and an incident occurs
- WHEN an operator reverts `data_source` to "mock" and `allow_fixture_fallback` to true in tenant config
- THEN prestamype identity queries route to the mock source
- AND fixture fallback is re-enabled
- AND no code deploy or image rebuild is required

---

## Acceptance Blockers

The following conditions MUST be true before the Doris activation requirement
(Tenant Data Source Activation) may be applied in production. They are
preconditions, not post-conditions.

| # | Blocker | Cleared by |
|---|---------|-----------|
| B1 | `pymysql` installs and imports inside rebuilt image | Image build + `import pymysql` test passing |
| B2 | Test connection to Doris succeeds inside rebuilt image | Live connection query returning results |
| B3 | `batch_asignacion_review_bronze` contains prestamype real debtors | Ricky/operator verification query with non-zero count |

Code-track requirements (DNI validation, fall-through fix, per-tenant flag)
are NOT gated on B1–B3 and MAY land independently.

---

---

## Capability: cobranza-balance

### Requirement: First-Unpaid-Installment Balance

The system MUST derive the outstanding balance (`saldo_por_cancelar`) from the
first unpaid installment of `batch_pagos_v2_bronze`, defined as the row with the
oldest `fecha_de_pago_esperada_original` WHERE `fecha_de_pago_del_cliente IS NULL`.
The system MUST NOT use `MAX(saldo_por_cancelar)`, which equals the original loan
principal and is the live bug.

#### Scenario: Correct balance for in-progress credit

- GIVEN credit P04197 has installments in `batch_pagos_v2_bronze`
- WHEN `consultar_deuda` is called for the associated DNI
- THEN the returned balance is 81,510.15 (first-unpaid saldo)
- AND the balance is NOT 108,450 (original loan / MAX value)

#### Scenario: Credit fully paid (no unpaid installments)

- GIVEN all installments for a credit have `fecha_de_pago_del_cliente IS NOT NULL`
- WHEN `consultar_deuda` is called
- THEN the returned balance is 0 or the system reports credit as fully settled

### Requirement: Next Installment From First-Unpaid Row

The system MUST populate "próxima cuota" using `cuota_esperada_mensual` of the
first unpaid installment. The system MUST NOT use `cuota_esperada_actualizada`
when it is NULL (Bug 3 — NULL in live data for P04197).

#### Scenario: cuota_esperada_actualizada is NULL

- GIVEN the first-unpaid installment has `cuota_esperada_actualizada = NULL`
- WHEN the debt profile is built
- THEN `proxima_cuota` equals `cuota_esperada_mensual` of that installment
- AND the response does NOT show NULL or an empty cuota field

### Requirement: Latest Assignment Batch Only

The system MUST filter `batch_asignacion_review_bronze` to the single latest batch
per `id_credito` (by `creado_el` DESC or `archivo` lexicographic DESC). All
non-aggregated columns (e.g. `dias_mora`, `inversionista`) MUST come exclusively
from that latest batch row; earlier batches MUST be excluded.

#### Scenario: Multiple batches exist for one credit

- GIVEN `batch_asignacion_review_bronze` contains 3 batches for credit P04197
  (Asignacion_19052026, _20052026, _20052026_F)
- WHEN `consultar_deuda` is called
- THEN `dias_mora` reflects only the latest batch (_20052026_F)
- AND the result is deterministic across repeated calls

---

## Capability: cobranza-credit-display

### Requirement: Debt Card Shows Inversionista and Bank Account

The system MUST include `inversionista` and `numero_de_cuenta` (bank account
number) in the debt card response and "datos de pago" templates. The system MUST
also include `codigo_de_cuenta_cci` alongside the bank account number. The system
MUST NOT expose `capital` (original principal) in any user-facing response.

#### Scenario: User requests credit status

- GIVEN the debtor's credit has `inversionista` and `numero_de_cuenta` in Doris
- WHEN the bot renders "tu crédito" or "datos de pago"
- THEN the response includes: corrected balance, inversionista, numero_de_cuenta,
  and codigo_de_cuenta_cci
- AND `capital` / `saldo_capital` is absent from the response

#### Scenario: Missing optional field

- GIVEN `numero_de_cuenta` is NULL for a credit
- WHEN the debt card is rendered
- THEN the bank account row is omitted gracefully (no NULL shown)

---

## Capability: cobranza-comprobante

### Requirement: Lighter Voucher Field Collection

The bot MUST request only: foto + monto pagado + inversionista (who they paid).
`id_credito` is OPTIONAL and the bot MUST NOT block on it. The bot MUST NOT
require the user to provide CCI or nro_operacion — those are resolved server-side
from the known credit profile. Collection is flexible: if the user resists
providing a field, the bot MUST proceed with what has been provided.

#### Scenario: User provides all lighter fields

- GIVEN the bot has resolved the debtor's credit (DNI → CCI + inversionista)
- WHEN the user submits foto + monto + inversionista
- THEN the comprobante is accepted for server-side validation
- AND the bot does NOT ask for CCI or nro_operacion

#### Scenario: User omits id_credito

- GIVEN the bot prompts for the lighter set
- WHEN the user provides foto + monto + inversionista but no id_credito
- THEN the flow proceeds without blocking on id_credito
- AND id_credito is resolved from the server-side credit profile

#### Scenario: User resists providing a field

- GIVEN the bot has requested monto or inversionista
- WHEN the user declines or ignores the prompt
- THEN the bot proceeds with what is available, recording the partial set

### Requirement: Server-Side Comprobante Validation

`validate_comprobante` MUST validate the submitted comprobante against the
credit's known CCI and inversionista from Doris (not from user input). It MUST
classify the payment as pago parcial (abono), pago total (cancelación), or pago
cuota based on `monto`. Anti-duplicate logic MUST remain active. The comprobante
MUST be left in status "EN REVISIÓN" for human conciliation. The audit record
MUST capture `inversionista`.

#### Scenario: Monto matches installment amount (abono)

- GIVEN a credit with first-unpaid cuota = 7,031.91
- WHEN a comprobante is submitted with monto = 7,031.91
- THEN classification = "pago cuota"
- AND status = "EN REVISIÓN"
- AND audit record includes inversionista from the server-side credit profile

#### Scenario: Monto equals outstanding balance (cancelación)

- GIVEN outstanding balance = 81,510.15
- WHEN comprobante is submitted with monto = 81,510.15
- THEN classification = "cancelación"
- AND status = "EN REVISIÓN"

#### Scenario: Duplicate comprobante submission

- GIVEN a comprobante with the same monto + credit has already been submitted
  within the anti-duplicate window
- WHEN a second identical submission arrives
- THEN the system rejects it with a duplicate-detected response
- AND no second audit record is created

#### Scenario: Inversionista mismatch

- GIVEN the credit's known inversionista is "Fondo A"
- WHEN a comprobante is submitted claiming inversionista = "Fondo B"
- THEN the system flags a validation warning
- AND the comprobante is still stored EN REVISIÓN (human conciliates)

---

## Out of Scope

- Second identity factor (DNI is single-factor today; separate future change).
- Any behavior change for the prestaunion demo tenant.
- BigQuery, dashboard, or downstream reporting changes.
