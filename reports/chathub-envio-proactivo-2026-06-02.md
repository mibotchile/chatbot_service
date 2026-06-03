# ChatHub — Contrato técnico de envío (proactivo y de sesión)

> Fuente: código real de `/home/ricardo/projects/chathub/backend-whatsapp` (NestJS monorepo).
> Para integrar **cualquier sistema externo** que quiera mandar WhatsApp vía ChatHub.
> No depende del chatbot-cobranza — es la API REST unificada de ChatHub.

## Resumen ejecutivo

- ChatHub es un **gateway multi-tenant multi-proveedor**. Tu sistema le pega a UNA API REST y ChatHub habla con Twilio / Meta Cloud API / Gupshup / Evolution / Olimpo / WPP-OnBotGo por debajo.
- Hay **3 endpoints** que importan para enviar: `POST /messages/send` (individual), `POST /messages/campaign` (masivo), y `GET /messages/templates` (descubrir plantillas).
- **Proactivo = template obligatorio.** Texto libre solo sirve dentro de la ventana de 24h. Para iniciar en frío mandás `type:"template"` con `template_id` + `variables`.
- Auth = **Bearer JWT (Firebase)** + header **`mibot_session`** que resuelve el tenant (`PROJECT_UID`).
- Swagger interactivo en `/api` del backend-whatsapp.

---

## 1. Endpoint individual — `POST /messages/send`

Controller: `libs/commons/src/unified-message/unified-message.controller.ts:61`
DTO: `libs/commons/src/unified-message/unified-message.dto.ts` (`SendUnifiedMessageDto`)

### Headers
```
Authorization: Bearer <JWT-Firebase>     # @ApiBearerAuth("JWT-Firebase-Auth")
mibot_session: <session>                  # required — resuelve PROJECT_UID (tenant)
Content-Type: application/json
```

### Body (contrato completo)
```jsonc
{
  "channel_id": "ch_abc123",     // canal de envío (número provisionado). REQUERIDO
  "to": "56912345678",           // destino con código de país. REQUERIDO
  "type": "text|file|template",  // opcional — se infiere si se omite (ver abajo)

  // type=text (solo dentro de 24h):
  "text": "Hola, tu deuda...",

  // type=template (PROACTIVO / fuera de 24h):
  "template_id": 42,             // id de plantilla pre-cargada en ChatHub
  "variables": { "1": "Juan", "2": "S/ 1200" },  // rellena {{1}}, {{2}}
  "contact_name": "Juan Perez",  // opcional

  // type=file:
  "file_url": "https://.../voucher.pdf",
  "file_name": "voucher.pdf",
  "caption": "Tu comprobante"
}
```

### Inferencia de `type` (si lo omitís)
`unified-message.service.ts:send()`:
```
type = dto.type
     ?? (template_id ? "template"
        : file_url   ? "file"
        : "text")
```

### Respuesta
```json
{ "message": "Mensaje enviado exitosamente",
  "result": { "success": true, "sid": "<provider-msg-id>", "type": "text|template" } }
```

---

## 2. ENVÍO PROACTIVO — la regla de oro

**Fuera de la ventana de 24h de WhatsApp NO podés mandar texto libre.** Meta lo bloquea.
El proactivo SIEMPRE va por plantilla pre-aprobada:

```jsonc
POST /messages/send
{
  "channel_id": "ch_abc123",
  "to": "51999888777",
  "type": "template",
  "template_id": 42,
  "variables": { "1": "Juan", "2": "S/ 1200", "3": "05/06" }
}
```

- La plantilla (`MessageTemplate`) vive en el schema del proyecto en ChatHub (`project_<uid>`).
- ChatHub sustituye `{{key}}` localmente (`text.replaceAll`) y delega al proveedor del canal.
- La plantilla declara `messaging_providers` (csv). Si el canal usa un proveedor no soportado por la plantilla → error `no es compatible con la plataforma`.
- Para Meta Cloud API la plantilla además debe estar **aprobada en Meta** — eso se gestiona del lado de ChatHub/proveedor, no en tu payload.

### Descubrir qué mandar (no adivines IDs)
```
GET /messages/channels                 → [{ id, nombre, numero, plataforma }]
GET /messages/templates?platform=cloud-api → lista de plantillas del tenant
GET /messages/templates/:id            → detalle + variables + EJEMPLO de request
```

---

## 3. ENVÍO PROACTIVO MASIVO — `POST /messages/campaign`

DTO: `create-campaign.dto.ts` (`CreateCampaignDto`). Úsalo para discador / blasts.

