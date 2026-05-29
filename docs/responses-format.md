# Formato del `responses.json` — guión curado por cliente

Este documento es el **contrato** para que un cliente entregue su guión de respuestas.
El motor de respuestas (`apps/agent/core/responses.py`) es **tenant-agnóstico**: no
hardcodea ningún cliente. Cada tenant activa la feature con su propio
`responses.json` + el flag `response_mode` en `tenant.config.json`. Un tenant sin
`responses.json` cae a modo `llm` (comportamiento actual del agente) y nada se rompe.

Objetivo del formato: el cliente **dicta el copy exacto** que ve el usuario
(compliance + marca) y el backend rellena las variables con los datos reales del
crédito verificado (cero alucinación). Los intents obvios se resuelven con un router
de keywords **sin llamar al LLM** (ahorro de costo).

---

## 1. Los tres `response_mode`

Se define por tenant en `tenant.config.json` → `"response_mode"`.

| Modo | Comportamiento |
|------|----------------|
| `llm` | El agente genera todo con el LLM. Default y 100% retrocompatible. No requiere `responses.json`. |
| `scripted` | SOLO canned + router de keywords. Sin clasificación LLM. Si nada matchea → canned `no_entendido` (LLM mínimo). |
| `hybrid` | Router de keywords (capa 1, gratis) → canned. Si falla, el LLM clasifica el intent (capa 2) → canned. Solo si no hay canned para el caso, el LLM genera libre. |

`prestamype` usa `hybrid`.

---

## 2. Estructura: objeto top-level keyed por intent

```json
{
  "_meta": { "...": "claves con prefijo _ se ignoran (metadata libre)" },
  "consulta_deuda": { "...config del intent..." },
  "saludo": { "...config del intent..." }
}
```

Las claves que empiezan con `_` (ej. `_meta`, `_response_mode`) **no son intents**;
se ignoran al cargar. `_response_mode` dentro del archivo es un override opcional,
pero el flag de `tenant.config.json` gana si está presente.

### Campos por intent

| Campo | Tipo | Para qué sirve |
|-------|------|----------------|
| `mode` | `"verbatim"` \| `"variant"` | `verbatim`: texto exacto dictado. `variant`: array, se elige uno al azar sin repetir el anterior. Default: `verbatim`. |
| `description` | string | Catálogo que usa el clasificador LLM en **capa 2**. Si falta, usa el nombre del intent. |
| `requires_identity` | bool | Gate por DNI. Si `true` y el usuario no está verificado → el motor responde con el intent `identidad_requerida` en lugar del contenido. |
| `keywords` | string[] | **Capa 1** (sin LLM): substrings case-insensitive. El match más largo gana. |
| `patterns` | string[] | **Capa 1**: regex (case-insensitive). Gana por largo del span matcheado. |
| `tool` | string | Tool del registry que el agente ejecuta **antes** de responder (ej. `consultar_deuda`). Data-driven: el JSON lo nombra, el motor lo ejecuta. |
| `template` | string \| objeto | Plantilla **single** (ver §3). |
| `list` | objeto | Plantilla **list** multi-crédito (ver §3). |
| `grupal` | objeto | Bloque opcional para créditos grupales (ver §4). |
| `variants` | array | Solo `mode: variant`. Lista de plantillas (cada una single str/dict o list dict). |
| `chips` | string[] | **Quick-replies contextuales** que se ofrecen DESPUÉS de la respuesta de este intent. Data-driven, **el LLM NO los inventa** (ver §2.1). |

---

## 2.1. Chips / quick-replies (data-driven, CORE)

Los chips (quick-replies del widget) los declara el tenant en su `responses.json`,
**no los genera el LLM**. Cuando un tenant declara chips, el backend los resuelve
y la salida del LLM (`suggest_quick_replies`) se **ignora por completo** → cero
alucinación (esto cura el bug histórico del chip "Ver proyectos", residuo del
engine inmobiliario). Un tenant **sin** chips conserva el comportamiento legacy
(chips del LLM / heurística) y no se rompe nada.

