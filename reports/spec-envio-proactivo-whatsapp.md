# SPEC — Envío proactivo de WhatsApp (cobranza ⇆ ChatHub)

> **Para una sesión nueva sin contexto previo.** Explica cómo funciona el envío de
> WhatsApp en este sistema, por qué "responder" ≠ "enviar proactivo", el estado real
> en producción (verificado por SSH), y qué falta para activar el proactivo.
> Generado 2026-06-02. Complemento: `reports/chathub-envio-proactivo-2026-06-02.md`
> (contrato HTTP detallado de la API de ChatHub).

---

## 0. TL;DR

- El chatbot hoy **conversa** por WhatsApp (recibe y contesta) pero **NO puede iniciar** un mensaje.
- Son dos mecanismos distintos: **Forma A (responder)** funciona; **Forma B (enviar proactivo)** está **apagada en prod** (verificado: las 3 env vars `COBRANZA_CHATHUB_OUTBOUND_*` están vacías en ambos containers).
- El proactivo se hace con `POST /messages/send` (o `/messages/campaign`) contra el **backend-api de ChatHub** (puerto 3030, máquina `200.27.227.216`), autenticando con un **JWT de Firebase** (patrón machine-to-machine).
- Lo que falta NO es código: es **provisioning** (usuario de servicio, número, plantilla HSM, host prod confirmado).

---

## 1. Modelo mental: dos formas de "mandar WhatsApp"

| | **Forma A — Responder** | **Forma B — Enviar proactivo** |
|---|---|---|
| ¿Quién abre la conexión? | ChatHub → chatbot | chatbot/servicio → ChatHub |
| ¿A qué apunta el chatbot? | a nada (responde en el body) | `POST {backend-api}/messages/send` |
| ¿Necesita `OUTBOUND_URL`? | No | **Sí** |
| ¿Requiere plantilla? | No (texto libre, dentro de 24h) | **Sí** (HSM, fuera de 24h) |
| ¿Funciona en prod hoy? | **Sí** ✅ | **No** ❌ (modo SIMULA) |

**Clave conceptual:** el agente NO habla con WhatsApp. Cuando un deudor escribe, ChatHub
le hace un `POST /<tenant>/chat` y el agente **responde síncrono en el body de ese mismo
request**. ChatHub agarra esa respuesta y la entrega al deudor por WhatsApp. El agente solo
contesta peticiones HTTP; nunca inicia una conexión saliente. Por eso "responder" no necesita
ninguna URL configurada, y por eso el demo funciona aunque el outbound esté vacío.

El proactivo es lo contrario: **iniciar** la conversación. No hay request entrante que
contestar → alguien tiene que abrir la conexión hacia ChatHub. Ese "alguien" hoy no existe.

---

## 2. Flujo real de un mensaje entrante (Forma A) — con IPs

```
Deudor →WhatsApp→ Evolution →  CHATHUB (200.27.227.216)
                                  │  POST ${OLIMPO_URL}/<tenant>/chat
                                  ▼
                          chatbot_legacy (gpt_tarjetaoh)  [172.16.250.21:8081]
                                  │  passthrough HTTP /<tenant>/chat
                                  ▼
                          AGENTE DE COBRANZA  [172.16.250.42 / demos.mibot.cl]
                                  │  responde EN EL BODY (síncrono, sin POST de vuelta)
                                  ▲
        la respuesta vuelve por la misma cadena ► ChatHub la entrega por WhatsApp
```

Handler del agente: `apps/agent/api/chathub.py` → `@chathub_router.post("/{bot_path}/chat")`
→ `async def chathub_chat(...)` → `return <respuesta>`. **No hay POST saliente.**

---

## 3. Estado actual en PRODUCCIÓN (verificado por SSH 2026-06-02)

Server de deploy: `ssh automation` (172.16.250.42, user `onbot`, key `id_ed25519_linux`).
Dos containers del bot: `prestamype-demo` y `bu-cobranza-omni`.

```
COBRANZA_CHATHUB_OUTBOUND_URL        = vacío   (en ambos)
COBRANZA_CHATHUB_OUTBOUND_CHANNEL_ID = vacío   (en ambos)
COBRANZA_CHATHUB_OUTBOUND_TOKEN      = vacío   (en ambos)
```

→ El cliente outbound corre en **modo SIMULA** (`apps/agent/integrations/chathub_outbound.py:75-79`:
URL vacía ⇒ loguea `[CHATHUB-OUTBOUND-DRY-RUN]`, retorna False, **no envía nada real**).

El código del envío proactivo **existe** (`ChathubOutboundClient.send_text`) pero **nunca se
activó en prod**. Lo invoca el tool `enviar_info` (`apps/agent/tools/cobranza.py`), que por eso
hoy también simula.

