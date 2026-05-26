# DEMO — PrestaUnion (cobranza MYPE)

> Demostración funcional, **marca blanca PrestaUnion** (fintech ficticia de préstamos a MYPEs).
> Datos 100% ficticios. Sin integración real, sin base de datos. Theming: Onbotgo Vox (dark + gold).

## Qué muestra

Una asistente de cobranzas llamada **Ada** que resuelve tres casos de uso, con un **gate de identidad duro**: sin ingresar por su enlace, el usuario NO ve datos de ninguna cuenta.

1. **Consulta de deuda** — saldo, cuotas pagadas/pendientes, próximo vencimiento, estado (al día / en mora), recargo por mora y TCEA.
2. **Reclamos (Libro de Reclamaciones, Indecopi)** — registra reclamo/queja, devuelve folio `LR-2026-NNNNN` y plazo de 15 días hábiles.
3. **Certificado de no adeudo** — si el saldo es cero, emite un PDF descargable con folio, fecha, nombre y razón social; si hay deuda, explica que no procede.

---

## Cómo levantar la demo

### 1. Dependencias

```bash
cd /home/ricardo/projects/chatbot-cobranza
uv sync
```

### 2. API key de Anthropic (necesaria para la conversación en vivo)

La demo conversacional usa Claude Haiku 4.5. **Exportá tu key antes de levantar el server:**

```bash
export COBRANZA_ANTHROPIC_API_KEY="sk-ant-..."   # o ANTHROPIC_API_KEY
```

> Sin la key, el server arranca igual y el frontend se ve, pero el chat responde con un
> mensaje de fallback (no llega a Claude). Las 3 herramientas y el certificado PDF funcionan
> de todos modos (probados con tests).

### 3. Levantar el server (sirve backend + frontend en el mismo puerto)

```bash
COBRANZA_CSRF_SECRET=demo \
PYTHONPATH=apps/agent \
uv run uvicorn api.main:app --host 0.0.0.0 --port 8099
```

El frontend se monta como estático dentro del backend (mismo origen → sin fricción de CORS).

### 4. Abrir la demo

```
http://localhost:8099/
```

---

## Los 3 enlaces de acceso (para mostrar en vivo)

Desde el portal hay un botón por escenario; o entrá directo por URL:

| Escenario | Enlace | Estado |
|---|---|---|
| **Juan — al día** | `http://localhost:8099/chat.html?ct=demo-juan` | Bodega Don Juan E.I.R.L. · saldo S/ 4,850 · 3 cuotas |
| **Carlos — en mora** | `http://localhost:8099/chat.html?ct=demo-carlos` | Ferretería El Tornillo S.A.C. · saldo S/ 2,300 · vencido 8 días |
| **María — sin deuda** | `http://localhost:8099/chat.html?ct=demo-maria` | Textiles María E.I.R.L. · saldo S/ 0 · cancelado |

El parámetro `?ct=` es el **token de campaña** (identidad). El portal lo pasa al widget y el
backend lo resuelve server-side a un perfil verificado. El `account_id` **nunca** lo dicta el
usuario ni el LLM.

---

## Guion de demo (qué decir/mostrar en cada escenario)

**Apertura (portal `/`):** "Este es el portal de clientes de PrestaUnion. Cada cliente recibe un
enlace seguro de campaña; al entrar, ya queda identificado sin tipear ningún dato sensible."

### Juan (al día) — `?ct=demo-juan`
1. Escribir / clickear **"¿Cuánto debo?"** → Ada llama `consultar_deuda` y reporta saldo S/ 4,850,
   3 de 12 cuotas pendientes, próxima cuota S/ 1,650 el 15/06/2026, estado **al día**.
2. Punto a destacar: *los montos salen de la herramienta, Ada no inventa nada.*

### Carlos (en mora) — `?ct=demo-carlos`
1. **"¿Cuál es mi saldo?"** → saldo S/ 2,300, **1 cuota vencida hace 8 días**, recargo por mora S/ 85.
2. Tono de **acompañamiento, nunca presión** (regla regulatoria; ver guardrails).
3. **"Quiero poner un reclamo"** → Ada pide tipo + descripción → registra y devuelve
   **folio `LR-2026-00001`** + plazo de **15 días hábiles**. (Se ve en `GET /api/v1/cobranza/reclamos`.)

### María (sin deuda) — `?ct=demo-maria`
1. **"¿Tengo deuda pendiente?"** → saldo S/ 0, préstamo cancelado.
2. **"Quiero mi certificado de no adeudo"** → Ada emite el **PDF** y entrega un enlace de descarga
   (folio `CNA-2026-NNNNN`, con nombre, razón social y nº de préstamo).
3. Contraste: si pedís el certificado como Juan o Carlos → Ada explica que **no procede** hasta cancelar.

### Gate de identidad (opcional, impactante)
- Abrir el chat **sin** `?ct=` (`http://localhost:8099/chat.html`) → tarjeta "Sesión no identificada".
- Preguntar "¿cuánto debo?" → Ada **no revela nada** y pide ingresar por el enlace seguro.
  El bloqueo es **duro** (ToolRegistry), no depende del prompt.

---

## Verificación (evidencia)

| Check | Resultado |
|---|---|
| `uv sync` | OK (reportlab agregado para el PDF) |
| Server arranca | OK (`Application startup complete`, frontend montado) |
| Resolver token → perfil | OK (tests: juan/carlos/maria correctos, token desconocido → None) |
| 3 tools con perfil mockeado | OK (deuda por perfil, reclamo genera folio, certificado solo María) |
| Certificado PDF | OK (genera `%PDF`, descargable vía `GET /api/v1/cobranza/certificate/{file}`) |
| Gate sin identidad | OK (tools devuelven `{"blocked":"identity_required"}`; E2E HTTP probado) |
| `tools_schema` sin `account_id` | OK (verificado: 0 leaks de account/borrower en los schemas) |
| `uv run pytest -v` | **20 passed** |

```bash
uv run pytest -v      # 20 passed
```

---

## Qué necesita el dueño para levantarla

- **API key**: `export COBRANZA_ANTHROPIC_API_KEY=sk-ant-...` (sin ella, el chat no llega a Claude).
- **Puerto**: 8099 (ajustable con `--port`).
- **URL**: `http://localhost:8099/` (portal) — backend y frontend van juntos.
- **Branding**: theming Vox (dark + gold + Inter), wordmark de texto "PrestaUnion".
  TODO opcional: reemplazar el wordmark por un logo si el cliente provee uno.

## Notas técnicas

- Reclamos persisten en `/tmp/prestaunion_reclamos.json`; certificados en `/tmp/prestaunion_certificates/`.
- Perfiles ficticios en `tenants/prestaunion/mock/borrowers.json`.
- Identidad, knowledge y guardrails del tenant en `tenants/prestaunion/`.
- Para resetear la conversación, recargá el chat (cada sesión usa un `conversation_id` nuevo en memoria).
