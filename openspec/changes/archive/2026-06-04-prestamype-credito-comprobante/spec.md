# Spec: prestamype-credito-comprobante

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
