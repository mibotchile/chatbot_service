# Refactor a Screaming Architecture — chatbot-cobranza

> Propuesta de estructura target. Fecha: 2026-06-02. Estado: **DRAFT — pendiente validación de Ricky**.

## Principio rector

La estructura debe **gritar el dominio** (cobranza, comprobantes, leads), no el framework.
Features de negocio arriba; lo técnico-compartido en un `shared/` (kernel).

## Estructura actual (layered — el "antes")

```
apps/agent/
  api/          main.py (1905!), dashboard.py, chathub.py
  config/       settings, soul, pricing, tools_schema, cors
  core/         (26 archivos — cajón de sastre)
                agent, responses, response_builder, response_guard,
                conversation_fsm, hooks, llm/
                whatsapp_service, whatsapp_formatter
                lead_machine, prospect_profile, opportunity_detector, visitor_memory
                persistence, db, state, redis_store
                rate_limit, email_service
                webhooks, webhook_config, tenant_loader
  integrations/ debt_source, doris_debt_source, mock_debt_source,
                chathub_adapter, chathub_outbound, chathub_web_publisher,
                certificate_pdf, analytics_sink
  tools/        cobranza.py (870!)
  prompts/      system.py
  skills/
tenants/        prestamype, prestaunion, _template (data, no código)
```

## Estructura target (screaming — el "después")

```
apps/agent/
  features/
    conversation/          # el corazón: el motor de diálogo (lo que el bot ES)
      agent.py
      responses.py         # (a partir, 764 líneas)
      response_builder.py
      response_guard.py
      conversation_fsm.py
      hooks.py
      prompts.py           # ← prompts/system.py

    cobranza/              # dominio de deuda + negociación
      tools.py             # ← tools/cobranza.py (a partir, 870 líneas)
      debt_source.py       # puerto (interfaz)
      doris_debt_source.py # adaptador prod
      mock_debt_source.py  # adaptador test

    comprobantes/          # validación de pago + certificados
      validator.py         # validar_comprobante (hoy en tools/cobranza)
      certificate_pdf.py
      email_delivery.py    # ← email_service (envío de comprobante)

    leads/                # detección de oportunidad + perfil
      lead_machine.py
      prospect_profile.py
      opportunity_detector.py
      visitor_memory.py

    messaging/            # canales de entrada/salida
      whatsapp_service.py
      whatsapp_formatter.py
      chathub_adapter.py
      chathub_outbound.py
      chathub_web_publisher.py

    analytics/
      analytics_sink.py
      dashboard.py         # ← api/dashboard.py (router)

  shared/                 # kernel técnico — NO grita dominio, es plomería
    llm/                  # anthropic, openai, base, factory
    persistence/          # persistence, db, state, redis_store
    rate_limit.py
    webhooks.py
    webhook_config.py
    config/               # settings, cors, tools_schema

  tenancy/                # multi-tenant: carga y configuración por cliente
    tenant_loader.py
    soul.py               # ← config/soul.py
    pricing.py            # ← config/pricing.py

  api/                    # HTTP entrypoints — routers delgados por feature
    main.py               # ← solo app + wiring (de 1905 a ~150 líneas)
    routers/
      chathub.py
      dashboard.py
      webhooks.py

tenants/                  # data por tenant (sin cambios)
```

## Reglas de dependencia (para que no se degrade)

1. `features/*` puede importar de `shared/` y `tenancy/`. **Nunca** al revés.
2. `features/*` NO se importan entre sí salvo vía puertos explícitos (ej. conversation usa cobranza.tools).
3. `shared/` no conoce ningún feature. Es plomería pura.
4. `api/` solo orquesta: importa features, expone routers. Cero lógica de negocio.

## Decisiones abiertas (de Ricky)

- **Alcance**: ¿solo mover archivos (low risk, low value) o también partir los 3 god files (high value, más trabajo)?
- **`shared` vs `kernel` vs `platform`**: nombre del kernel técnico.
- **¿`messaging` un solo feature o partir `whatsapp` / `chathub`?**
- **¿`comprobantes` separado de `cobranza` o subcarpeta?**

## Estrategia de ejecución (segura)

1. SDD: proposal → spec → design → tasks → apply, en slices por feature.
2. Imports: mantener absolutos pero re-mapear (`from features.cobranza.tools import ...`).
3. Un feature por commit. Tests verdes después de cada move antes del siguiente.
4. `git mv` para preservar historia.
5. Test suite completa (18 archivos) como red de seguridad en cada paso.
```