```jsonc
{
  "name": "Cobranza junio",
  "channel_id": "ch_abc123",
  "template_id": 42,
  "contacts": [
    { "phone": "51999888777", "name": "Juan",
      "variables": { "1": "Juan", "2": "S/ 1200" } },
    { "phone": "51988777666", "name": "Ana",
      "variables": { "1": "Ana", "2": "S/ 800" } }
  ],
  "auto_start": true,        // arranca sola; si false → POST /notificationCampaign/start/:id
  "deduplicate": true,       // dedup por teléfono (¡úsalo!)
  "webhook": {               // estados en tiempo real a TU sistema
    "url": "https://mi-sistema.com/webhook",
    "auth_type": "bearer", "token": "...",
    "events": ["notification_message_sent",
               "notification_message_failed",
               "notification_campaign_finished"]
  }
}
```

- **Rate limiting por proveedor** lo aplica ChatHub (tabla `send_rate_config`, ajustable con `PUT /sendRateConfig/:platform`). Delays por defecto: Gupshup 500ms, WPP-OnBotGo 5000ms, Evolution 3000ms, Twilio/Cloud/Olimpo 0ms.
- El **webhook** es cómo tu sistema externo sabe si llegó/falló cada mensaje (no es síncrono).

---

## 3.5 Autenticación machine-to-machine (servicio externo)

Fuente: `apps/backend-whatsapp/src/middlewares/authentication-middleware.ts`.

El endpoint valida 3 cosas:
1. `Authorization: Bearer <JWT>` — JWT de Firebase verificado con `verifyIdToken()`.
2. Header `mibot_session: {"projectUid":"...","client_uid":"..."}` — resuelve el tenant (`PROJECT_UID`).
3. El email del token debe existir como usuario en `project_<uid>.user` (`findByEmail`). Si no → rechaza.
4. Header opcional `firebase-project-id` (default `valhalla`).
5. Bypass: si la instancia corre con `DISABLE_AUTH=true` se saltea todo (no usar en prod).

**Patrón M2M (el mismo que ChatHub usa internamente, ver `conversation-handler.service.ts`):**
```
1. Login del usuario de servicio contra Firebase Auth REST:
   POST https://identitytoolkit.googleapis.com/identitytoolkit/v3/relyingparty/verifyPassword?key=<FIREBASE_API_KEY>
   { "email": "<svc-email>", "password": "<svc-pass>" }
   → idToken (válido ~1h, refrescar con el refreshToken)

2. POST {backend-api}/messages/send
   Authorization: Bearer <idToken>
   mibot_session: {"projectUid":"<tenant>","client_uid":"<...>"}
   { "channel_id":"...", "to":"51999...", "type":"template",
     "template_id": 42, "variables": {"1":"Juan","2":"S/ 1200"} }
```

**Prerrequisito de provisioning:** el servicio externo necesita un USUARIO de servicio dado de alta en el proyecto (email en `project_<uid>.user`) con credenciales Firebase. No es código — es alta de cuenta.

## 4. Proveedores soportados (abstraídos)

| Proveedor | id (`platform`) | delay def. | límite diario |
|---|---|---|---|
| Twilio | `twilio` | 0ms | 1000 |
| Meta Cloud API | `cloud-api` | 0ms | 1000 |
| Gupshup | `gupshup` | 500ms | — |
| WPP OnBotGo | `wpp-onbotgo` | 5000ms | — |
| Olimpo | `olimpo` | 0ms | — |
| Evolution API v2 | `evolution-api` | 3000ms | — |

Cada uno implementa `MessagingClient`: `sendText`, `sendFile`, `sendTemplate`, `sendInteractive`, `sendConversationMessage`. Tu sistema **no elige proveedor**: lo determina el `channel_id`.

---

## 5. Qué necesita tu sistema externo para arrancar

1. **Base URL** del backend-whatsapp de ChatHub (config deploy — `APP_PORT`, Swagger en `/api`). ⚠️ No hardcodeada en el repo; pedirla a infra/ChatHub.
2. **JWT Firebase** válido para `Authorization: Bearer`.
3. **`mibot_session`** del tenant (resuelve `PROJECT_UID`).
4. **`channel_id`** del número provisionado (vía `GET /messages/channels`).
5. **`template_id`** + nombres de variables (vía `GET /messages/templates`), con la plantilla **pre-cargada y aprobada**.

Con eso, proactivo = un `POST /messages/send` con `type:"template"`. Masivo = `POST /messages/campaign`.

---

## 6. Diferencia vs lo que hace el chatbot-cobranza hoy

El chatbot **solo** implementa el camino de sesión (texto plano `{to, message, channelId}` contra una env var hoy vacía → simula). **No** usa `template_id`, **no** usa `/messages/campaign`. O sea: el "método del chatbot" NO te sirve para proactivo. Para proactivo, tu sistema externo usa el contrato de plantillas de arriba, directo contra ChatHub.
