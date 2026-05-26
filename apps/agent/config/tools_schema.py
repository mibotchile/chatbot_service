"""NEUTRAL tool definitions for the PrestaUnion cobranza agent (DEMO).

Provider-agnostic schema: each tool is {name, description, parameters} where
`parameters` is a JSON Schema object. Providers translate it (Anthropic →
`input_schema`, OpenAI → `function.parameters`). See core/llm/.

Generic engine tools (suggest_quick_replies, navigate_page, collect_contact_info,
get_lead_status) are kept. The cobranza domain has THREE tools:
  - consultar_deuda
  - registrar_reclamo
  - emitir_certificado_no_adeudo

SECURITY (design doc fix #3): NO schema exposes account_id / borrower_id. The
LLM cannot dictate which account it is talking about — the ToolRegistry injects
the verified identity server-side from the resolved campaign token. Tools that
operate on the account take only business params (or none).
"""

TOOL_DEFINITIONS = [
    # ── Generic engine tools (kept as-is) ───────────────────────────────
    {
        "name": "get_lead_status",
        "description": "Get current lead qualification level and missing data. Use to decide what to ask next.",
        "parameters": {
            "type": "object",
            "properties": {
                "conversation_id": {"type": "string", "description": "Current conversation ID"},
            },
            "required": ["conversation_id"],
        },
    },
    {
        "name": "navigate_page",
        "description": "Scroll to a section on the current page or highlight an element. Use instead of re-fetching data the user can already see.",
        "parameters": {
            "type": "object",
            "properties": {
                "scroll_to": {"type": "string", "description": "CSS selector to scroll to"},
                "highlight": {"type": "string", "description": "CSS selector to briefly highlight (optional)"},
            },
            "required": ["scroll_to"],
        },
    },
    {
        "name": "suggest_quick_replies",
        "description": "OBLIGATORIO: llama esta tool AL FINAL de cada respuesta para generar opciones de respuesta rapida, coherentes con lo que acabas de preguntar o proponer.",
        "parameters": {
            "type": "object",
            "properties": {
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 2,
                    "maxItems": 4,
                    "description": "2-4 opciones cortas (2-5 palabras) que el usuario puede clickear para responder.",
                },
            },
            "required": ["options"],
        },
    },
    {
        "name": "collect_contact_info",
        "description": "Show an inline contact form. Use ONLY in canal frio (sin identidad verificada) si el usuario quiere que lo contacte un asesor.",
        "parameters": {
            "type": "object",
            "properties": {
                "form_type": {"type": "string", "enum": ["contact"], "description": "contact = nombre + telefono"},
            },
            "required": ["form_type"],
        },
    },
    # ── Identidad (NO gateada): DNI-first ──
    {
        "name": "identificar_cliente",
        "description": (
            "Identifica al cliente por su número de DNI (8 dígitos) cuando NO ingresó por su enlace. "
            "Llama esta tool en cuanto el usuario te dé su DNI. Si el DNI es válido, queda identificado "
            "y se habilitan las consultas de su préstamo; si no, infórmale con amabilidad y NO reveles datos. "
            "El DNI es el que el usuario escribe; la cuenta se resuelve internamente, nunca la inventes."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "dni": {"type": "string", "description": "Número de DNI que el usuario escribió (8 dígitos)"},
            },
            "required": ["dni"],
        },
    },
    # ── Cobranza domain tools (NO account_id — identity injected server-side) ──
    {
        "name": "consultar_deuda",
        "description": (
            "Consulta el estado del préstamo del usuario YA IDENTIFICADO: saldo pendiente, "
            "cuotas pagadas y pendientes, próxima cuota y su vencimiento, estado (al día / en mora), "
            "recargo por mora y TCEA. NO recibe parámetros: la cuenta se resuelve por la identidad "
            "verificada del usuario. Solo disponible si el usuario ingresó por su enlace."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "registrar_reclamo",
        "description": (
            "Registra un reclamo o queja en el Libro de Reclamaciones (obligatorio por Indecopi en Perú). "
            "Devuelve el número de folio y el plazo de respuesta (15 días hábiles). "
            "Pide al usuario el tipo y una descripción ANTES de llamar esta tool."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "tipo": {
                    "type": "string",
                    "enum": ["reclamo", "queja"],
                    "description": "reclamo = disconformidad con el producto/servicio; queja = disconformidad con la atención",
                },
                "descripcion": {
                    "type": "string",
                    "description": "Descripción del reclamo o queja en palabras del usuario",
                },
            },
            "required": ["tipo", "descripcion"],
        },
    },
    {
        "name": "emitir_certificado_no_adeudo",
        "description": (
            "Emite un certificado de no adeudo (PDF descargable) SI el usuario identificado tiene saldo CERO "
            "(préstamo cancelado). Si tiene deuda pendiente, la tool indica que no procede. "
            "NO recibe parámetros: la cuenta se resuelve por la identidad verificada."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "enviar_documento",
        "description": (
            "Envía al cliente identificado un documento por el canal que elija. "
            "OFRÉCELE primero: '¿Te lo envío a tu correo o a tu WhatsApp?'. "
            "tipo: 'certificado_no_adeudo' (solo si no tiene deuda) o 'estado_cuenta' (resumen de su deuda). "
            "canal: 'correo' o 'whatsapp'. El correo y el teléfono salen de su cuenta verificada, "
            "NO los pidas ni los inventes."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "tipo": {
                    "type": "string",
                    "enum": ["certificado_no_adeudo", "estado_cuenta"],
                    "description": "Documento a enviar",
                },
                "canal": {
                    "type": "string",
                    "enum": ["correo", "whatsapp"],
                    "description": "Canal de entrega elegido por el cliente",
                },
            },
            "required": ["tipo", "canal"],
        },
    },
    {
        "name": "escalate_to_human",
        "description": "Deriva la conversación a un asesor humano de PrestaUnion (consultas legales, disputas formales, casos sensibles, o usuario sin enlace).",
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {"type": "string", "description": "Motivo de la derivación"},
            },
            "required": ["reason"],
        },
    },
]