⚠️ Ojo: el `chathub_outbound.py` actual solo arma texto plano `{to, message, channelId}`.
Para proactivo real (plantilla) hay que usar el contrato `type:"template"` de la sección 5
— el cliente Python actual NO lo implementa todavía.

---

## 4. Topología de ChatHub (de `/home/ricardo/projects/chathub`, repo NestJS)

ChatHub es un **gateway multi-tenant multi-proveedor**. Servicios separados detrás de nginx
(`nginx/default.conf`, dev usa `*.chathub.local`):

| Servicio | Puerto | Subdominio dev | Rol |
|---|---|---|---|
| **backend-api** | **3030** | `api.chathub.local` | **API REST: `/messages/send`, `/messages/campaign`, templates** |
| webhook | 5050 | `webhook.chathub.local` | recibe inbound de proveedores |
| messages (WS) | 3003 | `ws.chathub.local` | conversaciones realtime |
| campaigns (WS) | 3004 | `ws-campaigns.chathub.local` | dashboard discador |
| evolution-api | 8080 | `evolution.chathub.local` | proveedor WhatsApp |

**Todos corren juntos en la misma máquina** (`docker-compose.dev.yml`). En prod esa máquina es
`200.27.227.216` (donde resuelve `hook-whatsapp-prod.mibot.cl:5050`).

⚠️ **Trampa:** `hook-whatsapp-prod.mibot.cl:5050` es el **webhook** (recibir), NO el send.
El `/messages/send` vive en **backend-api (3030)**. Su URL pública de prod NO está confirmada:
el repo solo tiene el deploy dev. Pendiente: confirmar por SSH a `200.27.227.216` el nginx/compose
de prod, o pedir a infra el subdominio (probable patrón `api-whatsapp-prod.mibot.cl`, sin confirmar).

Proveedores soportados (abstraídos por `channel_id`): `twilio`, `cloud-api`, `gupshup`,
`wpp-onbotgo`, `olimpo`, `evolution-api`.

---

## 5. Contrato del envío proactivo

> ⚠️ **El repo local `/home/ricardo/projects/chathub` está DESACTUALIZADO respecto a
> producción.** El contrato REAL de prod (confirmado por José Rivas + probe en vivo
> 2026-06-02) es el de la sección 5.0. El de 5.1 (del repo) queda como referencia histórica.

### 5.0 Contrato REAL de producción (confirmado por José + probe 2026-06-02)

Host prod: **`https://api-whatsapp-prod.mibot.cl`** (verificado: responde 403 sin token).

**Variante A — enviar a un número (PROACTIVO), path = channel_id:**
```
POST https://api-whatsapp-prod.mibot.cl/message/send/<channel_id>
Headers:
  Authorization: Bearer <JWT-FIREBASE>
  mibot_session: {"project_uid":"vnbLnzdM0b3BDClTPVPL","client_uid":"lEvxdkHyFXdOX4ieEMHs"}
  Content-Type: application/json
Body:
  { "message": { "type": "text", "content": { "text": "..." } }, "to": "51997443883" }
```

**Variante B — responder en conversación existente, path = conversation_id:**
```
POST https://api-whatsapp-prod.mibot.cl/message/send/101
Body:
  { "type": "text", "content": { "text": "..." } }     // sin "to"
```

Notas:
- Ruta es `/message/send/` (singular) con **id en el path**, NO `/messages/send` con channel en el body.
- Body anidado `{message:{type,content:{text}}}`, NO `{type, text}` plano.
- Probe sin token → `403 {"success":false,"message":"Auth token not found"}` (mismo middleware).
- Los ejemplos de José son `type:"text"`. Para fuera de 24h WhatsApp exige template/HSM —
  confirmar con José cómo se manda template en este endpoint (¿`type:"template"`?).
- `mibot_session` usa la clave `project_uid` (snake_case), no `projectUid`.

### 5.1 Contrato del repo local (REFERENCIA — puede no reflejar prod)

Controller: `chathub/backend-whatsapp/libs/commons/src/unified-message/unified-message.controller.ts`
DTO: `unified-message.dto.ts` (`SendUnifiedMessageDto`)

### 5.1 Individual — `POST /messages/send`
```jsonc
// Headers
Authorization: Bearer <ID-TOKEN-FIREBASE>
mibot_session: {"projectUid":"<tenant>","client_uid":"<...>"}
Content-Type: application/json

// Body proactivo (type=template — OBLIGATORIO fuera de 24h):
{
  "channel_id": "ch_abc123",
  "to": "51999888777",
  "type": "template",
  "template_id": 42,
  "variables": { "1": "Juan", "2": "S/ 1200" }   // rellena {{1}}, {{2}}
}
// OK = 200/201/202
```
Texto libre (`type:"text"`, campo `text`) **solo sirve dentro de la ventana de 24h** (respuesta).

