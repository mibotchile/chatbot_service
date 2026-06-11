# Proposal: Prestamype Cobranza — Scenario-Based Flows & Conversational Spec (Naomi 2026-06-05)
# Revised: 2026-06-10 — business definitions confirmed by Naomi Ramos (definiciones-naomi-2026-06-10.md)

## Intent

Naomi's spec (engram `cobranza/naomi-spec-flujos-escenarios`, xlsx 25 flujos + 7 WhatsApp templates) requires the bot to STOP answering the same way to everyone and instead ADAPT response + options to the credit situation: Nivel 1 (al día), Nivel 2 (cuota próxima a vencer), Nivel 3 (≥1 cuota vencida). Scenario-adaptive routing is the spine; comprobante refinement, compromiso de pago, and new informational intents all hang off the derived nivel. This is the next functional phase of the prestamype cobranza bot. **Planning-only SDD — implementation runs later in Hive from the tasks.**

## Scope

### In Scope (fronts A–F, framed for spec/design)
- **A. Scenario classifier (N1/N2/N3)** — derive nivel from existing verified profile (`cuotas_vencidas`, `days_overdue`, `next_due_date`). Gate flows/options by nivel. New shared classifier, consumed by router.
- **B. Comprobante** — add `n_cuota` field; pre-question "¿corresponde a tu próxima cuota?" (Sí→flujo / No→asesor); pago parcial → chain to Compromiso de pago. Builds on shipped foto+monto+inversionista+ID-opcional.
- **C. Compromiso de pago (N3)** — ask fecha; if >2 días → asesor; persist commitment; same-day reminder. NEW persistence + reminder.
- **D. Informational intents** — cronograma, fecha venc. contrato, N° cuotas pagadas/pendientes, cuentas bancarias (inversionista+cuenta+CCI), "ya pagué", deuda total, no-puede-pagar, alternativas, domingo/feriado (N1/N2), fuera de horario, no-comprendida 1er/2do intento. Mostly data-driven `responses.json`; cronograma needs Doris installment schedule.
- **E. Multi-credit display** — hasta 2 créditos en paralelo; detalle diferenciado por crédito con 7 campos: valor de cuota, cuenta bancaria, CCI, inversionista, plazo, fecha de vencimiento, inicio del préstamo (1ª cuota). Agregar selector de crédito al flujo cuando hay 2 créditos activos. (Confirmado 2026-06-10; antes solo inversionista+cuenta+CCI.)
- **F. ID de contrato** — alternative identification mechanism.
  ⚠️ **CORRECCIÓN vs. supuesto previo (2026-06-10)**: la info de deuda se muestra ÚNICAMENTE a los involucrados del préstamo (titular y garante), identificados por DNI o ID crédito. NO es acceso abierto sin distinción de rol. Fuente: definiciones-naomi-2026-06-10.md §3.

### Out of Scope (do NOT break what shipped)
- Identity gate, comprobante-liviano core, Doris balance freshness, per-tenant landings — unchanged.
- **G. 7 WhatsApp proactive templates** — outbound campaigns are a DIFFERENT subsystem (segmentation + scheduling) than the inbound chatbot. Defer to a separate `prestamype-outbound-campaigns` change; this proposal only records the copy/segmentation contract for coordination.
- Real-time conversation visualization (minuta 03/06) — separate.

## Capabilities

### New Capabilities
- `cobranza-scenario-routing`: derive N1/N2/N3 from profile; gate flows/options by nivel.
- `cobranza-compromiso-pago`: capture, persist, and remind payment commitments (N3).
- `cobranza-informational-intents`: data-driven cronograma, cuotas pagadas/pendientes, cuentas bancarias, horario/feriado, fallback escalation.
- `cobranza-id-contrato-identification`: identify by contract ID as DNI alternative.

### Modified Capabilities
- `cobranza-comprobante`: add N° cuota + "próxima cuota" pre-question + pago-parcial→compromiso chain.
- `cobranza-multicredit-display`: inversionista+cuenta+CCI per credit.

## Approach
Lean on the existing 2-layer canned router (`apps/agent/features/conversation/responses.py` route_layer1 → _emit_intent) and tenant `responses.json`. Add a thin **scenario classifier** module in `apps/agent/features/cobranza/` that maps the verified profile to a nivel; pass nivel into routing so intents pick nivel-specific variants. New intents are data-driven JSON wherever possible; only cronograma (needs `batch_pagos_v2_bronze` schedule) and compromiso (needs persistence) require code/data work. Keep tenant-agnostic engine, prestamype-specific copy in JSON.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `apps/agent/features/cobranza/scenario.py` | New | N1/N2/N3 classifier from profile |
| `apps/agent/features/cobranza/debt_source.py` / `doris_debt_source.py` | Modified | Expose installment schedule (cronograma) from Doris |
| `apps/agent/features/cobranza/tools.py` | Modified | Comprobante n_cuota, compromiso tool, cuentas-bancarias per credit |
| `apps/agent/features/conversation/responses.py` + `agent.py` | Modified | Nivel-aware variant selection, new intents, 2-strike fallback |
| `tenants/prestamype/responses.json` | Modified | New intents copy, nivel variants, ID-contrato, multi-credit |
| new compromiso persistence store | New | Persist commitments + reminder hook |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Scope too large for one PR | High | Slice for Hive (A→B→D→C→E/F); 400-line guard in sdd-tasks |
| Scenario thresholds ("próxima a vencer" window) unconfirmed | ~~High~~ **RESUELTO** | **5 días — CONFIRMADO por Naomi 2026-06-10.** |
| Cronograma/compromiso need new data/persistence | Med | Confirm Doris schedule fields + persistence store choice in design |
| WhatsApp proactive = separate subsystem | High | Explicitly out of scope; coordinate as separate change |

## Rollback Plan
Each front is additive and behind the nivel/intent layer. Revert per-PR (git revert). Classifier defaults to current behavior if nivel undetermined; new intents are JSON-only and removable; compromiso store is isolated.

## Dependencies
- Doris `batch_pagos_v2_bronze` installment schedule fields for cronograma.
- Business confirmation: ~~"próxima a vencer" window~~ ✓ (5 días), ~~horario de atención~~ ✓ (Lun-Vie 9:00-18:30, refrigerio 13:00-14:00, feriados en feriados_peru_2026.json), ~~compromiso store~~ ✓ (gestión-reg API, ≤2d registrar / >2d asesor), ~~ID-contrato uniqueness~~ ✓ (titular+garante → mismo crédito).
- Doris `batch_pagos_v2_bronze` installment schedule fields para cronograma (sigue pendiente de implementación).

## Success Criteria
- [ ] Bot returns credit-state-specific response + options for los casos reales: P04069 (`al_dia` — confirmado por Naomi 2026-06-10), P03638, P03700, P03871, P03886. Nota: caso real "cuota próxima a vencer" NO disponible aún — validar con data sintética (vencimientos junio solo hasta 12/06; regla activa a 5 días).

- [ ] Comprobante captures N° cuota and pre-question routes Sí→flujo / No→asesor.
- [ ] Compromiso de pago persists and schedules a same-day reminder; >2 días → asesor.
- [ ] New informational intents answer from verified data with zero invented numbers.
- [ ] Shipped behaviors (identity gate, comprobante-liviano, balance freshness, landing) unchanged.
