# Spec — Tenant PrestamYpe (consulta de deuda + validación de comprobantes)

> Tenant REAL (no marca blanca). Fintech P2P de préstamos con garantía hipotecaria, Perú. Reusa el engine `chatbot-cobranza` (FastAPI + Ada + gate de identidad por DNI). Datos REALES desde Doris (`project_QUIdI0iwQY0l3pJwRKLB`), no mock. Fecha: 2026-05-27.

## 1. Alcance (acotado — solo dos capacidades)
1. **Consulta de deuda** por DNI.
2. **Carga + validación de comprobantes de pago** (la innovación): clasificar en el formulario **tipo** (pago / abono / cancelación) y validar **a qué cuenta apunta** (CCI del inversionista).

NO incluye (a diferencia de PrestaUnion): negociación, planes de pago, certificado de no adeudo, reclamos. Tampoco conciliación bancaria automática (el comprobante es indicio; un humano concilia después contra el banco).

## 2. Branding
Marca real PrestamYpe: verde (`~#16b866` / `#0bbf73`, tomar el exacto del sitio), logo Prestamype, tono peruano ("tú", cero voseo). Footer "Powered by Onbotgo". NO es marca blanca: se muestra la identidad de PrestamYpe.

## 3. Modelo de datos (verificado en Doris)
Acceso: `ssh doris-fe` → `mysql 127.0.0.1:9030` (root). DB `project_QUIdI0iwQY0l3pJwRKLB`, capa bronze.

- **Deuda** → `batch_asignacion_review_bronze` (513 créditos, 905 DNI). Por crédito: `capital`, `monto_total`, `dias_mora`, `fecha_vencimiento`, `estado` (VIGENTE), `moneda`, banco, **`codigo_de_cuenta_cci`** (referencia de cuenta, 100% limpio), `id_inversionista`.
- **Cuotas/pagos** → `batch_pagos_v2_bronze` (33 831). Join por **`codigo_contrato` = `id_credito`** (513 match). Campos: `codigo_operacion`, `cuota_esperada_actualizada`, `saldo_por_cancelar`, `monto_total_pagado_al_credito`.
- **Cardinalidad**: 1 DNI → 1 crédito (multicrédito marginal = 2 casos). Créditos grupales: 1 crédito → N DNI (codeudores).
- **⚠️ Calidad**: `numero_de_cuenta` ~10% corrupto (notación científica E+12) → **usar SIEMPRE el CCI**, nunca `numero_de_cuenta`.

## 4. Flujo 1 — Consulta de deuda
1. Gate de identidad por **DNI** (reusa el flujo DNI-first del engine).
2. DNI → `batch_asignacion` → crédito(s) del cliente. Mostrar: saldo (capital/monto_total), días de mora, próximo vencimiento, estado.
3. Join a `batch_pagos_v2` por `id_credito` → cuota esperada, saldo por cancelar.
4. DNI en crédito grupal → mostrar el crédito igual (es compartido). Multicrédito (2 casos) → listar ambos.

## 5. Flujo 2 — Carga y validación de comprobante (no-cost)
**El usuario sube la imagen + ingresa/confirma 3 datos en el formulario**: CCI destino, monto, nº de operación (fecha opcional). NO se requiere OCR pago ni GPU. Validación contra Doris:

| Validación | Regla |
|---|---|
| **A qué cuenta apunta** | CCI ingresado ∈ CCI(s) del/los crédito(s) del DNI → ✓ cuenta válida + **identifica el crédito** (CCI único por crédito desambigua). Si no matchea → ✗ "esa cuenta no corresponde a tu crédito". |
| **Tipo de operación** | monto vs `cuota_esperada_actualizada` / `saldo_por_cancelar` del crédito: **pago** ≈ cuota · **abono** < cuota (parcial) · **cancelación** ≈ saldo total. |
| **Dedup** | `nº operación` no registrado antes → evita doble carga del mismo voucher. |

**Resultado al deudor**: "Recibimos tu comprobante. Lo clasificamos como **[PAGO/ABONO/CANCELACIÓN]** sobre tu crédito {id}, cuenta CCI ...{4}. Queda en revisión." La imagen + datos + clasificación se registran para conciliación humana.

**OCR opcional (mejora futura, no-cost)**: Tesseract en CPU para auto-rellenar los 3 campos. No bloquea el MVP.

## 6. Arquitectura
- Reusa el engine `chatbot-cobranza` (Ada, gate DNI, multi-proveedor LLM). Nuevo tenant `prestamype`.
- **Fuente de deuda por tenant**: el tenant `prestaunion` usa mock JSON; `prestamype` usa **Doris** (nuevo `doris_debt_source` read-only via mysql connector → `doris-fe:9030`). DECISIÓN: query directa read-only para la demo; producción → vista/API intermedia.
- **Tools nuevas**: `consultar_deuda_doris(dni)` y `validar_comprobante(dni, cci, monto, nro_operacion)` → {cuenta_valida, credito, tipo, dedup}.
- **Recepción de imagen**: upload web (endpoint nuevo) / media WhatsApp (Evolution download).
- **Storage + audit**: imagen + datos + clasificación + estado (en revisión / conciliado). PII financiero → audit trail.

## 7. Fases de implementación
- **F0 — Scaffold tenant**: `tenants/prestamype/` (branding verde, config, knowledge), conexión Doris read-only + `doris_debt_source`.
- **F1 — Consulta de deuda**: `consultar_deuda_doris(dni)` (asignacion + join pagos) tras el gate DNI. Verificar contra datos reales.
- **F2 — Carga + validación**: formulario de carga (web upload + WhatsApp media) + `validar_comprobante` (CCI→crédito, monto→tipo, dedup) + mensaje de resultado.
- **F3 — Storage/audit** + (opcional) OCR Tesseract para auto-rellenar.

## 8. Decisiones / gaps
1. Acceso Doris: **query directa read-only** (demo) — confirmar si producción usa vista/API.
2. Conciliación real: fuera de alcance del MVP (humano concilia el comprobante contra el banco).
3. Storage de comprobantes: ¿dónde? (MinIO, FS local, etc.).
4. Branding verde exacto: tomar del sitio prestamype.com.
