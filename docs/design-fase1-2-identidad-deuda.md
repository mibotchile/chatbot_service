# Diseño técnico — Fase 1+2: Identidad de deuda + fuente de deuda (API read-only)

> Estado: **DISEÑO (spec), NO implementación.** Aterriza dos decisiones tomadas:
> 1. **Fuente de deuda = API read-only intermedia** (el bot NO toca Postgres core directo).
> 2. **Verificación de identidad = token único por link de campaña** (token → deudor; sin PII en el chat; canal frío sin token → fallback).
>
> Base: engine `sorelia` scaffoldeado (ver `SCAFFOLD-NOTES.md`). Este doc diseña SOBRE el código real, no sobre supuestos. Las citas `archivo.py:line` son al estado actual del scaffold.

---

## 0. Principios de diseño (no negociables para cobranza)

1. **Cero PII en el chat.** El deudor nunca tipea DNI/cuenta. El token ES la identidad.
2. **`account_id` NUNCA viene del LLM ni del usuario.** Se resuelve server-side desde el token y se inyecta en el `tool_registry`. Las tools de deuda ignoran cualquier `account_id` que el LLM intente pasar.
3. **Gate duro:** ninguna tool de deuda se ejecuta sin identidad resuelta. El gate vive en el `ToolRegistry` (defensa real), no solo en el prompt (defensa blanda).
4. **El bot es read-mostly.** Solo un write: `register_payment_promise` (PTP). Va por ruta separada y auditada.
5. **Reuso > reescritura.** El engine ya tiene HMAC tokens, multi-tenant, FSM, response_guard. Se extiende, no se rearquitectura.

---

## A. Contrato de la API read-only intermedia (`cobranza-debt-api`)

Servicio HTTP intermedio que envuelve la BD core de cobranza (Postgres `bd-intranet` DBs `cobranza`/`crm`/`base`). El bot SOLO habla con esta API; nunca con Postgres. Esto desacopla, permite cache, audit, y rate-limit independiente del core.

### Auth bot → API

- **Mutual API key + scope.** El bot manda `Authorization: Bearer <COBRANZA_API_KEY>` por tenant. Cada key tiene scope `read:debt` y (solo para PTP) `write:ptp`.
- La key NO va en el repo: env `COBRANZA_API_KEY` (o `COBRANZA_API_KEYS` JSON por tenant, igual patrón que `anthropic_tenant_keys` en `settings.py:16`).
- Recomendado: mTLS si la API queda fuera de la red interna; si queda dentro de la VPN, API key + IP allowlist alcanza.

### Base URL / config (settings.py)

Agregar a `Settings` (`config/settings.py`), prefijo `COBRANZA_` (ya es el `env_prefix`, settings.py:59):

```python
debt_api_url: str = ""              # COBRANZA_DEBT_API_URL  ej https://cobranza-api.internal:8090
debt_api_key: str = ""              # COBRANZA_DEBT_API_KEY  (fallback single-tenant)
debt_api_keys: str = "{}"           # COBRANZA_DEBT_API_KEYS  JSON {tenant_id: key}
debt_api_timeout_s: float = 5.0     # COBRANZA_DEBT_API_TIMEOUT_S
debt_token_resolve_path: str = "/v1/campaign-token/resolve"
debt_cache_ttl_s: int = 120         # cache deuda en memoria por conversación
ptp_write_enabled: bool = False     # gate de feature: PTP write off por defecto
```

`resolve_debt_api_key(tenant_id)` espejo exacto de `resolve_api_key` (settings.py:84-92).

### Endpoints

Todos devuelven `application/json`. Errores con `{"error": {"code": "...", "message": "..."}}` + HTTP status. `code` es enum estable (abajo).

#### A.1 — `POST /v1/campaign-token/resolve` ⭐ (endpoint clave)

Dado un token de campaña, devuelve identidad + deuda en UNA llamada (minimiza round-trips; el bot lo cachea por conversación).

Request:
```json
{ "token": "ey...", "tenant_id": "clienteX" }
```