Dos niveles, con precedencia:

1. **Por intent** (`chips` dentro de un intent): chips contextuales tras la
   respuesta de ESE intent. Ej. `consulta_deuda` → ofrecer subir comprobante,
   datos de pago, asesor.
2. **Por estado** (bloque reservado `_chips`): el default para el saludo / turnos
   sin intent claro. Dos estados:
   - `cold` — usuario **sin identificar**.
   - `identified` — usuario con **DNI verificado**.

El backend usa los chips del intent resuelto si existen; si no, cae a los del
estado actual. Máximo 4 chips (se truncan). Si el tenant declara chips pero no
hay chips para el turno → se muestran **cero** chips (no se cae al LLM).

```json
{
  "_chips": {
    "cold": ["Consultar mi deuda", "Subir comprobante"],
    "identified": ["Ver mi deuda", "Subir comprobante", "Datos de pago"]
  },
  "consulta_deuda": {
    "chips": ["Subir comprobante", "Datos de pago", "Hablar con un asesor"],
    "template": "..."
  }
}
```

Regla de oro: los chips deben ser **acotados a las capacidades reales del bot**.
Nunca chips fuera de dominio.

---

## 3. Plantillas: `single` vs `list`

### Single (`template`)

Una sola cadena con tokens `{var}`:

```json
"template": "Tu crédito {loan} tiene un saldo de {saldo}. La próxima cuota es de {cuota} y vence el {fecha_venc}. Estado: {estado}."
```

### List (`list`) — multi-deuda

Cuando el usuario tiene más de un crédito, el motor itera todos los créditos
(principal + `additional_credits`) y repite el `item`:

```json
"list": {
  "header": "Tienes {n_creditos} créditos vigentes a tu nombre:",
  "item": "• Crédito {loan}: saldo {saldo}, cuota {cuota} (vence {fecha_venc}) — {estado}.",
  "footer": "Saldo total entre tus créditos: {total}. ¿Sobre cuál quieres avanzar?"
}
```

Reglas de selección (modo `verbatim`):
- Si hay `list` **y** el usuario tiene >1 crédito → usa `list`.
- Si hay `template` → usa `template` (single).
- Si solo hay `list` → usa `list` aunque haya 1 crédito.

Variables extra disponibles en `list`:
- `header`/`footer`: `{n_creditos}` (cantidad de créditos), `{total}` (suma de saldos, solo en `footer`).
- `item`: `{n}` (índice 1-based) + todas las variables del crédito en curso.

---

## 4. Bloque `grupal` (codeudores)

Se **anexa** al texto del intent cuando el perfil trae `is_grupal: true` y
`codeudores`. Soporta las mismas formas single/list. En `list`, el `item` se repite
por codeudor:

```json
"grupal": {
  "header": "Este es un crédito grupal; lo comparten:",
  "item": "• {codeudor} ({rol}) — DNI {dni}.",
  "footer": "El saldo y las cuotas son compartidos por el grupo."
}
```

Variables del bloque grupal:
- `header`: `{n_codeudores}`.
- `item`: `{codeudor}` (nombre), `{rol}`, `{dni}` (enmascarado, ej. `44****3`).
- forma `template` (single): `{codeudores}` (lista de nombres separados por coma).

---

## 5. Variables disponibles

El backend las rellena desde el crédito verificado. Tokens no reconocidos se dejan
tal cual (superficie de bug visible en el guión, no crashea).

### Top-level (single templates) — desde el perfil / crédito principal

| Token | Valor |
|-------|-------|
| `{nombre}` | Primer nombre, capitalizado |
| `{nombre_completo}` | Nombre completo capitalizado |
| `{saldo}` | Saldo formateado (`S/ 1,234.56`) |
| `{moneda}` | Símbolo de moneda (`S/`) |
| `{fecha_venc}` | Próxima fecha de vencimiento |
| `{cuota}` | Monto de la próxima cuota, formateado |
| `{loan}` | Número de préstamo / account_id |
| `{dias_mora}` | Días de mora |
| `{estado}` | Etiqueta de estado del crédito |
| `{cci}` | CCI para pago |
| `{banco}` | Banco |

