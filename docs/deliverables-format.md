# Formato de `_deliverables` — envío de información bajo demanda

Esta es la feature **CORE** que permite que el asistente (Ada) **envíe información al
cliente bajo demanda**, por **correo o WhatsApp**, según lo que el cliente elija.
Es **tenant-agnóstica** y **data-driven**, igual que el motor de respuestas: los
**tipos de información enviables** y su **copy** se declaran en el `responses.json`
del tenant bajo la clave reservada `_deliverables`. **Cero hardcode** del cliente en
el motor.

> Motor: `apps/agent/tools/cobranza.py:enviar_info` + `apps/agent/core/responses.py`.
> El tool del registry es `enviar_info(tipo, canal)`.

---

## 1. Idea general

1. El cliente pide algo enviable (o Ada lo ofrece tras consultar deuda / datos de
   pago / validar comprobante).
2. Ada **pregunta el canal**: "¿Te lo envío a tu correo o por WhatsApp?".
3. El cliente responde `correo` | `whatsapp` → se guarda en el estado de sesión.
4. Se ejecuta el envío **al correo/teléfono REGISTRADO del cliente** (del perfil
   verificado, **nunca uno que el usuario tipee**).
5. Ada confirma con el **destino enmascarado** (`c···@···.com`, `···7890`).

**Requiere identidad** (gate por DNI): los intents de envío llevan
`requires_identity: true`.

---

## 2. Modo demo (simulado) vs producción (real)

El motor decide por el `data_source` del tenant (`tenant.config.json`):

| `data_source` | `delivery_mode` | Comportamiento |
|---------------|-----------------|----------------|
| `mock` (demo) | `simulate` | **NO** llama a SendGrid/ChatHub. Ada confirma con el destino enmascarado. Se loguea como simulado. |
| `doris` (prod) | `real` | Envío real: **correo** vía SendGrid (`apiintranet.mibot.cl:8085`); **WhatsApp** vía ChatHub outbound. |

> El destino siempre sale del **perfil verificado** y se muestra **enmascarado**,
> tanto en demo como en prod.

### WhatsApp real (ChatHub outbound)

WhatsApp dejó de ir por Evolution. El envío real va por **ChatHub** (`/messages/send`)
a través del adaptador `apps/agent/integrations/chathub_outbound.py`
(`ChathubOutboundClient`), configurable por entorno:

| Env | Para qué |
|-----|----------|
| `COBRANZA_CHATHUB_OUTBOUND_URL` | URL de `/messages/send`. **Vacío ⇒ SIMULA.** |
| `COBRANZA_CHATHUB_OUTBOUND_TOKEN` | Token Firebase/bearer (si aplica). |
| `COBRANZA_CHATHUB_OUTBOUND_CHANNEL_ID` | Canal ChatHub del número del tenant. |

Mientras ChatHub **no** tenga el número provisionado + auth, aunque el tenant esté en
prod el WhatsApp se **simula** de forma honesta (`channel_status: "chathub_pending"`).
Se activa el envío real al setear la URL + auth y tener el número provisionado.

---

## 3. Estructura del bloque `_deliverables`

Objeto top-level en el `responses.json` del tenant, keyed por **tipo**:

```json
"_deliverables": {
  "estado_cuenta": {
    "label": "estado de cuenta",
    "correo":   { "subject": "...", "body": <plantilla> },
    "whatsapp": { "text": <plantilla> }
  },
  "datos_pago":  { "...": "..." },
  "constancia_comprobante": { "...": "..." }
}
```

| Campo | Para qué |
|-------|----------|
| `label` | Texto humano del tipo (ej. "estado de cuenta"). Aparece en la confirmación de Ada. |
| `correo.subject` | Asunto del correo (plantilla single con `{variables}`). |
| `correo.body` | Cuerpo del correo (plantilla **single** o **list**, ver §4). |
| `whatsapp.text` | Texto del mensaje de WhatsApp (plantilla single o list). |

Las claves con prefijo `_` (ej. `_doc`) se ignoran.

---

## 4. Plantillas (single / list) — reusan el motor de respuestas

El `body`/`text` puede ser:

- **single**: una cadena con tokens `{var}`, o `{ "template": "..." }`.
- **list** (multi-deuda): `{ "header", "item", "footer" }` — el motor itera todos
  los créditos del cliente (`principal + additional_credits`) y repite `item`.

```json
"body": {
  "header": "Hola {nombre}, este es el estado de tus créditos:",
  "item": "Crédito {loan}: saldo {saldo}, cuota {cuota} (vence {fecha_venc}) — {estado}.",
  "footer": "Saldo total: {total}."
}
```

Variables disponibles (rellenadas desde el crédito verificado): `{nombre}`,
`{nombre_completo}`, `{saldo}`, `{moneda}`, `{fecha_venc}`, `{cuota}`, `{loan}`,
`{dias_mora}`, `{estado}`, `{cci}`, `{banco}`. En `list`: además `{n}` (índice),
y en `footer` `{n_creditos}` + `{total}`. (Mismo contrato que `docs/responses-format.md`.)

---

## 5. Los intents que activan el flujo (en el mismo `responses.json`)

El flujo conversacional es **también** data-driven. Se apoya en dos mecanismos
genéricos del motor:

- `set_session: { clave: valor }` — el intent guarda un valor en la sesión para un
  turno posterior. `valor` puede ser literal o `"{capture}"` (lo capturado del patrón).
- `needs_session: ["a", "b"]` — el intent lee esas claves de la sesión y las pasa como
  argumentos a su `tool`.
- `rerender_with_result: true` — el texto final lo arma el `tool` (trae el destino
  enmascarado / mensaje de error correcto), en vez del `template`.

### Intent que pide el envío (guarda el tipo, pregunta el canal)

```json
"enviar_estado": {
  "requires_identity": true,
  "keywords": ["envíame mi estado", "enviar estado de cuenta", "..."],
  "patterns": ["(env[ií]a|m[aá]nda|p[aá]sa).*(estado|deuda|saldo)"],
  "set_session": { "tipo": "estado_cuenta" },
  "template": "Claro, te puedo enviar tu estado de cuenta. ¿Te lo envío a tu correo o por WhatsApp?"
}
```

### Intent de elección de canal (corre el envío)

```json
"elegir_canal": {
  "requires_identity": true,
  "tool": "enviar_info",
  "capture": "canal",
  "patterns": ["\\b(?P<canal>correo|email|whats?app|wsp|wasap)\\b"],
  "set_session": { "canal": "{capture}" },
  "needs_session": ["tipo", "canal"],
  "rerender_with_result": true,
  "template": "Te envío la información al canal que elegiste."
}
```

El `tool` `enviar_info` recibe `tipo` (de la sesión, guardado por el intent anterior)
y `canal` (capturado este turno). El motor normaliza variantes de canal
(`email`→`correo`, `wsp`→`whatsapp`, …).

---

## 6. Cómo agrega un cliente sus envíos

1. Declarar los tipos en `_deliverables` (label + copy por canal).
2. Agregar los intents `enviar_<algo>` (con `set_session: {tipo: <clave>}`) y
   reutilizar `elegir_canal`.
3. Listo: sin tocar código. En demo se simula; en prod (data_source=doris) envía real.