Response 200:
```json
{
  "valid": true,
  "debtor": {
    "debtor_ref": "d_8f3a...",         // opaco, NO el DNI; se usa internamente
    "display_name": "Juan P.",          // parcial, para saludar (nunca nombre completo en logs)
    "account_id": "ACC-99812"           // identificador de cuenta interno
  },
  "debt": {                              // resumen embebido (evita 2do call)
    "currency": "PEN",
    "total": 3450.00,
    "capital": 2900.00,
    "interest": 410.00,
    "late_fees": 140.00,
    "days_overdue": 73,
    "tramo": "60-90",
    "status": "overdue"
  },
  "campaign": { "id": "camp-2026-05", "channel": "whatsapp" },
  "token_meta": { "issued_at": "2026-05-20T10:00:00Z", "expires_at": "2026-06-20T10:00:00Z" }
}
```

Response 200 inválido / 401:
```json
{ "valid": false, "error": { "code": "TOKEN_EXPIRED", "message": "..." } }
```

`error.code` enum: `TOKEN_INVALID`, `TOKEN_EXPIRED`, `TOKEN_REVOKED`, `DEBTOR_NOT_FOUND`, `RATE_LIMITED`, `UPSTREAM_DOWN`.

> **Decisión:** el resolve devuelve el `debt` resumido embebido. Así el gate de identidad y el primer `get_debt_detail` se sirven de un solo round-trip cacheado. `get_debt_detail` solo re-llama si el bot necesita el breakdown completo o si expiró el cache.

#### A.2 — `GET /v1/accounts/{account_id}/debt`

Detalle de deuda. `account_id` viene del resolve (server-side), nunca del LLM.

Response 200:
```json
{
  "account_id": "ACC-99812", "currency": "PEN",
  "total": 3450.00, "capital": 2900.00, "interest": 410.00, "late_fees": 140.00,
  "days_overdue": 73, "tramo": "60-90", "status": "overdue",
  "last_payment": { "date": "2026-02-10", "amount": 200.00 }
}
```

#### A.3 — `GET /v1/accounts/{account_id}/status`

Estado liviano (al día / mora / días / tramo). Subconjunto de A.2; existe para `get_account_status` sin traer breakdown.

#### A.4 — `POST /v1/accounts/{account_id}/payment-plan/simulate`

Request: `{ "installments": 6 }` (o `{ "down_payment": 500 }`).
Response:
```json
{
  "account_id": "ACC-99812", "installments": 6, "currency": "PEN",
  "schedule": [
    { "n": 1, "due_date": "2026-06-15", "amount": 575.00 },
    { "n": 2, "due_date": "2026-07-15", "amount": 575.00 }
  ],
  "total_with_plan": 3450.00, "constraints": { "min_installment": 100.00, "max_installments": 12 }
}
```

> **Importante:** la simulación es **read** (no compromete nada). Solo `register_payment_promise` (A.7) escribe.

#### A.5 — `GET /v1/accounts/{account_id}/discount-eligibility`

Response:
```json
{
  "account_id": "ACC-99812", "eligible": true,
  "offers": [
    { "type": "quita", "pct": 30, "valid_until": "2026-06-05", "conditions": "pago único al contado" }
  ]
}
```
Si `eligible:false` → `offers: []`. **Regulatorio:** el bot solo ofrece lo que la API devuelve; nunca inventa quitas (soul.py:97 `pricing_policy=from_database_only`).

#### A.6 — `GET /v1/accounts/{account_id}/payment-channels`

```json
{
  "channels": [
    { "type": "link", "label": "Pago online", "url": "https://pay..../ACC-99812?t=..." },
    { "type": "transfer", "label": "Transferencia", "bank": "...", "cci": "..." },
    { "type": "agent", "label": "Agente Niubiz", "code": "99812" }
  ]
}
```
El `url` lo genera la API (token de pago propio, distinto del de campaña). El bot lo pasa tal cual.

#### A.7 — `POST /v1/accounts/{account_id}/payment-promise` ⚠️ WRITE

> **Decisión sobre el write:** NO va en la API read-only. Va en la **misma API pero en una ruta con scope `write:ptp`** y feature-flag `ptp_write_enabled` (default off). Razón: una sola superficie de integración para el bot, pero auth/audit separados. Si infra prefiere otro servicio, el contrato es idéntico y solo cambia base URL.

Request:
```json
{ "amount": 575.00, "promise_date": "2026-06-15", "channel": "whatsapp", "conversation_id": "uuid", "source": "bot" }
```
Response 201:
```json
{ "registered": true, "ptp_id": "ptp_4471", "account_id": "ACC-99812", "amount": 575.00, "promise_date": "2026-06-15" }
```
Idempotencia: header `Idempotency-Key: <conversation_id>:<promise_date>` para que un reintento no duplique PTP.

