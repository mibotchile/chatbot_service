# chatbot-cobranza

Chatbot multi-tenant de **cobranza**. El motor (loop del agente, multi-tenancy,
FSM, lead machine, persistencia, WhatsApp) fue extraido del agente probado
`apps/sorelia` (portal-inmobiliario). La capa de dominio inmobiliaria fue
reemplazada por stubs de cobranza.

> Estado: **Fase 0 — scaffold**. El proyecto importa y arranca con un tenant
> vacio. La logica de cobranza aun NO esta implementada (tools son stubs con TODO).
> Ver `SCAFFOLD-NOTES.md` para el detalle de que se copio, stub-eo y difirio.

## Layout

```
apps/agent/
  core/      ENGINE — loop, FSM, lead machine, persistencia, WhatsApp, email
  api/       ENGINE — FastAPI app (chat, webhook WhatsApp, dashboard)
  config/    settings (engine) + soul/tools_schema (dominio cobranza)
  tools/     DOMINIO — stubs de cobranza (debt.py, payment.py, registry)
  skills/    skills genericas + placeholders cobranza
  knowledge/ schema futuro (sin KB todavia)
  prompts/   system builder + guardrails/identity placeholders
tenants/
  _template/ tenant de referencia (config + knowledge + guardrails)
infrastructure/  Dockerfile + docker-compose + traefik
tests/       test_smoke.py
```

## Requisitos

- Python 3.12+
- `uv`

## Arranque (dev)

```bash
uv sync
# importa la app sin levantar nada:
uv run python -c "import sys; sys.path.insert(0,'apps/agent'); from api.main import app; print('OK')"
# levantar el server (necesita .env con COBRANZA_ANTHROPIC_API_KEY, etc.):
cd apps/agent && uv run uvicorn api.main:app --host 0.0.0.0 --port 8000
```

## Tests

```bash
uv run pytest tests/ -v
```

## Configuracion

Variables de entorno con prefijo `COBRANZA_` (ver `apps/agent/config/settings.py`).
Tenants en `tenants/{slug}/tenant.config.json`. Crear un chatbot nuevo no requiere
tocar el backend: agregar tenant + knowledge + (opcional) instancia WhatsApp.

## Proximos pasos

Fase 1: implementar tools de cobranza, soul/guardrails/skills reales, KB.
Fase 2: verificacion de identidad (gate antes de revelar deuda), PII, regulacion.
Ver `reports/chatbot-cobranza-plan-2026-05-26.md` en el repo de planes.
