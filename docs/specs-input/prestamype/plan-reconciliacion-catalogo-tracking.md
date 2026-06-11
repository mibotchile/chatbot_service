# Plan — Reconciliación catálogo de tracking ↔ intents reales (DRY, sin romper)

**Fecha:** 2026-06-10
**Contexto:** El catálogo de `conversation-result-tracking` (gestion_catalog.py) keyea en nombres de
intent/tool inventados que NO coinciden con los 16 intents reales de prestamype ni con los tools reales.
Los tests pasan porque validan el catálogo contra sí mismo. Hay que reconciliar sin romper los 648 verdes.

---

## Problema: 3 vocabularios divergentes

| Fuente | Vocabulario | Estado |
|---|---|---|
| `tenants/prestamype/responses.json` (keys) | 16 intents reales (`consulta_deuda`, `comprobante_reportar`, `derivar_asesor`, `identificar`, `elegir_credito`, `donde_pagar`, `politica_pago`, `no_entendido`, `enviar_*`…) | **fuente real** |
| `gestion_catalog.py` (dicts) | nombres inventados (`identificacion`, `upload_comprobante`, `register_payment_commitment`, `multicredito`, `cronograma`…) | no matchea nada |
| `openspec/changes/prestamype-cobranza-flujos` (spec) | intents planificados, OTRA nomenclatura (`consulta_deuda_al_dia`, `compromiso_pago`, `realizar_pago_vencido`…) | aún no implementados |

El catálogo **hardcodea nombres de intent** → drift garantizado, NO DRY.

---

## Principio DRY (target)

Una sola fuente de verdad por concepto:

- **`gestion_catalog.py` = SOLO vocabulario genérico, tenant-agnóstico**: `Outcome`, `EventType`,
  `Capability` (enum), `OutcomeReason` (enum), `SCHEMA_VERSION`. **Sin** nombres de intent de ningún tenant.
- **`responses.json` (por tenant) = binding**: cada intent declara, en su propia metadata, su
  `capability` y su `terminal_signal` (+ `escalation_reason` opcional), usando valores del vocabulario del catálogo.
- **`gestion_derivation` + `_emit_gestion` = leen el binding desde la config del intent resuelto**,
  NO desde dicts hardcodeados.

Resultado: agregar/renombrar un intent lleva su mapping consigo. Cero duplicación. El "per-tenant
mapping" queda resuelto **por construcción** (cada responses.json es per-tenant), validado contra el
vocabulario genérico del catálogo.

---

## "Sin romper" — garantías

1. Los campos nuevos en `responses.json` (`capability`, `terminal_signal`, `escalation_reason`) son
   **OPCIONALES**. Intent sin anotar → `capability=None`, `terminal=None` → outcome `unresolved` —
   **idéntico al comportamiento actual** (donde los dicts inventados tampoco matchean nada).
2. Cambios de código **aditivos** (lectura con default seguro). El hook sigue fire-and-forget / never-raise.
3. Único "break" intencional: los tests del catálogo que asertan `INTENT_TO_CAPABILITY` /
   `TERMINAL_SIGNALS` (mapping inventado) se **reescriben** para asertar el vocabulario + el binding
   vía responses.json. Es corregir un test que validaba algo falso, no romper comportamiento real.
4. Verificación tras cada paso con `GESTION_TEST_PG_DSN` → suite completa verde.

---

## Work items (ordenados, no-break)