### Manejo de errores (bot side)

| Situación | Status | `error.code` | Comportamiento del bot |
|---|---|---|---|
| Token inválido/firmado mal | 401 | `TOKEN_INVALID` | Estado `IDENTITY_FAILED` → fallback canal frío |
| Token expirado | 401 | `TOKEN_EXPIRED` | Mensaje "tu enlace venció" + CTA reenvío / humano |
| Deudor no existe | 404 | `DEBTOR_NOT_FOUND` | Escalar a humano (no exponer que no existe) |
| API timeout (>5s) | — | — | "no puedo consultar ahora" + reintento 1x + fallback |
| API 5xx | 5xx | `UPSTREAM_DOWN` | Degradar a fallback; loguear; NO inventar deuda |
| Rate limited | 429 | `RATE_LIMITED` | Backoff; mensaje genérico |

Cliente HTTP: `httpx.AsyncClient` (ya importado en `api/main.py:15`), timeout de `debt_api_timeout_s`, 1 reintento con jitter solo en timeout/5xx (NUNCA en 4xx).

### Cache

- **Cache por conversación, no global.** Tras `resolve`, guardar `{account_id, debt_snapshot, expires}` en el `ConversationState` (state.py:23, agregar campo `debt_context`) o en Redis si está activo (`redis_store.py`).
- TTL `debt_cache_ttl_s` (120s default). Evita re-pegarle a la API en cada turno del LLM.
- **NUNCA cachear el token resuelto entre conversaciones distintas.** El cache es scoped a `conversation_id`.

---

## B. Flujo de token de campaña

### B.1 — Cómo entra el token al bot HOY (estado real)

`ChatRequest` (api/main.py:530-559) tiene: `channel, tenant_id, text, conversation_id, visitor_id, previous_response_id, page_context`. **NO hay campo token.** `page_context` es un `dict | None` libre — es el punto de entrada natural sin tocar el contrato del frontend.

Dos canales:

**Web widget (query param):**
- Link de campaña: `https://portal.clienteX.cl/?ct=<TOKEN>`.
- El widget lee `?ct=` y lo manda en `page_context.campaign_token` en el primer `POST /api/v1/chat` (o en `/api/v1/page-context`, api/main.py:869).
- Recomendado: agregar campo explícito `campaign_token: str | None = None` a `ChatRequest` para validación/longitud, en vez de esconderlo en `page_context`. Es 1 línea y da control (Pydantic valida formato).

**WhatsApp (deep-link):**
- `wa.me` no propaga query params arbitrarios al servidor; lo que llega es el **texto del primer mensaje**.
- Patrón: `https://wa.me/<phone>?text=Hola%20CT-<TOKEN>` → el deudor envía "Hola CT-<TOKEN>".
- El webhook (api/main.py:893) ya extrae `text`. En `_process_whatsapp_message` (api/main.py:103) se parsea el token del primer mensaje con regex `CT-([A-Za-z0-9_\-\.]+)` y se guarda en el `ConversationState` ANTES de invocar al agente.
- Encaja perfecto con el modo `website_leads_only` ya existente (api/main.py:1023): el "trigger phrase" pasa a ser el prefijo `CT-`. Sin token → `fallback_reply` del tenant.

> **Conflicto/riesgo #1:** WhatsApp NO da el token de forma confiable más allá del primer mensaje. Si el deudor borra el texto pre-cargado o escribe "Hola" a secas, no hay token → cae a canal frío. Mitigación: el bot puede pedir "reenviá el enlace que te mandamos" en `IDENTITY_FAILED`, pero NUNCA pedir DNI en el chat.

### B.2 — Validación y resolución (token → account_id → deuda)

1. Request entra (web: `page_context.campaign_token`; WA: parseado del primer texto).
2. Si hay token y la conversación aún no está verificada → llamar `POST /v1/campaign-token/resolve` (A.1).
3. Si `valid:true` → guardar en `ConversationState.debt_context`: `{verified:true, account_id, debtor_ref, debt_snapshot}`. Marcar `conv.identity_verified = True`.
4. El `ToolRegistry` se construye con `debt_context` → habilita tools de deuda.
5. Si `valid:false` → `conv.identity_verified = False`, estado FSM `IDENTITY_FAILED`.

