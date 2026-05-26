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
        "Saluda de forma breve, cálida y profesional (trato de 'tú'). Preséntate como "
        "asistente de PrestaUnion. Si el usuario YA está identificado (ingresó por su enlace), "
        "ofrécele consultar su préstamo, registrar un reclamo o (si no tiene deuda) emitir "
        "su certificado de no adeudo. NO pidas datos personales por el chat."
    ),
    COLD: (
        "El usuario NO está identificado (no ingresó por su enlace seguro). "
        "NO reveles ni consultes datos de ninguna cuenta. NO pidas DNI, número de cuenta ni datos "
        "sensibles por el chat. Explica con amabilidad que, para ver la información de su préstamo, "
        "necesita ingresar por el enlace seguro que se le envió. Puedes ofrecer derivar a un asesor "
        "humano (escalate_to_human). Solo puedes responder preguntas generales (cómo pagar, qué es la "
        "TCEA, requisitos) sin tocar datos de cuenta."
    ),
    VERIFIED: (
        "El usuario está IDENTIFICADO. Tienes tres acciones disponibles:\n"
        "1. consultar_deuda — saldo, cuotas, próximo vencimiento, estado (al día / en mora).\n"
        "2. registrar_reclamo — Libro de Reclamaciones (pide tipo y descripción antes de registrar).\n"
        "3. emitir_certificado_no_adeudo — solo si el saldo es CERO; si hay deuda, explica que no procede.\n"
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