### Por-crédito (en `item` de `list`)

Además de las top-level, el `item` recibe las del crédito en curso: `{loan}`,
`{account_id}`, `{moneda}`, `{saldo}`, `{cuota}`, `{fecha_venc}`, `{dias_mora}`,
`{estado}`, `{cci}`, `{banco}`, más `{n}` (índice).

### Grupal

`{codeudor}`, `{rol}`, `{dni}` (enmascarado), `{n_codeudores}`, `{codeudores}`.

---

## 6. Router de dos capas (cómo se resuelve un turno)

```
Capa 1 (gratis, sin LLM)  →  match_keyword_intent: keywords + patterns del JSON
                             hit → gate de identidad → render canned → ejecuta tool
                             tag analytics: canned_keyword

Capa 1 miss:
  - scripted → render canned no_entendido (LLM mínimo)
  - hybrid   → el LLM clasifica el intent contra {intent: description}  (Capa 2)
               → render canned (tag: canned_intent)
               → si no hay canned para el caso → LLM genera libre (tag: llm)
```

El catálogo del clasificador de capa 2 se arma dinámicamente desde los `description`
del JSON — **nunca hardcodeado**. El gate `requires_identity` aplica en ambas capas.

---

## 7. Ejemplo completo (extracto real de prestamype)

```json
{
  "_meta": {
    "tenant": "prestamype",
    "doc": "Guión curado. Editar intents = editar este JSON, sin tocar código."
  },

  "saludo": {
    "mode": "variant",
    "description": "El usuario saluda o inicia la conversación sin pedir nada concreto.",
    "requires_identity": false,
    "keywords": ["hola", "buenas", "buenos dias", "buenas tardes"],
    "patterns": ["^\\s*(hola|buen[oa]s|hey)\\b"],
    "variants": [
      "Hola, soy Ada de PrestamYpe. ¿Qué necesitas?",
      "Hola, qué gusto. Soy Ada de PrestamYpe. ¿En qué empezamos?"
    ]
  },

  "identidad_requerida": {
    "mode": "verbatim",
    "description": "Se pidió información de la cuenta sin haber verificado identidad (DNI).",
    "requires_identity": false,
    "template": "Para mostrarte los datos de tu cuenta necesito identificarte primero. Por favor, indícame tu número de DNI (8 dígitos)."
  },

  "consulta_deuda": {
    "mode": "verbatim",
    "description": "El usuario pregunta por su deuda, saldo, cuota, vencimiento o estado de su préstamo.",
    "requires_identity": true,
    "tool": "consultar_deuda",
    "keywords": ["mi deuda", "cuanto debo", "saldo", "mi cuota", "mi credito"],
    "patterns": ["cu[aá]nt[oa].*(debo|pago|deuda|cuota)"],
    "template": "Tu crédito {loan} tiene un saldo de {saldo}. La próxima cuota es de {cuota} y vence el {fecha_venc}. Estado: {estado}.",
    "list": {
      "header": "Tienes {n_creditos} créditos vigentes a tu nombre:",
      "item": "• Crédito {loan}: saldo {saldo}, cuota {cuota} (vence {fecha_venc}) — {estado}.",
      "footer": "Saldo total entre tus créditos: {total}. ¿Sobre cuál quieres avanzar?"
    },
    "grupal": {
      "header": "Este es un crédito grupal; lo comparten:",
      "item": "• {codeudor} ({rol}) — DNI {dni}.",
      "footer": "El saldo y las cuotas son compartidos por el grupo."
    }
  }
}
```

Para activarlo: en `tenant.config.json` agregar `"response_mode": "hybrid"`.