### B.3 — Canal frío (SIN token) — fallback definido

Un deudor que escribe sin token (lo encontró el número, link viejo, etc.):

- Estado FSM: **`COLD`** (nuevo, ver C).
- El bot **NO revela ni pide nada sensible.** Tools de deuda DESHABILITADAS.
- Responde con guion frío: "Hola, soy {agent} de {company}. Para ayudarte con tu cuenta necesito que ingreses por el enlace seguro que te enviamos. ¿Lo tenés a mano?" + quick replies: ["No tengo el enlace" → escalate_to_human, "Hablar con una persona" → escalate].
- Tools permitidas en COLD: `suggest_quick_replies`, `escalate_to_human`, `get_payment_channels` SOLO si el tenant lo permite genérico (canales públicos sin cuenta). El resto: bloqueadas.

### B.4 — Expiración / seguridad del token (recomendación)

- **TTL:** 30 días desde emisión de campaña (configurable). El resolve valida `expires_at` server-side; el bot confía en el `valid` de la API.
- **Firma:** el token DEBE ser firmado/opaco (JWT firmado por el emisor de campaña, o token opaco random con lookup en la API). NO un `account_id` plano ni un correlativo adivinable.
- **Recomendación concreta:** token opaco aleatorio (>=128 bits) almacenado en la API con su mapping a `account_id` + estado (`active/expired/revoked`). Ventaja sobre JWT: revocación inmediata (campañas se cancelan), y el bot no necesita la clave de firma.
- **Un solo uso vs reusable:** reusable dentro del TTL (el deudor puede volver al chat). Pero loguear cada resolve para detectar abuso (mismo token desde N teléfonos distintos → flag).
- El bot **NUNCA** loguea el token en claro (ver E).

---

## C. Rediseño del FSM con gate de identidad

### C.1 — Estados actuales (conversation_fsm.py:11-16)

`GREETING, EXPLORING, INTERESTED, QUALIFYING, CLOSING, ENRICHING`.

`detect_state` (conversation_fsm.py:49-97) es **puramente data-driven** desde `lead_status.collected` + `history` + `page_context`. **NO hay ningún concepto de identidad ni de "gate".** Los estados inmobiliarios (EXPLORING/INTERESTED/QUALIFYING/CLOSING) asumen captura progresiva sin auth — exactamente lo que cobranza NO puede hacer.

> **Conflicto/riesgo #2 (el más importante):** el FSM actual decide estado por *completitud de datos de lead*, no por *autorización*. En cobranza el eje es "¿verificado o no?", que es ortogonal a "¿cuántos datos tengo?". Meter identidad como otro `if` en `detect_state` lo ensucia. **Decisión de diseño:** la identidad NO es un estado más en la misma dimensión — es un **gate previo** que cortocircuita `detect_state`.

### C.2 — Diseño del gate: dos capas

**Capa 1 — Estado FSM (defensa blanda, prompt):** agregar 3 estados que tienen prioridad ABSOLUTA sobre los demás. `detect_state` chequea identidad ANTES que nada.

**Capa 2 — ToolRegistry (defensa dura, ejecución):** el registry recibe `identity_verified: bool` + `account_id`. Si no verificado, `execute()` de cualquier tool de deuda devuelve `{"blocked": "identity_required"}` SIN pegarle a la API. Esto es lo que realmente protege — el prompt puede fallar, el código no.

### C.3 — Estados nuevos

| Estado | Cuándo | Tools habilitadas |
|---|---|---|
| `IDENTITY_CHECK` | Hay token, resolviendo | suggest_quick_replies (espera) |
| `IDENTITY_FAILED` | Token inválido/expirado | suggest_quick_replies, escalate_to_human |
| `COLD` | Sin token (canal frío) | suggest_quick_replies, escalate_to_human, (get_payment_channels genérico opcional) |
| *(verificado)* | resolve ok → sigue al FSM normal | TODAS las de deuda |

Una vez verificado, **se reutiliza el FSM existente** (EXPLORING→...→CLOSING) pero reinterpretado para cobranza: EXPLORING = explorar opciones de pago, CLOSING = registrar PTP. No se inventan estados nuevos para el post-verificación; se reusa el mecanismo.