| WI | Qué | Archivo | Verify |
|---|---|---|---|
| **1** | Catálogo: **remover** `INTENT_TO_CAPABILITY` / `TERMINAL_SIGNALS` / `INTENT_TO_REASON`. **Agregar** `Capability(str,Enum)` + `OutcomeReason(str,Enum)` derivados de los intents reales. Mantener `Outcome`/`EventType`/`SCHEMA_VERSION`. | `gestion_catalog.py` | `pytest tests/test_gestion_catalog.py` |
| **2** | Anotar los 16 intents de prestamype con `capability` + `terminal_signal` (+ `escalation_reason`). Ver tabla de mapeo abajo. | `tenants/prestamype/responses.json` | JSON válido |
| **3** | Accessor único: `intent_binding(intent_name, responses_cfg) -> (capability, terminal_signal, reason)`. Una sola función, leída por derivation y wiring (DRY). | `gestion_catalog.py` o `gestion_derivation.py` | unit test |
| **4** | `derive_outcome`: consumir el `terminal_signal` del intent (no flags inventados). Ajustar firma para recibir el binding. | `gestion_derivation.py` | `pytest tests/test_gestion_derivation.py` |
| **5** | `_emit_gestion`: resolver `(capability, terminal_signal, reason)` desde la config del intent resuelto (`result.metadata.intent` → tenant responses), append capability event, derivar outcome. | `wiring.py` | `pytest tests/test_gestion_wiring.py` |
| **6** | Reescribir tests para usar **intents reales**: catalog (vocabulario), derivation (signal-driven), wiring (intents reales), integration. | `tests/test_gestion_*.py` | suite completa con DSN |
| **7** | Crear `tenants/_template/responses.json` mínimo documentando los 3 campos de binding como convención para nuevos tenants. | `tenants/_template/` | — |

---

## Mapeo prestamype (16 intents → capability / terminal_signal / reason)

| intent | capability | terminal_signal | escalation_reason |
|---|---|---|---|
| `saludo` | — | — | — |
| `despedida` | — | — | — |
| `identidad_requerida` | identificacion | — | — |
| `identificar` | identificacion | — | — |
| `consulta_deuda` | consulta_deuda | info_provided | — |
| `elegir_credito` | multicredito | — | — |
| `politica_pago` | politica_pago | info_provided | — |
| `donde_pagar` | cuentas_bancarias | info_provided | — |
| `enviar_estado` | estado_cuenta | info_provided | — |
| `enviar_datos_pago` | cuentas_bancarias | info_provided | — |
| `enviar_constancia` | constancia | info_provided | — |
| `comprobante_reportar` | comprobante | — | — |
| `comprobante_resultado` | comprobante | proof | — |
| `derivar_asesor` | — | escalation | explicit_agent_request |
| `no_entendido` | — | fallback | fallback_exhausted |
| `elegir_canal` | — | — | — |

> El `Capability` enum (WI-1) se define a partir de esta columna real:
> `identificacion, consulta_deuda, cuentas_bancarias, estado_cuenta, constancia, politica_pago, comprobante, multicredito`
> (+ los que sumará el change de flujos: `cronograma, cuotas, fecha_vencimiento, compromiso, pago, deuda_total, horario_feriado`).
> `identity_failed` se deriva del gate de identidad (session flag), no de un intent — se mantiene como señal aparte.

---

## Reconciliación futura (change prestamype-cobranza-flujos)

Cuando aterricen los intents nuevos (`compromiso_pago`, `realizar_pago_vencido`, `cronograma`,
`cuentas_bancarias` formal, `domingo_feriado`, `fuera_de_horario`, `no_comprendida`, `id_contrato_*`,
selector multicrédito), **cada uno se anota igual** en `responses.json`. El catálogo no cambia.

⚠️ **Decisión pendiente**: el flujos spec usa nomenclatura distinta a la real (`consulta_deuda_al_dia`
vs `consulta_deuda`). Hay que decidir si flujos **renombra** los intents reales o usa los existentes.
Esto NO bloquea esta reconciliación, pero conviene alinear antes de implementar el flujos change.

---

## Riesgos

- **Acceso a la config del intent desde el hook**: confirmar que `_emit_gestion` puede resolver la
  metadata del intent resuelto (path al responses registry del tenant). Si el hook no la tiene a mano,
  pasar el binding ya resuelto desde el turno (el engine ya resolvió el intent). — verificar en WI-5.
- **`outcome_reason` para `derivar_asesor`**: hoy hay 1 solo motivo; los sub-motivos
  (`cannot_pay`, `commitment_beyond_window`, etc.) llegan con el change de flujos. Default por ahora.

---

## Entrega

Aún no hay PR abierto del change de tracking → **fold de esta reconciliación dentro del change**
(commit "fix(analytics): bind capability catalog to real tenant intents") antes de abrir el PR.
Es la opción DRY y sin deuda. Alternativa: PR aparte `tracking-intent-binding` si se quiere aislar el review.
