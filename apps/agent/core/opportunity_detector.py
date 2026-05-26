"""Detect opportunities to ask for missing lead data with natural excuses."""

from core.lead_machine import LeadMachine, EXTRACTION_EXCUSES


def detect_opportunities(
    lead: LeadMachine,
    tool_calls_made: list[str],
    response_content: str,
) -> list[dict]:
    """Detect if the current context creates a natural excuse to ask for data.

    Returns list of opportunities with field, excuse, and trigger reason.
    """
    status = lead.get_status()
    missing = set(status["missing"])
    opportunities = []

    # If we showed properties → excuse to send brochure → capture email
    if "search_properties" in tool_calls_made and "email" in missing:
        opportunities.append({
            "field": "email",
            "excuse": "Puedo enviarte los planos y brochure a tu correo",
            "trigger": "properties_shown",
        })

    # If user asks about a specific property → excuse to schedule visit → capture phone
    if "get_property_detail" in tool_calls_made and "phone" in missing:
        opportunities.append({
            "field": "phone",
            "excuse": "Quieres que te agende una visita? Te confirmo por WhatsApp",
            "trigger": "property_detail_shown",
        })

    # If mortgage simulation done → excuse to check subsidy → capture income
    if "simulate_mortgage" in tool_calls_made and "income" in missing:
        opportunities.append({
            "field": "income",
            "excuse": "Con tu ingreso mensual puedo verificar si aplicas al bono MiVivienda",
            "trigger": "mortgage_calculated",
        })

    # If user expressed interest and we have name but no contact
    if lead.level == "PRE_LEAD" and "name" not in missing:
        if "phone" in missing:
            opportunities.append({
                "field": "phone",
                "excuse": "Para darte una atención más personalizada, ¿a qué número te contacto?",
                "trigger": "pre_lead_with_name",
            })

    return opportunities
