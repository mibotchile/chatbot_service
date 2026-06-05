# Prestamype: Comprobante Upload + "Tu Crédito" Flow Investigation
**Date:** 2026-06-04  
**Branch:** feat/prestamype-landing-redesign  
**Scope:** Read-only. No code was modified.

---

## A. Comprobante Upload Flow — Current State

### POST /api/v1/comprobante (apps/agent/api/routers/cobranza.py)

Current form fields accepted:

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `tenant_id` | str | yes | |
| `dni` | str | yes | Identity gate — must resolve to a borrower |
| `monto` | float | yes | Used for tipo classification (pago/abono/cancelación) |
| `nro_operacion` | str | yes | Dedup key |
| `file` | UploadFile | yes | JPG/PNG/PDF, 8 MB max, magic-byte validated |
| `account_type` | str | no | "cci" (default) or "cuenta" |
| `cuenta_destino` | str | no | Destination account digits |
| `cci` | str | no | Legacy alias for `cuenta_destino` |

**No `nombre_inversionista` field. No `id_credito` field.**

### Storage
- File: `COBRANZA_COMPROBANTE_DIR/<dni>/<nro_operacion>.<ext>`
- Audit JSON record (per comprobante): `{credito, dni, account_type, cuenta_destino, cci, monto, nro_operacion, tipo, estado, created_at}`
- No `nombre_inversionista` in the audit record.

### Bot conversation — what it asks the borrower
The `consultar_deuda` / `validar_comprobante` flow is **deterministic form-based**, not LLM-free-form.

The agent prompt (`MUST` guardrail) says:
> "Para validar un comprobante, pedir al usuario los 3 datos del voucher (CCI, monto, número de operación) ANTES de llamar la tool"

The widget form (`comprobanteFormHtml()` in widget.js) collects:
1. Imagen/PDF (`file`)
2. Tipo de cuenta: CCI / Número de cuenta (radio)
3. Número de cuenta destino (`cuenta_destino`)
4. Monto (`monto`)
5. Número de operación (`nro_operacion`)
6. Fecha de pago (optional date input, not sent to API — display only)

**No OCR.** `registrar_comprobante_foto` exists for WhatsApp photo path but explicitly documents "NO hay OCR — solo constancia de imagen recibida, conciliación MANUAL posterior."

### Gap analysis: wanted fields vs current

| Field | Wanted | Currently captured | Gap |
|-------|--------|--------------------|-----|
| Foto comprobante | yes | YES (file upload) | None |
| Monto pagado | yes | YES (form field + stored) | None |
| Nombre inversionista | yes | NO | Missing — not asked, not stored |
| ID crédito | yes (optional) | PARTIAL — `credito` (= `account_id`) is resolved server-side from DNI, stored in audit record, but NOT shown/asked to user | Not exposed to user, but implicitly captured |

**Summary gaps:**
- `nombre_inversionista` — not captured anywhere (form, API, audit JSON).
- `id_credito` — implicitly captured server-side via identity resolution (stored as `credito` in audit JSON), but not shown to borrower nor explicitly confirmed.

---

## B. "Tu Crédito" Loan-Info Presentation — Current State

### consultar_deuda tool (features/cobranza/tools.py)

Fields returned in the `summary` dict (what the LLM can narrate):

```
account_id, loan_number, business_name, currency, currency_symbol,
balance, balance_formatted, next_due_date, next_installment_amount,
next_installment_formatted, status, status_label, days_overdue,
installments_pending, installment_history,
has_debt,
banco,       ← YES, present
cci,         ← YES, present (FULL, not masked)
cci_masked,  ← YES, present (masked ···XXXX form)
```

