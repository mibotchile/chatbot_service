"""Prospect profile compression — replaces raw history with a structured summary.

Instead of sending 20+ raw messages to the LLM each turn (~8000 tokens),
compress the conversation into a ~200 token profile + last 3 messages.
"""

from typing import Any

# How many recent messages to keep as raw context alongside the profile
RECENT_MESSAGES_LIMIT = 6  # 3 user + 3 assistant turns


def build_prospect_profile(lead_status: dict, page_context: dict, history: list[dict]) -> str:
    """Build a compact prospect profile from lead data + conversation history.

    Returns a short text block (~100-200 tokens) summarizing:
    - Who they are (name, contact)
    - What they want (project, zone, budget, purpose)
    - Where they are in the funnel
    - Key topics discussed
    """
    collected = lead_status.get("collected", {})
    level = lead_status.get("level", "VISITOR")

    lines = ["## Perfil del Prospecto"]

    # Identity
    name = collected.get("name", "Desconocido")
    lines.append(f"- Nombre: {name}")

    # Contact
    contact = []
    if collected.get("email"):
        contact.append(f"email: {collected['email']}")
    if collected.get("phone"):
        contact.append(f"tel: {collected['phone']}")
    if contact:
        lines.append(f"- Contacto: {', '.join(contact)}")

    # Interest
    if collected.get("project_interest"):
        lines.append(f"- Proyecto interes: {collected['project_interest']}")
    if collected.get("district"):
        lines.append(f"- Zona: {collected['district']}")
    if collected.get("purpose"):
        purpose_map = {"investment": "Inversion", "primary_home": "Vivienda propia"}
        lines.append(f"- Proposito: {purpose_map.get(collected['purpose'], collected['purpose'])}")
    if collected.get("budget"):
        lines.append(f"- Presupuesto: {collected['budget']}")
    if collected.get("bedrooms"):
        lines.append(f"- Dormitorios: {collected['bedrooms']}")

    # Page context
    if page_context.get("project_name"):
        lines.append(f"- Viendo ahora: {page_context['project_name']}")

    # Visitor data
    visitor = page_context.get("visitor", {})
    if visitor.get("visit_count", 0) > 1:
        lines.append(f"- Visita #{visitor['visit_count']} (visitante recurrente)")
    if visitor.get("projects_viewed"):
        lines.append(f"- Proyectos vistos: {', '.join(visitor['projects_viewed'])}")

    # Funnel stage
    lines.append(f"- Etapa: {level}")

    # Extract key topics from history (lightweight — no LLM call)
    topics = _extract_topics(history)
    if topics:
        lines.append(f"- Temas tratados: {', '.join(topics)}")

    # Enrichment data
    enrichment = []
    if collected.get("income"):
        enrichment.append(f"ingreso: {collected['income']}")
    if collected.get("document_number"):
        enrichment.append(f"DNI: {collected['document_number']}")
    if collected.get("employer"):
        enrichment.append(f"empresa: {collected['employer']}")
    if enrichment:
        lines.append(f"- Datos adicionales: {', '.join(enrichment)}")

    return "\n".join(lines)


def truncate_history(history: list[dict], limit: int = RECENT_MESSAGES_LIMIT) -> list[dict]:
    """Keep only the last N messages from history."""
    if len(history) <= limit:
        return history
    return history[-limit:]


def _extract_topics(history: list[dict]) -> list[str]:
    """Extract discussion topics from history without an LLM call.

    Scans assistant messages for key phrases to build a topic list.
    """
    topics = set()
    topic_signals = {
        "brochure": ["brochure", "planos", "te envie"],
        "visita agendada": ["agendo tu visita", "visita confirmada", "agendar visita"],
        "simulacion cuota": ["cuota mensual", "simulacion", "mensualidad"],
        "comparacion": ["comparar", "comparacion", "vs"],
        "tipologias": ["tipologia", "plano", "flat", "loft"],
        "financiamiento": ["mivivienda", "bono", "credito", "hipotecario"],
        "tour virtual": ["tour virtual", "praux3d", "recorrido"],
    }

    for msg in history:
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content", "").lower()
        for topic, signals in topic_signals.items():
            if any(s in content for s in signals):
                topics.add(topic)

    return sorted(topics)
