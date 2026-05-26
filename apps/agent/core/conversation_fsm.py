"""Conversation state machine — controls agent behavior per stage.

States progress based on what the agent knows about the user, not on
lead qualification level.  The lead machine tracks *data completeness*;
this FSM tracks *conversational intent* and gates what the agent is
allowed to ask or offer at each turn.
"""

# --- States ---

GREETING = "greeting"
EXPLORING = "exploring"
INTERESTED = "interested"
QUALIFYING = "qualifying"
CLOSING = "closing"
ENRICHING = "enriching"

# --- State rules (Spanish — injected into system prompt) ---

_STATE_RULES: dict[str, str] = {
    GREETING: (
        "Saluda brevemente. Pregunta que busca. NO pidas datos personales.\n"
        "Tu objetivo es entender que necesita: zona, tipo de depa, presupuesto."
    ),
    EXPLORING: (
        "Responde preguntas, muestra opciones. Usa herramientas (search_properties, simulate_mortgage).\n"
        "NO pidas datos personales todavia. Enfocate en dar valor y entender preferencias."
    ),
    INTERESTED: (
        "Profundiza en el proyecto que interesa. Usa el arsenal de ventas (CPP, experiencia, palabras clave).\n"
        "Puedes preguntar el nombre de forma natural: 'A quien le preparo la cotizacion?'"
    ),
    QUALIFYING: (
        "Ya sabes su interes. Pide email O telefono (no ambos a la vez).\n"
        "Ofrece valor a cambio: brochure, planos, simulacion personalizada, agendar visita.\n"
        "Si ya pidiste un dato y no lo dio, NO insistas — espera a que surja naturalmente."
    ),
    CLOSING: (
        "Agenda visita, envia brochure, conecta con asesor. Usa datos reales de urgencia.\n"
        "Tienes suficiente info para actuar — ejecuta las acciones, no solo las propongas."
    ),
    ENRICHING: (
        "Ya es contacto completo. Solo pide datos extra (DNI, ingreso, empleador) si la conversacion\n"
        "lo permite naturalmente. Usa excusas de valor: pre-calificacion bancaria, bono MiVivienda, convenios."
    ),
}


def detect_state(
    lead_status: dict,
    history: list[dict],
    page_context: dict,
) -> str:
    """Detect current conversation state from lead data and history.

    Priority order (first match wins):
      1. No messages at all → GREETING
      2. Has name + email + phone → ENRICHING
      3. Has name + (email or phone) → CLOSING
      4. Has some contact data (name or email) → QUALIFYING
      5. Has project interest → INTERESTED
      6. Has messages but nothing else → EXPLORING
    """
    collected = lead_status.get("collected", {})

    # 1. No history → greeting
    if not history:
        return GREETING

    has_name = "name" in collected
    has_email = "email" in collected
    has_phone = "phone" in collected
    has_project = "project_interest" in collected

    # 2. Full contact → enriching
    if has_name and has_email and has_phone:
        return ENRICHING

    # 3. Name + one contact method → closing
    if has_name and (has_email or has_phone):
        return CLOSING

    # 4. Any contact data → qualifying
    if has_name or has_email:
        return QUALIFYING

    # 5. Project interest (from collected data or page context)
    if has_project:
        return INTERESTED

    # Also check page context — viewing a project detail page signals interest
    page = page_context.get("page", "")
    if page == "project_detail" or page_context.get("project_slug"):
        return INTERESTED

    # 6. Default for active conversations
    return EXPLORING


def get_state_rules(state: str) -> str:
    """Return the behavioral rules for a given conversation state."""
    return _STATE_RULES.get(state, _STATE_RULES[EXPLORING])
