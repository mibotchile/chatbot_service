"""Anthropic tool definitions for the cobranza agent.

Generic tools (suggest_quick_replies, navigate_page, collect_contact_info,
get_lead_status) are inherited from the engine. Domain tools are STUBS for
collections (cobranza) — schemas defined here, implementations are TODO.
"""

TOOL_DEFINITIONS = [
    # ── Generic engine tools (kept as-is) ───────────────────────────────
    {
        "name": "get_lead_status",
        "description": "Get current lead qualification level and missing data. Use to decide what to ask next.",
        "input_schema": {
            "type": "object",
            "properties": {
                "conversation_id": {"type": "string", "description": "Current conversation ID"},
            },
            "required": ["conversation_id"],
        },
    },
    {
        "name": "navigate_page",
        "description": "Scroll to a section on the current page or highlight an element. Use instead of re-fetching data the user can already see. Works on ANY page.",
        "input_schema": {
            "type": "object",
            "properties": {
                "scroll_to": {"type": "string", "description": "CSS selector to scroll to (e.g. #seccion-deuda, footer)"},
                "highlight": {"type": "string", "description": "CSS selector to briefly highlight (optional)"},
            },
            "required": ["scroll_to"],
        },
    },
    {
        "name": "suggest_quick_replies",
        "description": "OBLIGATORIO: llama esta tool AL FINAL de cada respuesta para generar opciones de respuesta rapida, coherentes con lo que acabas de preguntar o proponer.",
        "input_schema": {
            "type": "object",
            "properties": {
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 2,
                    "maxItems": 4,
                    "description": "2-4 opciones cortas (2-5 palabras) que el usuario puede clickear para responder. Coherentes con tu pregunta.",
                },
            },
            "required": ["options"],
        },
    },
    {
        "name": "collect_contact_info",
        "description": "Show an inline form to capture contact data. Use when the user agrees to share their info. The form renders inside the chat.",
        "input_schema": {
            "type": "object",
            "properties": {
                "form_type": {
                    "type": "string",
                    "enum": ["contact", "identity"],
                    "description": "contact = name+email+phone, identity = document + control data for verification",
                },
            },
            "required": ["form_type"],
        },
    },
    # ── Cobranza domain tools (STUBS — implementation TODO Fase 1) ───────
    {
        "name": "get_debt_detail",
        "description": "TODO: Devuelve el detalle de la deuda (monto total, capital, interes, mora) de una cuenta. Requiere verificacion de identidad previa.",
        "input_schema": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string", "description": "Identificador de la cuenta del deudor"},
            },
            "required": ["account_id"],
        },
    },
    {
        "name": "get_account_status",
        "description": "TODO: Devuelve el estado de la cuenta (al dia, en mora, dias de atraso, tramo).",
        "input_schema": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string", "description": "Identificador de la cuenta del deudor"},
            },
            "required": ["account_id"],
        },
    },
    {
        "name": "get_payment_channels",
        "description": "TODO: Devuelve los canales/medios de pago disponibles (links, transferencia, agentes).",
        "input_schema": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string", "description": "Identificador de la cuenta del deudor"},
            },
            "required": [],
        },
    },
    {
        "name": "simulate_payment_plan",
        "description": "TODO: Simula un plan de pago en cuotas para una deuda. Devuelve numero de cuotas, montos y fechas.",
        "input_schema": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string", "description": "Identificador de la cuenta del deudor"},
                "installments": {"type": "integer", "description": "Numero de cuotas deseado"},
            },
            "required": ["account_id"],
        },
    },
    {
        "name": "check_discount_eligibility",
        "description": "TODO: Verifica si la cuenta es elegible para descuento/quita por pronto pago.",
        "input_schema": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string", "description": "Identificador de la cuenta del deudor"},
            },
            "required": ["account_id"],
        },
    },
    {
        "name": "register_payment_promise",
        "description": "TODO: Registra una promesa de pago (PTP) con monto y fecha comprometida.",
        "input_schema": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string", "description": "Identificador de la cuenta del deudor"},
                "amount": {"type": "number", "description": "Monto comprometido"},
                "promise_date": {"type": "string", "description": "Fecha comprometida en formato YYYY-MM-DD"},
            },
            "required": ["account_id", "amount", "promise_date"],
        },
    },
    {
        "name": "escalate_to_human",
        "description": "TODO: Deriva la conversacion a un gestor humano de cobranza.",
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {"type": "string", "description": "Motivo de la derivacion"},
            },
            "required": ["reason"],
        },
    },
]
