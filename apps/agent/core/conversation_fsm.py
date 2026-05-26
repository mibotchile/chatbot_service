"""Conversation state machine — controls agent behavior per stage.

Cobranza model: the primary axis is *identity*, not data completeness. The
identity gate short-circuits everything else (design doc, riesgo #2):
  - No verified identity  → COLD  (cold channel: offer the secure link / human)
  - Verified identity     → VERIFIED (debt / reclamo / certificado available)

The lead-completeness states are not used in the demo flow.
"""

# --- States ---

GREETING = "greeting"
COLD = "cold"
VERIFIED = "verified"

# --- State rules (Spanish — injected into system prompt) ---

_STATE_RULES: dict[str, str] = {
    GREETING: (
        "Eres Ada, asistente de PrestaUnion. Saluda breve, cálida y profesional (trato de 'tú'). "
        "Si el usuario YA está identificado, ofrécele consultar su préstamo, registrar un reclamo o "
        "(si no tiene deuda) su certificado de no adeudo. Si NO está identificado, para ayudarlo pídele "
        "su número de DNI (di algo como 'Para ayudarte, indícame tu DNI'). NO pidas otros datos sensibles."
    ),
    COLD: (
        "Eres Ada. El usuario NO está identificado todavía. Para poder ayudarlo, PÍDELE su número de DNI "
        "(8 dígitos): 'Para ayudarte, indícame tu DNI, por favor'. En cuanto te lo dé, llama a "
        "identificar_cliente con ese DNI. Si el DNI es válido quedará identificado; si no, infórmaselo con "
        "amabilidad y NO reveles ningún dato. NO reveles ni consultes datos de ninguna cuenta antes de "
        "identificar. Puedes responder preguntas generales (cómo pagar, qué es la TCEA, requisitos) y "
        "derivar a un asesor (escalate_to_human). Nunca pidas contraseñas ni datos de tarjeta."
    ),
    VERIFIED: (
        "Eres Ada. El usuario está IDENTIFICADO. Acciones disponibles:\n"
        "1. consultar_deuda — saldo, cuotas, próximo vencimiento, estado (al día / en mora).\n"
        "2. registrar_reclamo — Libro de Reclamaciones (pide tipo y descripción antes de registrar).\n"
        "3. emitir_certificado_no_adeudo — SOLO si el saldo es CERO; si hay deuda, explica que no procede "
        "hasta cancelar el préstamo (no lo ofrezcas a quien tiene deuda).\n"
        "4. enviar_documento — entrega un documento (certificado_no_adeudo o estado_cuenta). ANTES de "
        "enviar, PREGÚNTALE a qué correo o WhatsApp quiere recibirlo ('¿A qué correo o WhatsApp te lo "
        "envío?') y envíalo a ESE destino (no asumas el de su cuenta). Confírmale el envío al destino dado.\n"
        "Usa SIEMPRE los datos que devuelven las herramientas. NUNCA inventes montos, fechas ni "
        "condiciones. Si la consulta es legal o una disputa formal, deriva con escalate_to_human."
    ),
}


def detect_state(
    lead_status: dict,
    history: list[dict],
    page_context: dict,
    identity: dict | None = None,
) -> str:
    """Detect conversation state. Identity gate has absolute priority.

    Priority:
      1. No messages → GREETING
      2. Verified identity → VERIFIED
      3. Otherwise → COLD (cold channel, gate closed)
    """
    if not history:
        return GREETING

    ident = identity if identity is not None else page_context.get("identity", {})
    if ident.get("verified"):
        return VERIFIED
    return COLD


def get_state_rules(state: str) -> str:
    """Return the behavioral rules for a given conversation state."""
    return _STATE_RULES.get(state, _STATE_RULES[COLD])