### C.4 — Cambio en `detect_state` (mínimo, quirúrgico)

Agregar al inicio de `detect_state`, ANTES del check de `if not history` (conversation_fsm.py:67):

```python
identity = lead_status.get("identity", {})   # nuevo: inyectado desde ConversationState
if identity.get("status") == "resolving":
    return IDENTITY_CHECK
if identity.get("status") == "failed":
    return IDENTITY_FAILED
if identity.get("status") == "cold":
    return COLD
# si verified → cae al flujo normal de abajo (reusado)
```

`lead_status` ya se pasa entero (system.py:157 llama `detect_state(lead_state, ...)`). Se inyecta `identity` en el dict de `get_status()` o se pasa aparte. **Recomendación:** pasar `identity` como 4º arg explícito a `detect_state` para no contaminar el lead_machine con concerns de auth (mantiene SRP).

### C.5 — Diagrama ASCII

**ANTES (inmobiliario, data-driven):**
```
                 ┌─────────┐
   (no history)  │ GREETING│
                 └────┬────┘
                      ▼
                 ┌─────────┐   has_project   ┌───────────┐
                 │EXPLORING├────────────────►│ INTERESTED│
                 └────┬────┘                  └─────┬─────┘
       has_name/email │                            │
                      ▼                            ▼
                ┌──────────┐  name+contact   ┌─────────┐  +enrichment ┌──────────────┐
                │QUALIFYING├────────────────►│ CLOSING ├─────────────►│  ENRICHING   │
                └──────────┘                  └─────────┘              └──────────────┘
```

**DESPUÉS (cobranza, gate de identidad primero):**
```
  request entra
       │
       ▼
  ┌──────────────┐  no token        ┌──────┐  escalate   ┌─────────────────┐
  │ token check  ├─────────────────►│ COLD ├────────────►│ escalate_to_human│
  └──────┬───────┘                  └──────┘             └─────────────────┘
         │ token presente
         ▼
  ┌──────────────┐  resolve()
  │IDENTITY_CHECK│──────────────┐
  └──────────────┘              │
         │ valid:true           │ valid:false / expired
         ▼                      ▼
  ┌──────────────┐       ┌────────────────┐  reenvío/ humano
  │  VERIFIED    │       │ IDENTITY_FAILED│
  │  (gate open) │       └────────────────┘
  └──────┬───────┘
         │  ↓ se reusa el FSM existente, reinterpretado a cobranza:
         ▼
  ┌─────────┐   ┌──────────┐   ┌─────────┐   ┌──────────┐
  │GREETING │──►│EXPLORING │──►│QUALIFYNG│──►│ CLOSING  │  (= registrar PTP)
  └─────────┘   │(opciones │   │(plan/   │   └──────────┘
                │ de pago) │   │ descuento)│
                └──────────┘   └──────────┘
  ─────────────────────────────────────────────────────────
  GATE DURO (ToolRegistry): get_debt_detail / status / plan / discount /
  channels / PTP  →  SOLO ejecutables si identity_verified == True.
  En COLD / IDENTITY_FAILED / IDENTITY_CHECK devuelven {"blocked":"identity_required"}.
```

### C.6 — Dónde vive el flag (state.py)

`ConversationState` (state.py:23) gana:
```python
self.identity_verified: bool = False
self.debt_context: dict = {}   # {account_id, debtor_ref, debt_snapshot, expires}
self.identity_status: str = "cold"  # cold | resolving | verified | failed
```
Se persiste igual que el resto (state.py:67 `_persist`). En reconexión (state.py:117 `get_or_create_async`) se restaura → un deudor verificado no re-verifica al volver dentro del TTL.

---

## D. Mapeo tools → API

