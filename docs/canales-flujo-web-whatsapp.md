# Flujo de canales — Web (landing) vs WhatsApp (chathub)

> Principio: **un cerebro, N adaptadores de canal.** El agente (`core/agent.py`: LLM + tools + gate de identidad) es idéntico para ambos canales. Cada canal es solo un adaptador de entrada/salida que resuelve identidad, conversación y tenant a su manera, y traduce la respuesta del agente al formato del canal.

```
  Web (landing/widget) ──► POST /api/v1/chat ──────────┐
                            (WebAdapter)                │
                                                        ▼
                                            ┌───────────────────────┐
                                            │   AGENTE COBRANZA      │
                                            │  LLM provider          │
                                            │  ToolRegistry + GATE   │  ← idéntico para ambos
                                            │  ConversationState     │
                                            └───────────────────────┘
                                                        ▲
                            (ChathubChatAdapter)        │
  WhatsApp ──► chathub ──► POST /<botPath>/chat ────────┘
              (gateway)
```

---

## Flujo WEB (landing / widget)

```
1. Usuario abre landing: portal.../?ct=<token>   (o sin token)
2. Widget → handshake: obtiene session + CSRF
3. Widget lee ?ct= de la URL
4. Usuario escribe → POST /api/v1/chat
   { channel:"web", tenant_id, text, conversation_id, campaign_token, page_context, CSRF }
5. WebAdapter:
   a. valida CSRF + session
   b. tenant   ← tenant_id del request (explícito)
   c. conversación ← conversation_id (uuid del widget)
   d. identidad ← campaign_token (?ct=) → resolver token → debtor profile
                  setea identity_verified + debt_context
                  sin token → COLD
6. AGENTE procesa (gate duro: tools de deuda solo si identity_verified)
7. Respuesta JSON sync → { reply, quick_replies, ui_actions }
8. Widget renderiza burbuja + chips + links (certificado, etc.)
```

## Flujo WHATSAPP (vía chathub)

```
1. Deudor abre WhatsApp: wa.me/<num>?text=CT-<token>  (campaña)  o escribe al número
2. WhatsApp → chathub (recibe por Evolution/Gupshup/Cloud) → webhook → RabbitMQ → messages app
3. chathub: número de canal → projectUid (tenant) ; manager incluye "bot" → handleBot
4. Debounce Redis: junta globos (~2s, last-wins) → message multi-línea
5. chathub → POST /<botPath>/chat
   { channel_id, message, unique_id, chathub_conversation_id, chathub_project_id, platform:"chathub" }
   (NO trae phone ni nombre)
6. ChathubChatAdapter:
   a. auth  ← (HOY ninguna — TODO: token compartido)
   b. tenant   ← channel_id → tenant  (NO confiar en chathub_project_id: bug latente)
   c. conversación ← chathub_conversation_id  (NO parsear unique_id)
   d. identidad ← token CT- en el message (primer turno) → resolver → debtor profile
                  ya verificado en la conversación → saltear
                  sin token y no verificado → COLD (pedir enlace o derivar)
   e. normalizar multi-línea (debounce) antes de pasar al agente
7. AGENTE procesa (mismo gate duro)
8. ChathubChatAdapter traduce la salida al contrato chathub (respuesta SYNC en el body):
   - texto normal      → { type:"text", response:<markdown> }
   - con botones/listas → { type:"interactive", response, content:<InteractiveMessage> }
   - derivar a asesor   → { type:"redirect", response:<despedida>,
                            content:{ receiver:{ type:"agent"|"group", identifier } } }
9. chathub manda la respuesta al deudor por WhatsApp
```

---

## Diferencias entre canales

| Aspecto | Web (landing) | WhatsApp (chathub) |
|---|---|---|
| Entrada | `POST /api/v1/chat` (widget) | `POST /<botPath>/chat` (chathub) |
| Auth | CSRF + session | **ninguna hoy** → TODO token |
| Identidad | `?ct=` query param | `CT-` en primer mensaje |
| Conversación | `conversation_id` (widget) | `chathub_conversation_id` |
| Tenant | `tenant_id` en el request | `channel_id` → tenant |
| Respuesta | JSON `{reply, quick_replies}` | body sync: `text`/`interactive`/`redirect` |
| Quick replies | chips web | botones nativos WhatsApp (`interactive`) |
| Derivar a asesor | mensaje + registro (no hay agente en vivo) | `redirect` real → asesor/cola en chathub |
| Iniciar contacto (proactivo) | no aplica | `/messages/send` + templates HSM (fuera de 24h) |
| Multi-línea | no | sí (debounce junta globos) |

---

## Lo común (no cambia entre canales)

- **Gate de identidad (duro, ToolRegistry)**: ninguna tool de deuda se ejecuta sin `identity_verified`. Igual en web y WhatsApp. Sin identidad → estado COLD.
- **El agente** (LLM, prompt, rúbrica, tools de cobranza) es el mismo objeto.
- **`escalate_to_human` = señal neutra**: el agente invoca la MISMA tool; cada adaptador la materializa según el canal:
  - WhatsApp → `redirect` a chathub (asesor por email o cola/grupo, con despedida)
  - Web → mensaje "te contactará un asesor" + registro de la solicitud (en la demo no hay agente en vivo)
  - El agente expone una señal neutra `{handoff: true, target?, farewell}`; el adaptador traduce.

---

## Puntos a decidir (antes de implementar)

1. **Resolución de tenant en WhatsApp**: como `chathub_project_id` viene mal (bug chathub), el adaptador mapea `channel_id → tenant` con una tabla de config en el bot. ¿OK, o preferimos arreglar chathub para que mande el `projectUid` real?
2. **Identidad por token vs por teléfono**: arrancamos con token `CT-` (no toca chathub). Para campañas proactivas el teléfono ya lo tenemos al disparar el template → el deudor queda identificado desde el envío. ¿Suficiente, o queremos también resolver por teléfono (requiere que chathub propague `phone`)?
3. **Auth chathub→bot**: cerrar el gap con un token compartido antes de producción. ¿Lo metemos desde el arranque del adaptador o lo dejamos para el hardening pre-prod?
4. **Coexistencia con Olimpo**: si Olimpo sigue vivo para otros bots, sumar el mapa `botPath→url` en chathub (cambio chico). Si cobranza es el único motor, basta apuntar `OLIMPO_URL`.
