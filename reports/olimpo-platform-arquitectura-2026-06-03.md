# Olimpo — Plataforma de Agentes (visión arquitectónica)

> Estado: **VISIÓN / referencia**. No es ejecución inmediata. Fecha: 2026-06-03.
> Origen: la conversación sobre hacer el engine multi-tipo (cobranza / venta de créditos / inmobiliario).

## Tesis

El engine NO es "un bot de cobranza". Es un **engine de agentes conversacionales genérico** que nació inmobiliario (sorelia) y hoy corre cobranza. Olimpo es la **plataforma** que aloja N agentes de distinto tipo, un engine único, configurados por proyecto.

3 tipos de uso identificados:
1. **Cobranza** — deuda, comprobantes (productivo hoy).
2. **Venta de créditos** — simular, capturar y calificar solicitudes.
3. **Inmobiliario** — propiedades, financiamiento, visitas (≈ sorelia, recuperado limpio).

## Principio rector

**Un engine genérico + dominios como `features/` + `agent_type` por proyecto.**
No múltiples repos ni apps duplicadas. El `agent_type` en `tenant.config.json` decide qué se compone.

## Lo COMÚN (engine genérico) vs lo ESPECÍFICO (por tipo)

### Común — vive en `features/conversation/`, `shared/`, `tenancy/`
- Motor de diálogo (LLM loop, prompt building, compresión de historial).
- **Estado neutro de captura progresiva** (ver abajo).
- Identidad (resolver quién es: DNI / teléfono).
- Canales (`messaging/`: whatsapp, web/chathub).
- Persistencia (olimpo, schema por proyecto, user aislado).
- Framework de skills (cargar conocimiento por tipo).
- ToolRegistry (componer tools por tipo — el DI de PR8 ya lo habilita).
- Analytics (telemetría a Doris).

### Específico — vive en `features/<dominio>/`

| Capa | cobranza | creditos | inmobiliario |
|---|---|---|---|
| Estado | `debtor` (nivel deuda) | `prospect` (calificación) | `lead` (interés propiedad) |
| Tools | consultar_deuda, validar_comprobante, certificado, reclamo | simular_credito, capturar_solicitud, calificar | search_properties, simulate_mortgage, agendar_visita, brochure |
| Gate | hard DNI (no revelar deuda) | identidad para solicitud formal | abierto (info pública) |
| Data | Doris (deuda) | scoring / productos | catálogo inmobiliario |
| Skills | negociacion-cobranza, regulacion | venta-creditos, calificacion | propiedades, financiamiento |

## La observación clave: el patrón genérico de captura

Los 3 tipos comparten el MISMO funnel abstracto:

```
captar interlocutor → entender necesidad → calificar/mostrar → acción/compromiso
  cobranza:   identificar deudor → mostrar deuda → negociar → registrar compromiso
  creditos:   captar prospecto → calificar → simular → capturar solicitud
  inmobiliario: captar interesado → entender → mostrar propiedades → agendar visita
```

El `lead_machine` ORIGINAL de sorelia ya era esto: un modelo genérico de captura progresiva (`CONTACT_FIELDS` / `INTEREST_FIELDS` / `ENRICHMENT_FIELDS` + niveles). Lo especializamos a cobranza y lo renombramos a `DebtorState`.

**Para la plataforma, recuperamos esa generalidad — limpia:**
- `features/conversation/capture_state.py` — máquina de captura progresiva genérica, parametrizada por tipo (qué campos, qué niveles, qué acción).
- Cada feature la especializa: cobranza→`debtor`, creditos→`prospect`, inmobiliario→`lead`.

El rename `lead→debtor` de hoy **fue correcto** (cobranza-puro). Lo que faltaba era la capa neutra encima. Con 3 tipos, se diseña bien.

## Ubiquitous Language (nomenclatura de dominio) — DECIDIDO

Principio DDD: cada bounded context (tipo) tiene su lenguaje. NO se busca un nombre único; se busca un **concepto neutro mínimo** + proyecciones por contexto (composición, no herencia).

| Capa | Nombre | Qué es | Dónde |
|---|---|---|---|
| **Neutro** | **`Record`** | el registro/caso del interlocutor: identidad + datos de contacto + estado de captura | `conversation/` |
| Por contexto | `Debtor` | `Record` + deuda | `features/cobranza` |
| Por contexto | `Applicant` (o `Prospect`) | `Record` + scoring/solicitud | `features/creditos` |
| Por contexto | `Lead` | `Record` + interés/propiedad | `features/inmobiliario` |

`Record` = término de contact center (el caso/contacto a gestionar). Decidido por Ricky 2026-06-03.
Precaución de implementación: tiparlo explícito como entidad de dominio (`Record`), no confundir con "fila de BD" genérica.

El rename `lead→debtor` de hoy queda confirmado: `debtor` es el nombre en el contexto cobranza; `lead` se libera para inmobiliario/ventas. Solo falta poner `Record` (neutro) por encima cuando llegue el 2º tipo.

Persistencia (cierra la duda de nombres + tablas):
- Común: `records` (o `conversations` con el `Record` embebido) + `visitors` — sin el prefijo legacy `sorelia_`.
- Por tipo: `debtors` / `applicants` / `leads` — la crea `ensure_tables` según `agent_type`.

## El registro de `agent_type`

El corazón de la plataforma: un mapa
```
agent_type → { features, tools, skills, gate_model, state_spec }
```
El engine, dado el `agent_type` del proyecto, ensambla tools (ToolRegistry componible), carga skills, aplica el gate del dominio, instancia el state spec. Una sola pieza de composición.

## Mapeo a Olimpo (infra)

Cada agente = un proyecto = un schema en olimpo:
- `prestamype-cobranzas` → `project_QUId...` · user `olimpo_prestamype` (existe hoy)
- `prestamype-creditos`  → `project_<uid>` · user `olimpo_prestamype_creditos`
- `<cliente>-inmobiliario` → `project_<uid>` · user aislado
Patrón de aislamiento por schema ya establecido (ver engram `cobranza/olimpo-schema-isolation-pattern`).

## Camino de implementación (cuando se ejecute — NO ahora)

Incremental, guiado por casos reales. NO abstraer en vacío:
1. **Generalizar el estado** cuando aparezca el 2º tipo real (`prestamype-creditos`): extraer `capture_state` neutro de `DebtorState`.
2. **ToolRegistry por `agent_type`**: componer tools + gate según tipo (el DI de PR8 es el 80%).
3. **`features/creditos/`** con un caso real que guíe el diseño.
4. **`features/inmobiliario/`** recuperando el dominio de sorelia (git history como base), limpio y adaptado a préstamos/propiedades reales.
5. `agent_type` en tenancy + el registro de tipos.

## Qué NO hacer ahora

- No generalizar en abstracto sin un 2º tipo real en producción (over-engineering).
- El refactor de hoy (screaming + DI + olimpo + aislamiento) **ya dejó el terreno listo**. La base no necesita más hasta tener el caso concreto que valide el diseño de la capa neutra.
- Reposicionar `chatbot-cobranza` → `olimpo` (nombre de plataforma) es conceptual; hacerlo cuando exista el 2º tipo.