| Tool (stub actual) | Endpoint API | `account_id` viene de | Devuelve al LLM | Gate |
|---|---|---|---|---|
| `get_debt_detail` (debt.py:10) | `GET /v1/accounts/{id}/debt` (A.2) o cache del resolve | `debt_context` (server) | total, capital, interés, mora, días, tramo | ✅ verified |
| `get_account_status` (debt.py:23) | `GET /v1/accounts/{id}/status` (A.3) | `debt_context` | status, días, tramo | ✅ verified |
| `get_payment_channels` (debt.py:33) | `GET /v1/accounts/{id}/payment-channels` (A.6) | `debt_context` | lista de canales + URLs | ✅ verified (genérico permitido en COLD si tenant lo activa) |
| `simulate_payment_plan` (payment.py:9) | `POST /v1/accounts/{id}/payment-plan/simulate` (A.4) | `debt_context` | cuotas, fechas, montos | ✅ verified |
| `check_discount_eligibility` (payment.py:20) | `GET /v1/accounts/{id}/discount-eligibility` (A.5) | `debt_context` | eligible + ofertas (quita/desc) | ✅ verified |
| `register_payment_promise` (payment.py:33) | `POST /v1/accounts/{id}/payment-promise` (A.7) ⚠️write | `debt_context` | ptp_id, confirmación | ✅ verified + `ptp_write_enabled` |
| `escalate_to_human` (tools/__init__.py:133) | (interno: webhook/cola) | — | confirmación de derivación | siempre (incl. COLD) |

**Cambio clave en los stubs:** hoy `get_debt_detail(account_id)` recibe `account_id` del LLM (tools_schema.py:67-76, payload). **Hay que sacar `account_id` del `input_schema` de TODAS las tools de deuda.** El LLM no debe poder pasarlo. El `ToolRegistry` lo inyecta desde `debt_context`. Esto cierra el agujero de que el LLM "adivine" o el usuario dicte una cuenta ajena.

> **Conflicto/riesgo #3:** los `input_schema` actuales (tools_schema.py:67-145) exponen `account_id` como required. Eso contradice el principio 2 (account_id nunca del LLM). Hay que reescribir los schemas: quitar `account_id`, dejar solo params de negocio (`installments`, `amount`, `promise_date`). El registry inyecta account_id. Riesgo si no se hace: el LLM podría llamar `get_debt_detail(account_id="otra cuenta")`.

---

## E. PII / audit / regulación

### E.1 — Qué se loguea y qué NO

**PROHIBIDO loguear:**
- Token de campaña en claro (en logs, ni truncado de forma reversible). Si hay que correlacionar: hash SHA-256 de los primeros bytes, o un `token_id` opaco que devuelva la API.
- Monto de deuda **junto a** identidad (nombre, DNI, teléfono, account_id) en la misma línea de log.
- DNI / documento completo. (Hoy no se pide — mantener así.)
- Respuesta del resolve completa en `INFO`. Solo `debug` con campos enmascarados.

**Permitido:**
- `conversation_id` (UUID, no PII).
- `account_id` SOLO en logs de audit firmados (no en el log de app general).
- Eventos: `identity_resolved | identity_failed | debt_viewed | ptp_registered` SIN el monto.
- Hoy ya se loguea bien: api/main.py:903 loguea evento+instancia, no PII. Mantener ese estándar.

### E.2 — Audit trail (nuevo, requisito Fase 2)

Tabla nueva `cobranza_audit` (o vista en la debt-api). Cada revelación de deuda y cada PTP genera un registro:

```json
{
  "ts": "2026-05-26T15:00:00Z",
  "conversation_id": "uuid",
  "tenant_id": "clienteX",
  "token_id": "tk_opaco",          // NO el token
  "account_id": "ACC-99812",
  "event": "debt_viewed",          // identity_resolved | debt_viewed | plan_simulated | discount_viewed | ptp_registered | escalated
  "channel": "whatsapp",
  "detail_ref": "ptp_4471"         // sin montos en claro; referencia al registro
}
```

Responde a: **qué se reveló, a quién (token/account), cuándo, por qué canal.** Es requisito regulatorio de cobranza. Escribir desde el bot vía la debt-api (`POST /v1/audit`) o async a Postgres del bot. **Decisión:** que lo escriba la **debt-api** (ya está cerca del core y tiene el contexto), el bot solo manda el evento.

### E.3 — Guardrails regulatorios (response_guard + prompts/guardrails.md)

`response_guard.py` HOY solo des-duplica pedidos de datos de contacto (guard_response, response_guard.py:70). **No valida nada regulatorio.** Hay que extenderlo (sin romper lo existente) con un segundo pasaje:

