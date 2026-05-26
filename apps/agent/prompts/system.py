"""System prompt builder — assembles core + skills into the final prompt."""

import json
from dataclasses import replace
from datetime import date
from pathlib import Path

from config.soul import AgentSoul
from core.conversation_fsm import detect_state, get_state_rules

_GUARDRAILS = (Path(__file__).parent / "guardrails.md").read_text()

_KNOWLEDGE_DIR = Path(__file__).resolve().parent.parent / "knowledge"


def _load_knowledge(name: str, key: str | None = None):
    """Load a knowledge JSON, degrading to empty when absent (Fase 0: no KB yet)."""
    path = _KNOWLEDGE_DIR / name
    if not path.exists():
        return [] if key else {}
    data = json.loads(path.read_text())
    return data.get(key, []) if key else data


# Cobranza KB not authored yet (Fase 1) — these default to empty.
_FAQ: list[dict] = _load_knowledge("faq.json", "faqs")
_FINANCIAL: dict = _load_knowledge("financial_literacy.json")
_SALES_ARSENAL: dict = _load_knowledge("sales_arsenal.json")


def _format_faq(faqs: list[dict]) -> str:
    """Turn FAQ JSON into readable prompt text."""
    lines: list[str] = []
    for cat in faqs:
        lines.append(f"## {cat['category'].replace('_', ' ').title()}")
        lines.append("Preguntas frecuentes: " + " / ".join(cat["questions"]))
        for key, val in cat["knowledge"].items():
            lines.append(f"- {key}: {val}")
        lines.append("")
    return "\n".join(lines).strip()


def _format_sales_arsenal(data: dict, priority_slug: str = "") -> str:
    """Turn sales arsenal JSON into prompt text per project.

    If *priority_slug* is set, that project is listed first.
    """
    projects = data.get("projects", {})
    if priority_slug and priority_slug in projects:
        ordered = [(priority_slug, projects[priority_slug])]
        ordered += [(s, a) for s, a in projects.items() if s != priority_slug]
    else:
        ordered = list(projects.items())
    lines: list[str] = []
    for slug, arsenal in ordered:
        name = slug.replace("-", " ").title()
        words = ", ".join(arsenal["palabras_que_definen"])
        lines.append(f"## {name}")
        lines.append(f"Palabras que lo definen: {words}")
        lines.append("Caracteristicas principales (CPP):")
        for i, cpp in enumerate(arsenal["cpp"], 1):
            lines.append(f"  {i}. {cpp}")
        lines.append("Argumentos de experiencia:")
        for i, exp in enumerate(arsenal["experiencia_cliente"], 1):
            lines.append(f"  {i}. {exp}")
        lines.append("")
    return "\n".join(lines).strip()


def _format_financial(data: dict) -> str:
    """Turn financial literacy JSON into readable prompt text."""
    lines: list[str] = []
    disclaimer = data.get("_disclaimer", "")
    if disclaimer:
        lines.append(f"Disclaimer: {disclaimer}\n")

    for section, content in data.items():
        if section.startswith("_") or section == "sorelia_rules":
            continue
        title = section.replace("_", " ").title()
        if isinstance(content, dict):
            lines.append(f"## {title}")
            for k, v in content.items():
                if isinstance(v, list):
                    lines.append(f"- {k}: {', '.join(str(i) for i in v)}")
                elif isinstance(v, dict):
                    sub = "; ".join(f"{sk}: {sv}" for sk, sv in v.items())
                    lines.append(f"- {k}: {sub}")
                else:
                    lines.append(f"- {k}: {v}")
            lines.append("")
        elif isinstance(content, list):
            lines.append(f"## {title}")
            for item in content:
                lines.append(f"- {item}")
            lines.append("")

    rules = data.get("sorelia_rules", {})
    if rules:
        lines.append("## Reglas de Sorelia sobre financiamiento")
        if "puede_decir" in rules:
            lines.append("Puede decir: " + " / ".join(rules["puede_decir"]))
        if "no_puede_decir" in rules:
            lines.append("NO puede decir: " + " / ".join(rules["no_puede_decir"]))

    return "\n".join(lines).strip()


# Default soul — can be overridden per tenant
_default_soul = AgentSoul()


