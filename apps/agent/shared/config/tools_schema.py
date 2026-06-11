"""Tenant-neutral tool definitions for the cobranza agent.

Provider-agnostic schema: each tool is {name, description, parameters} where
`parameters` is a JSON Schema object. Providers translate it (Anthropic →
`input_schema`, OpenAI → `function.parameters`). See core/llm/.

Generic engine tools (suggest_quick_replies, navigate_page, collect_contact_info,
get_debtor_status) are kept. The cobranza domain has THREE tools:
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
        "name": "get_debtor_status",
        "description": "Get current debtor qualification level and missing data. Use to decide what to ask next.",
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
            "Envía al cliente identificado un documento al correo o WhatsApp que ÉL indique. "
            "ANTES de llamar esta tool PREGÚNTALE a qué correo o número de WhatsApp quiere recibirlo "
            "('¿A qué correo o WhatsApp te lo envío?') y pasa ese dato en 'destino'. "
            "tipo: 'certificado_no_adeudo' (solo si no tiene deuda) o 'estado_cuenta' (resumen de su deuda). "
            "El documento y la identidad salen de su cuenta verificada; lo ÚNICO que viene del usuario es "
            "el 'destino' de entrega. No inventes el destino: si no te lo dio, pregúntalo."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "tipo": {
                    "type": "string",
                    "enum": ["certificado_no_adeudo", "estado_cuenta"],
                    "description": "Documento a enviar",
                },
                "destino": {
                    "type": "string",
                    "description": "Correo (con @) o número de WhatsApp que el USUARIO indicó para recibir el documento",
                },
                "canal": {
                    "type": "string",
                    "enum": ["correo", "whatsapp"],
                    "description": "Opcional; se infiere de 'destino' (con @ → correo; número → whatsapp)",
                },
            },
            "required": ["tipo", "destino"],
        },
    },
    {
        "name": "enviar_info",
        "description": (
            "Envía al cliente identificado UN TIPO DE INFORMACIÓN a su correo o WhatsApp REGISTRADO "
            "(no a uno que él escriba). ANTES de llamar, pregúntale el canal ('¿a tu correo o por WhatsApp?'). "
            "tipo: 'estado_cuenta' (su deuda/saldo), 'datos_pago' (CCI/banco/monto/vencimiento) o "
            "'constancia_comprobante' (recepción de su comprobante). canal: 'correo' o 'whatsapp'. "
            "La identidad y el destino salen de su cuenta verificada; confirma con el destino ENMASCARADO. "
            "En demo el envío es simulado; nunca inventes el destino."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "tipo": {
                    "type": "string",
                    "enum": ["estado_cuenta", "datos_pago", "constancia_comprobante"],
                    "description": "Tipo de información a enviar",
                },
                "canal": {
                    "type": "string",
                    "enum": ["correo", "whatsapp"],
                    "description": "Canal elegido por el cliente",
                },
            },
            "required": ["tipo", "canal"],
        },
    },
    {
        "name": "validar_comprobante",
        "description": (
            "Registra un comprobante de pago del cliente YA IDENTIFICADO (PrestamYpe). "
            "Pídele solo: foto del voucher, monto pagado e inversionista (a quién pagó). "
            "El ID de crédito es OPCIONAL — si el usuario no lo sabe, no insistas. "
            "NO pidas CCI ni número de operación — se resuelven del lado del servidor. "
            "La tool clasifica la operación (pago cuota / abono / cancelación) según el monto "
            "y deja el comprobante EN REVISIÓN para conciliación humana. Si el inversionista "
            "no coincide con el registrado, se registra igual con una alerta para el equipo. "
            "La identidad y el crédito salen de la cuenta verificada; no inventes ningún dato."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "monto": {
                    "type": "number",
                    "description": "Monto transferido según el comprobante del usuario",
                },
                "inversionista": {
                    "type": "string",
                    "description": "Nombre del inversionista al que el usuario realizó el pago (opcional — pregúntalo pero no bloquees si no lo tiene)",
                },
                "id_credito": {
                    "type": "string",
                    "description": "ID del crédito si el usuario lo menciona (opcional — se resuelve del crédito verificado si no se provee)",
                },
            },
            "required": ["monto"],
        },
    },
    {
        "name": "escalate_to_human",
        "description": "Deriva la conversación a un asesor humano (consultas legales, disputas formales, casos sensibles, o usuario sin enlace).",
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {"type": "string", "description": "Motivo de la derivación"},
            },
            "required": ["reason"],
        },
    },
]