**`inversionista` is NOT in the `consultar_deuda` summary dict.** The field exists on the profile (it's fetched from Doris) but `consultar_deuda` never includes `profile.get("inversionista")` in what it returns.

**`principal_original` / `saldo_capital`** — `principal_original` is mapped in the Doris schema (→ `capital` column) but is NOT included in the `consultar_deuda` return dict either. The tool does NOT expose `saldo_capital` or `principal_original`.

### Side-panel debt card (`_debtCardHtml` in widget.js)

Rendered rows (in order):
1. `Próxima cuota` → `next_installment_formatted`
2. `Vence` → `next_due_date`
3. `Cuenta para realizar el pago` → `cci` (full) or `cci_masked`, appended with `· {banco}`
4. Header: `Saldo pendiente` → `balance_formatted` (large display)
5. Badge: `status_label` (Al día / En mora)
6. Bank name shown as subtitle: `banco`

**Fields NOT rendered in the debt card:**
- `inversionista` — NOT rendered
- `saldo_capital` / `principal_original` — NOT rendered (confirmed: not in `_debtCardHtml`)
- Separate `cuenta_bancaria` (numero de cuenta) — NOT rendered; only CCI is shown

### responses.json — consulta_deuda template

```
"Tu crédito {loan} tiene un saldo de {saldo}. La próxima cuota es de {cuota} y vence el {fecha_venc}. Estado: {estado}."
```

Variables: `{loan}`, `{saldo}`, `{cuota}`, `{fecha_venc}`, `{estado}` — no `{inversionista}`, no `{cci}` (CCI only in `donde_pagar` template).

### donde_pagar template

```
"Puedes pagar tu crédito {loan} a la cuenta CCI {cci} del banco {banco}. ..."
```

Only CCI — no separate `numero_de_cuenta` / `cuenta_bancaria`.

### Summary for B

| Field | Required | Currently shown |
|-------|----------|-----------------|
| `saldo_capital` (principal_original) | OMIT (requirement) | NOT shown (already absent) |
| `cci` | SHOW | YES — shown in debt card + donde_pagar template |
| `cuenta_bancaria` (numero_de_cuenta) | ADD | NOT shown — field exists in Doris (`numero_de_cuenta`) but not in column_map or anywhere in the display |
| `nombre_inversionista` | SHOW | NOT shown — fetched from Doris, in profile, but NOT included in consultar_deuda return dict and NOT in debt card or any template |

---

## C. Doris Data Source — Live Column Verification

### Connection details
- Host: `10.110.0.15:9030` (MySQL wire), user `cobranza_ro`, DB `project_QUIdI0iwQY0l3pJwRKLB`

### batch_asignacion_review_bronze — relevant columns (LIVE verified)

| Column | Type | Notes |
|--------|------|-------|
| `id_credito` | varchar(50) | → `account_id`, `loan_number` ✓ already queried |
| `dni_ruc` | varchar(50) | → `dni` ✓ |
| `nombre_completo` | varchar(100) | → `borrower_name` ✓ |
| `inversionista` | varchar(100) | → `inversionista` ✓ **EXISTS, already in column_map, already SELECTed** |
| `id_inversionista` | varchar(50) | Investor ID — NOT in column_map |
| `codigo_de_cuenta_cci` | varchar(50) | → `cci` ✓ **EXISTS, already queried** |
| `numero_de_cuenta` | varchar(50) | **EXISTS in table — NOT in column_map, NOT queried** |
| `banco` | varchar(50) | → `banco` ✓ already queried |
| `capital` | double | → `principal_original` ✓ in column_map but NOT in consultar_deuda return |
| `moneda` | varchar(50) | → `currency` ✓ |
| `dias_mora` | varchar(20) | → `days_overdue` ✓ |
| `fecha_vencimiento` | datev2 | → `next_due_date` ✓ |

**No `saldo_capital` column exists.** The concept is `capital` (principal at origination). There is no separate "saldo capital" (outstanding principal) column — the running balance is in `batch_pagos_v2_bronze.saldo_por_cancelar`.

### batch_pagos_v2_bronze — relevant columns (LIVE verified)

| Column | Type | Notes |
|--------|------|-------|
| `saldo_por_cancelar` | double | → `balance`, `saldo_por_cancelar` ✓ already queried |
| `cuota_esperada_actualizada` | double | → `cuota_esperada`, `next_installment_amount` ✓ |
| `inversionista` | varchar(150) | Also present here — NOT in column_map (redundant; debt table is canonical) |
| `saldo_por_cancelar_esperado_actualizada` | double | Available if needed |
| `capital_fraccionado_actualizado` | double | Per-installment capital split |
| `codigo_contrato` | varchar(50) | Join key ✓ |

### Column status summary

| Needed field | Doris column | Currently in column_map | Currently in consultar_deuda output | Action needed |
|---|---|---|---|---|
| `inversionista` (name) | `inversionista` (debt table) | YES | NO (not in return dict) | Add to `consultar_deuda` return + debt card + template |
| `cci` | `codigo_de_cuenta_cci` | YES | YES (in return dict) | None for CCI itself |
| `cuenta_bancaria` (num. cuenta) | `numero_de_cuenta` (debt table) | NO | NO | Add to `column_map` + `consultar_deuda` return + debt card |
| `saldo_capital` | Does NOT exist as own column | N/A | N/A | Not applicable — no such column. `capital` = monto original del préstamo; running balance = `saldo_por_cancelar` |

---

## Open Product Questions

1. **Comprobante: nombre inversionista — free text or pre-filled?**  
   The borrower doesn't know the investor's full registered name. Should the form PRE-FILL it from the profile (shown read-only, confirming "you're paying investor X") or ask the borrower to type it? Pre-filling from profile is cleaner and eliminates typos.

2. **Comprobante: is `nombre_inversionista` for reconciliation or for UX?**  
   If it's purely for human reconcilers, storing `profile.get("inversionista")` automatically when the comprobante is registered (server-side) is enough — no need for a user-facing field.

3. **Comprobante: ID crédito — shown to borrower or just captured?**  
   It's already captured server-side. Should it be displayed to the borrower on the form ("You are paying toward credit P02137") for UX confirmation?

4. **cuenta_bancaria vs CCI — when does each apply?**  
   `numero_de_cuenta` exists in Doris. Is it a different account from `codigo_de_cuenta_cci`? Are some investors same-bank (número de cuenta) and others inter-bank (CCI only)? Or is `numero_de_cuenta` the CCI "short form" for same-bank? This determines whether to show both, or show one and derive the other.

5. **"Tu crédito" card — inversionista display format?**  
   Show as "Inversionista: [NOMBRE]" row in the debt card, or in the donde_pagar template ("Paga a la cuenta CCI {cci} del banco {banco} — inversionista: {inversionista}")? The P2P context makes the second feel more natural.

6. **`saldo_capital` clarification — is `capital` (monto_original) what was meant?**  
   There is no live `saldo_capital` column. The closest thing is `capital` (original loan amount = `principal_original`). Confirm: is the requirement to HIDE `principal_original` (capital original), or to hide a "capital pendiente" breakdown? Current behavior already omits it.

---

## Files Affected / Key Paths

| File | Role |
|------|------|
| `apps/agent/api/routers/cobranza.py` | POST /api/v1/comprobante — accepts form fields |
| `apps/agent/features/cobranza/tools.py` | `consultar_deuda()` return dict + `validar_comprobante()` + audit JSON |
| `apps/agent/features/comprobantes/validator.py` | validate_comprobante + audit record storage |
| `apps/agent/features/cobranza/doris_debt_source.py` | SQL builder from schema + profile mapping |
| `tenants/prestamype/tenant.config.json` | `doris_schema.column_map` — add `cuenta_bancaria` here |
| `tenants/prestamype/responses.json` | Chat templates — add `{inversionista}` to consulta_deuda/donde_pagar |
| `frontend/widget.js` | `_debtCardHtml()` — add inversionista row + cuenta_bancaria row |

---

## Engram
- **topic_key:** `cobranza/comprobante-credito-explore`
- **project:** chatbot-cobranza