| Guardrail | Regla | Dónde |
|---|---|---|
| **No-acoso / no-amenaza** | Bloquear/reescribir frases de amenaza ("embargo", "te denuncio", "consecuencias legales") salvo info factual escalada | nuevo `_REGULATORY_PATTERNS` en response_guard |
| **No prometer lo que no está en la API** | El bot solo cita montos/quitas que vinieron de A.2/A.5. Si el texto menciona un descuento NO retornado por la API → strip o escalar | response_guard + check contra `tool_pairs` |
| **Horarios de contacto** | El bot responde a inbound siempre, pero NO inicia outbound fuera de horario. (Outbound es de campaña, fuera de scope del bot reactivo — documentar el límite) | a nivel de campaña/scheduler, no del bot |
| **Tono digno** | soul.py:26 ya fija `tone=empatico y firme`, `formality=usted`. Reforzar en guardrails.md | prompts/guardrails.md |
| **No revelar deuda sin verificar** | Defensa dura ya en ToolRegistry (C.2); el guard es backup: si el texto contiene un monto y `identity_verified==False` → strip | response_guard |
| **Legal → escalar** | soul.py:51 `legal_policy=escalate_always`. Cualquier consulta legal → `escalate_to_human` | prompt + tool |

`prompts/guardrails.md` y `prompts/identity.md` son placeholders hoy (SCAFFOLD-NOTES.md:40). Acá se llenan con: no-acoso, qué no prometer, escalamiento legal, no pedir PII.

> **Riesgo #4:** `guard_response` opera sobre texto con regex en español — frágil para detectar amenazas. El gate DURO de tools es la protección real de "no revelar sin verificar"; el guard regulatorio de texto es best-effort, no la única línea de defensa. No confiar el cumplimiento regulatorio solo a regex.

---

## F. Checklist de implementación Fase 1+2

> Leyenda: **[MOCK]** = se puede hacer ya contra la API mockeada (solo necesita el contrato de A). **[REAL]** = necesita la debt-api real desplegada.

### F.0 — Contrato y mock de la API (desbloquea todo lo demás)
- [ ] **[MOCK]** Escribir OpenAPI/JSON-schema de los 7 endpoints de la sección A (artefacto compartido con infra).
- [ ] **[MOCK]** Levantar un mock server (FastAPI stub o `prism`/`json-server`) que responda A.1–A.7 con fixtures. Esto permite desarrollar el bot entero sin el core.

### F.1 — Config (settings)
- [ ] `config/settings.py` — agregar `debt_api_url`, `debt_api_key(s)`, `debt_api_timeout_s`, `debt_token_resolve_path`, `debt_cache_ttl_s`, `ptp_write_enabled`. Agregar `resolve_debt_api_key(tenant_id)`.
- [ ] `config/settings.py` — agregar `campaign_token` handling docs.

### F.2 — Cliente de la debt-api (nuevo módulo)
- [ ] **[MOCK]** `apps/agent/integrations/debt_api.py` (NUEVO) — cliente `httpx.AsyncClient`: `resolve_token`, `get_debt`, `get_status`, `simulate_plan`, `discount_eligibility`, `payment_channels`, `register_ptp`. Timeout, 1 retry en 5xx/timeout, mapeo de `error.code` a excepciones tipadas (`TokenInvalid`, `TokenExpired`, `DebtorNotFound`, `UpstreamDown`).
- [ ] **[MOCK]** Tests del cliente contra el mock (happy + cada error).

### F.3 — Token en el ingreso del request
- [ ] `api/main.py:530` — `ChatRequest`: agregar `campaign_token: str | None = None` (+ field_validator de longitud/charset).
- [ ] `api/main.py:893` webhook WA + `_process_whatsapp_message` (api/main.py:103) — parsear `CT-<token>` del primer mensaje; guardar en `ConversationState` antes de invocar al agente.
- [ ] `api/main.py:869` `/page-context` — aceptar `campaign_token` (web widget).
- [ ] Reusar modo `website_leads_only` (api/main.py:1023): trigger phrase = prefijo `CT-`.

### F.4 — Estado de conversación (gate state)
- [ ] `core/state.py:23` `ConversationState` — agregar `identity_verified`, `identity_status`, `debt_context`. Persistir/restaurar (state.py:67, :117).
- [ ] **[MOCK]** Lógica de resolución: en `api/main.py` (chat + WA handler), si hay token y no verificado → `debt_api.resolve_token` → poblar `debt_context` + `identity_status`.

