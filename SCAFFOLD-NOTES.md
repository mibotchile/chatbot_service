# SCAFFOLD-NOTES — chatbot-cobranza Fase 0

> Estrategia: **template fresco**. Engine probado copiado de
> `portal-inmobiliario/apps/sorelia`, capa de dominio inmobiliaria reemplazada
> por **stubs de cobranza**. Objetivo de Fase 0 cumplido: el proyecto IMPORTA y
> ARRANCA con un tenant vacio. NO hay logica de cobranza todavia.

## Layout y decision de imports

El codigo vive bajo `apps/agent/` pero el engine usa imports absolutos
(`from core.X`, `from api.X`, `from config.X`). Para no reescribir el engine:

- `pyproject.toml`: `[tool.pytest.ini_options] pythonpath = ["apps/agent"]` y
  `[tool.uv] package = false` (es una app, no una libreria).
- Docker: `WORKDIR /app/apps/agent` + `ENV PYTHONPATH=/app/apps/agent`.
- El import de verificacion es `from api.main import app` (no `apps.agent.api.main`).

## Que se COPIO tal cual (ENGINE)

`core/`: agent.py, conversation_fsm.py, tenant_loader.py, lead_machine.py,
persistence.py, visitor_memory.py, redis_store.py, db.py, state.py, hooks.py,
response_builder.py, response_guard.py, webhooks.py, webhook_config.py,
whatsapp_service.py, whatsapp_formatter.py, email_service.py,
opportunity_detector.py, prospect_profile.py, `__init__.py`.
`api/`: main.py, dashboard.py, `__init__.py`. `config/`: settings.py, `__init__.py`.
`prompts/`: system.py, `__init__.py`. Skills genericas: formato-respuesta,
navegacion-web, canal-whatsapp, anti-patrones, herramientas, `__init__.py`.

## Que se STUB-eo (DOMINIO cobranza)

| Archivo | Stub |
|---|---|
| `config/soul.py` | mecanismo igual; defaults cobranza (`role="agente de cobranza"`, tono empatico-firme, usted, excusas placeholder) |
| `config/tools_schema.py` | tools genericas (suggest_quick_replies, navigate_page, collect_contact_info, get_lead_status) + STUBS cobranza con docstring TODO |
| `tools/__init__.py` | `ToolRegistry` engine; wireado a stubs cobranza; mantiene la firma del constructor (ignora meili/visit/calendar por compat) |
| `tools/debt.py` (NUEVO) | get_debt_detail, get_account_status, get_payment_channels — mock minimo + TODO |
| `tools/payment.py` (NUEVO) | simulate_payment_plan, check_discount_eligibility, register_payment_promise — mock + TODO |
| `core/lead_machine.py` | `INTEREST_FIELDS = {debt_amount, days_overdue, account_id, payment_intent, dispute_reason}`; `EXTRACTION_EXCUSES` placeholders cobranza |
| `skills/negociacion-cobranza/`, `skills/regulacion-cobranza/` | placeholders TODO; `DEFAULT_SKILLS` actualizado |
| `prompts/guardrails.md`, `prompts/identity.md` | placeholders cobranza (TODO regulacion/horarios/no-acoso) |
| `knowledge/_schema.md` + `.gitkeep` | schema futuro (faq, payment_channels, discount_rules, account); sin KB todavia |

## Que NO se copio

`tools/finance.py`, `tools/knowledge.py`, `tools/compare.py`, `tools/timeline.py`,
`tools/search.py`, `core/visit_manager.py`, `core/google_calendar.py`,
`knowledge/*.json` inmobiliarios, skills de ventas (arsenal-ventas,
cultura-financiera, metodo-ventas, reglas-fundamentales, faq), tests inmobiliarios,
`apps/web`. Dep `meilisearch` quitada del pyproject.

## Hardcodes limpiados en `api/main.py`

- Imports rotos `core.visit_manager` / `core.google_calendar` eliminados (+ su init
  en lifespan y kwargs en ambos `ToolRegistry(...)`).
- 2x `from tools.knowledge import get_nova_project_detail` en `on_lead_captured` →
  reemplazado por `case_context` neutro (TODO Fase 1: construir contexto de caso).
- `_fallback_response`, greeting de `/page-context`, texto wa.me del limite diario,
  fallback de `get_tenant_contact_phone` → neutralizados (sin "Pia"/"Nova").
- `meilisearch` (busqueda inmobiliaria) → siempre `None` (cobranza no la usa).
- `from core.agent import resetAgent` → eliminado (bug latente, nunca existio).
- default `tenant_id="nova"` → `"demo"`; `env_prefix` `SORELIA_` → `COBRANZA_`;
  `notification_email` y CORS inmobiliarios → neutros.

## DEFERIDO a Fase 1 (deuda documentada, NO rompe el arranque)

Estos quedaron INERTES — sus llamadores fueron removidos, asi que el branding
inmobiliario nunca se dispara, pero el texto sigue ahi:

- `core/whatsapp_service.py`: `send_brochure()` y strings "Nova Inmobiliaria" /
  `demos.mibot.cl` (metodos sin caller).
- `core/email_service.py`: `from_email="...sorelia@novainmobiliaria.pe"`,
  `_brochure_html`, `send_brochure` (sin caller en flujo cobranza).
- `core/hooks.py`: `extract_implicit_data` aun extrae distritos de Lima / dormitorios
  (reescribir para cobranza en Fase 1).
- `core/response_builder.py`: `build_ui_actions` y `build_quick_replies` aun
  ramifican por `search_properties`/`mortgage`/`subsidy`/`projects.json` — ramas
  muertas (tools removidas), protegidas por try/except.
- `api/dashboard.py`: endpoint `/visits` + nombres de tabla `sorelia_*`
  (`_safe_fetch` devuelve `[]` si la tabla no existe).
- Clase `SoreliaAgent` y `app.title="Sorelia API"` conservados (marca del engine,
  no dominio).

## Acoplamientos engine↔dominio encontrados

1. `prompts/system.py` cargaba 3 JSONs de knowledge a NIVEL DE MODULO → crasheaba
   el import sin archivos. Resuelto con `_load_knowledge()` tolerante a ausencia.
2. `api/main.py` importaba 2 modulos NO portados (visit_manager, google_calendar) a
   nivel de modulo → habria roto el import. Removidos.
3. `tools/__init__.py` original importaba 4 modulos de tools inmobiliarios →
   reescrito.
4. `ToolRegistry` recibe `meilisearch/visit_manager/google_calendar` desde el engine
   → constructor mantiene los kwargs (ignorados) para no tocar `api/main.py`.
5. Mismatch de ruta de tenants en runtime: `parent.parent.parent/"tenants"` apunta a
   `apps/tenants` (mal para dev local; bien en Docker via volumen `/app/tenants`).
   Fix en Fase 1. El smoke test carga `_template` por ruta absoluta, no afectado.

## Verificacion (evidencia)

1. `uv sync` → OK (resuelve e instala, Python 3.13).
2. `from api.main import app` → imprime `OK import`.
3. `pytest tests/test_smoke.py -v` → **5 passed**.
4. Commit en rama `main`.

## Proximos pasos

Fase 1: tools de cobranza reales (fuente de deuda), soul/guardrails/skills,
reescribir hooks/response_builder, KB. Fase 2: verificacion de identidad (gate
IDENTITY_CHECK antes de revelar deuda), PII + audit trail, regulacion.