def build_system_prompt(
    lead_state: dict,
    page_context: dict,
    conversation_summary: str = "",
    soul: AgentSoul | None = None,
    history: list[dict] | None = None,
    channel: str = "web",
    tenant: "object | None" = None,
) -> str:
    from skills import load_skill, DEFAULT_SKILLS, WEB_ONLY_SKILLS, WHATSAPP_ONLY_SKILLS

    # ── Resolve tenant data ──
    if tenant is not None:
        soul = tenant.soul
        faq = tenant.faq
        financial = tenant.financial
        sales_arsenal = tenant.sales_arsenal
        guardrails = tenant.guardrails or _GUARDRAILS
        skill_names = getattr(tenant, "skills", None) or DEFAULT_SKILLS
    else:
        soul = soul or _default_soul
        faq = _FAQ
        financial = _FINANCIAL
        sales_arsenal = _SALES_ARSENAL
        guardrails = _GUARDRAILS
        skill_names = DEFAULT_SKILLS

    if channel == "whatsapp":
        soul = replace(soul, max_response_words=60)

    # ── Core sections (always present) ──
    sections: list[str] = []
    sections.append(soul.to_prompt_section())
    sections.append(f"# FECHA ACTUAL\nHoy es {date.today().strftime('%A %d de %B de %Y')} ({date.today().isoformat()})")

    # Channel-specific skills
    if channel == "whatsapp":
        for sn in WHATSAPP_ONLY_SKILLS:
            rendered = load_skill(sn, {})
            if rendered:
                sections.append(rendered)

    # FSM state — identity gate drives the state (cobranza)
    conv_history = history if history is not None else []
    identity = page_context.get("identity", {}) if page_context else {}
    state = detect_state(lead_state, conv_history, page_context, identity=identity)
    sections.append(f"# ESTADO CONVERSACIONAL: {state}\n{get_state_rules(state)}")

    # Verified identity context — the agent narrates only what's here / from tools
    if identity.get("verified"):
        sections.append(
            "# USUARIO IDENTIFICADO\n"
            f"Nombre: {identity.get('borrower_name', '')}\n"
            f"Negocio (MYPE): {identity.get('business_name', '')}\n"
            f"Préstamo: {identity.get('loan_number', '')}\n"
            f"Estado: {identity.get('status_label', '')}\n"
            "Para montos exactos, cuotas y fechas usa la herramienta consultar_deuda. "
            "Nunca inventes cifras."
        )

    # Lead state
    if lead_state:
        level = lead_state.get("level", "VISITOR")
        collected = lead_state.get("collected", {})
        missing = lead_state.get("missing", [])
        opportunities = lead_state.get("opportunities", [])
        sections.append(
            f"# ESTADO DEL LEAD\n"
            f"Nivel actual: {level}\n"
            f"Datos recolectados: {collected}\n"
            f"Datos faltantes: {', '.join(missing) if missing else 'ninguno'}\n"
            f"Oportunidades de extraccion: {'; '.join(opportunities) if opportunities else 'ninguna'}"
        )

    # Page context
    if page_context:
        page = page_context.get("page", "home")
        project = page_context.get("project_name", "")
        ctx_text = f"# CONTEXTO DE PAGINA\nPagina: {page}"
        if project:
            ctx_text += f"\nProyecto: {project} (slug: {page_context.get('project_slug', '')})"
        sections.append(ctx_text)

    # Conversation summary
    if conversation_summary:
        sections.append(f"# RESUMEN DE CONVERSACION\n{conversation_summary}")

    # ── Build skill context (template variables) ──
    _ap = sales_arsenal.get("projects", {})
    _first_slug = next(iter(_ap), "")
    _first_name = _first_slug.replace("-", " ").title() if _first_slug else "tu proyecto"
    _first_words = _ap.get(_first_slug, {}).get("palabras_que_definen", [])[:3] if _first_slug else []

    priority_slug = page_context.get("project_slug", "") or (
        lead_state.get("collected", {}).get("project_interest", "")
        if isinstance(lead_state.get("collected"), dict)
        else ""
    )

    skill_ctx = {
        "company": soul.company,
        "agent_name": soul.name,
        "agent_role": soul.role,
        "escalation_contact": soul.escalation_contact,
        "project_count": str(len(_ap)),
        "project_list": ", ".join(s.replace("-", " ").title() for s in _ap),
        "enrichment_list": "".join(
            f"{i}. {e}\n" for i, e in enumerate(soul.enrichment_excuses, 1)
        ),
        "first_project_name": _first_name,
        "first_project_words": (
            ", ".join(f"'{w}'" for w in _first_words)
            if _first_words
            else "'palabras especificas'"
        ),
        "formatted_arsenal": _format_sales_arsenal(sales_arsenal, priority_slug=priority_slug),
        "formatted_faq": _format_faq(faq),
        "formatted_financial": _format_financial(financial),
        "word_limit": str(60 if channel == "whatsapp" else 80),
        "currency": soul.currency,
    }

    # ── Load tenant skills ──
    for skill_name in skill_names:
        rendered = load_skill(skill_name, skill_ctx)
        if rendered:
            sections.append(rendered)

    # Web-only skills
    if channel != "whatsapp":
        for sn in WEB_ONLY_SKILLS:
            rendered = load_skill(sn, skill_ctx)
            if rendered:
                sections.append(rendered)

    # ── Core: guardrails (always present) ──
    sections.append(f"# RESTRICCIONES\n{guardrails}")

    return "\n\n".join(sections)