### F.5 — FSM (gate blando)
- [ ] `core/conversation_fsm.py` — agregar estados `IDENTITY_CHECK`, `IDENTITY_FAILED`, `COLD`. Reglas en `_STATE_RULES`.
- [ ] `core/conversation_fsm.py:49` `detect_state` — agregar 4º param `identity: dict` y cortocircuito de identidad ANTES del flujo data-driven.
- [ ] `prompts/system.py:157` — pasar `identity` (desde `conv`) a `detect_state`.
- [ ] Reescribir `_STATE_RULES` de EXPLORING/INTERESTED/QUALIFYING/CLOSING/ENRICHING (conversation_fsm.py:20-46) — hoy son texto inmobiliario; reinterpretar a cobranza post-verificación.

### F.6 — ToolRegistry (gate duro)
- [ ] `tools/__init__.py:21` `ToolRegistry.__init__` — recibir `identity_verified: bool`, `debt_context: dict`, `debt_api` client.
- [ ] `tools/__init__.py:59` `execute` — si tool ∈ {debt tools} y no verified → `return {"blocked":"identity_required"}` SIN llamar la API.
- [ ] `api/main.py:147` y `:669` — pasar `identity_verified`, `debt_context`, `debt_api` al construir el registry.

### F.7 — Tools reales (reemplazar stubs)
- [ ] **[REAL]** `tools/debt.py` — `get_debt_detail`/`get_account_status`/`get_payment_channels` → llamar `debt_api` con `account_id` del `debt_context` (no del arg). (funciona con [MOCK] para dev.)
- [ ] **[REAL]** `tools/payment.py` — `simulate_payment_plan`/`check_discount_eligibility`/`register_payment_promise` → `debt_api`. PTP detrás de `ptp_write_enabled`.
- [ ] `config/tools_schema.py:65-145` — **quitar `account_id` de TODOS los input_schema de deuda** (riesgo #3). Dejar solo params de negocio.

### F.8 — Guardrails regulatorios
- [ ] `core/response_guard.py:70` — segundo pasaje `_REGULATORY_PATTERNS` (no-acoso, amenazas) + check "monto sin verificar" + "descuento no retornado por API". Sin romper el des-dup existente.
- [ ] `prompts/guardrails.md` + `prompts/identity.md` — llenar placeholders: no-acoso, qué no prometer, escalar legal, jamás pedir PII.

### F.9 — Audit / PII
- [ ] **[REAL]** Definir `cobranza_audit` (tabla en bot o vista en debt-api). Eventos de E.2.
- [ ] **[MOCK]** Emisión de eventos de audit desde el bot (resolve/debt_viewed/ptp). Masking de logs: token nunca en claro; nunca monto+identidad juntos.
- [ ] Revisar TODO log nuevo contra E.1.

### F.10 — Tenant config (Fase 3 overlap, pero el schema se define ya)
- [ ] `tenants/_template/tenant.config.json` — agregar bloque `cobranza`: `debt_api_url` override, `cold_fallback_reply`, `payment_channels_public` (bool), `discount_policy`, `escalation` (cola humana).

### Orden sugerido
F.0 → F.1 → F.2 (todo [MOCK]) → F.3/F.4/F.5/F.6 (gate, [MOCK]) → F.7 (swap a [REAL] cuando exista la API) → F.8/F.9 → F.10.

**Todo el bot se puede construir y testear end-to-end con la API mockeada (F.0).** Solo F.7 y F.9-real necesitan la debt-api desplegada.

---

## Resumen de riesgos / conflictos con el engine real

| # | Conflicto | Severidad | Mitigación |
|---|---|---|---|
| 1 | WhatsApp no propaga token de forma confiable (solo primer texto) | Media | Prefijo `CT-` + modo `website_leads_only`; sin token → COLD, jamás pedir PII |
| 2 | `detect_state` es data-driven (completitud), no auth-driven; identidad es ortogonal | **Alta** | Gate previo que cortocircuita `detect_state`; identidad como 4º param, no como otro `if` de datos |
| 3 | `input_schema` de tools expone `account_id` (LLM podría dictarlo) | **Alta** | Quitar `account_id` de los schemas; inyectar server-side desde `debt_context` |
| 4 | `response_guard` es regex-only; débil para cumplimiento regulatorio | Media | Gate DURO en ToolRegistry es la protección real; guard de texto es backup best-effort |
| 5 | Stubs de tools hoy esperan `account_id` (debt.py/payment.py) | Baja | Refactor de firmas en F.7 |