### 5.2 Masivo — `POST /messages/campaign`
DTO `CreateCampaignDto`: `{ name, channel_id, template_id, contacts[{phone,name,variables}],
auto_start, deduplicate, webhook{url,auth_type,token,events} }`. Trae rate-limiting por proveedor
(tabla `send_rate_config`), dedup por teléfono y webhook de estados (`sent`/`failed`/`finished`).

### 5.3 Descubrimiento (no adivinar IDs)
```
GET /messages/channels                  → canales [{id, nombre, numero, plataforma}]
GET /messages/templates?platform=...    → plantillas del tenant
GET /messages/templates/:id             → detalle + variables + ejemplo de request
```

---

## 6. Autenticación machine-to-machine

Middleware: `chathub/backend-whatsapp/apps/backend-whatsapp/src/middlewares/authentication-middleware.ts`

Valida: (1) `Authorization: Bearer <JWT>` verificado con `verifyIdToken()`; (2) `mibot_session`
→ `PROJECT_UID`; (3) el email del token existe en `project_<uid>.user`. Bypass: `DISABLE_AUTH=true`.

**Patrón M2M (el mismo que ChatHub usa internamente, `conversation-handler.service.ts`):**
```
1. POST https://identitytoolkit.googleapis.com/identitytoolkit/v3/relyingparty/verifyPassword?key=<FIREBASE_API_KEY>
   { "email": "<svc-email>", "password": "<svc-pass>" }  → idToken (~1h)
2. POST {backend-api}/messages/send  con  Authorization: Bearer <idToken>
```
El servicio externo necesita un **usuario de servicio** dado de alta en el proyecto + sus
credenciales Firebase. Es alta de cuenta, no código.

---

## 7. Checklist para activar el proactivo (lo que falta)

| # | Tarea | Tipo | Estado |
|---|---|---|---|
| 1 | Usuario de servicio en `project_<uid>.user` + credenciales Firebase | provisioning | ❌ |
| 2 | Provisionar número WhatsApp en ChatHub → `channel_id` | provisioning | ❌ |
| 3 | Cargar + aprobar plantilla HSM (para fuera de 24h) | provisioning/Meta | ❌ |
| 4 | Confirmar host público prod del backend-api (`:3030`) | infra | ❌ |
| 5 | Obtener `mibot_session` del tenant prestamype | infra | ❌ |
| 6 | (Si lo dispara el bot) extender `chathub_outbound.py` para `type:"template"` | código | ❌ |
| 7 | Setear `COBRANZA_CHATHUB_OUTBOUND_URL/CHANNEL_ID/TOKEN` en prod | deploy | ❌ |

**Nada de esto es bloqueante por código de ChatHub** — el endpoint ya existe y soporta todo.
El cuello de botella es provisioning + confirmar el host de prod.

---

## 8. Mapa de archivos (para navegar)

**chatbot-cobranza** (`/home/ricardo/projects/chatbot-cobranza`):
- `apps/agent/integrations/chathub_outbound.py` — cliente outbound (hoy texto plano, SIMULA)
- `apps/agent/integrations/chathub_adapter.py` — traduce salida del agente al contrato ChatHub
- `apps/agent/api/chathub.py` — endpoint inbound `/{bot_path}/chat` (Forma A)
- `apps/agent/config/settings.py:135,146` — `chathub_webhook_url`, `chathub_outbound_*`
- `apps/agent/tools/cobranza.py` — tool `enviar_info` que llama al outbound

**chathub** (`/home/ricardo/projects/chathub/backend-whatsapp`):
- `libs/commons/src/unified-message/unified-message.controller.ts` — `/messages/*`
- `libs/commons/src/unified-message/unified-message.dto.ts` — `SendUnifiedMessageDto`
- `libs/commons/src/unified-message/create-campaign.dto.ts` — `CreateCampaignDto`
- `libs/commons/src/unified-message/unified-message.service.ts` — lógica send (text/template/file)
- `apps/backend-whatsapp/src/middlewares/authentication-middleware.ts` — auth Firebase
- `nginx/default.conf`, `docker-compose.dev.yml` — topología (dev)

---

## 9. Verificación rápida (para la sesión que retome)

```bash
# Estado del outbound en prod (debe seguir vacío hasta que se active):
ssh automation 'docker exec prestamype-demo printenv | grep COBRANZA_CHATHUB_OUTBOUND'

# Confirmar host prod del backend-api (PENDIENTE — requiere acceso a 200.27.227.216):
#   leer nginx/compose de PROD ahí, buscar el server_name que rutea al puerto 3030.
```

**Engram:** topic keys `chathub/proactive-send-contract`, `chathub/outbound-prod-state`.
