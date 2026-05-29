"""Build structured responses with UI actions from tool results."""

from typing import Any


def build_ui_actions(tool_calls_results: list[tuple[str, dict]]) -> dict:
    """Build ui_actions from tool execution results.

    Args:
        tool_calls_results: list of (tool_name, result_dict) tuples

    Returns:
        dict with property_cards, comparison_table, mortgage_calc, subsidy_info, quick_replies
    """
    actions: dict[str, Any] = {}

    for tool_name, result in tool_calls_results:
        if tool_name == "search_properties" and result.get("properties"):
            actions["property_cards"] = [
                {
                    "slug": p.get("slug", ""),
                    "name": p.get("name") or p.get("property_name", ""),
                    "district": p.get("district", ""),
                    "min_price": p.get("price_from_pen") or p.get("min_price_pen") or p.get("min_price", 0),
                    "hero_image": p.get("hero_image", ""),
                    "construction_status": p.get("construction_status", ""),
                    "bedrooms": max(p["bedrooms"]) if isinstance(p.get("bedrooms"), list) else p.get("max_bedrooms") or p.get("bedrooms"),
                    "min_area": p.get("area_from_m2") or p.get("min_area"),
                }
                for p in result["properties"]
            ]
            if result.get("total") == 1:
                slug = result["properties"][0].get("slug", "")
                if slug:
                    actions["suggest_navigate"] = f"/proyectos/{slug}"

        elif tool_name == "get_property_detail":
            actions["property_detail"] = result
            if result.get("found") and result.get("slug"):
                actions["navigate_to"] = f"/proyectos/{result['slug']}"

        elif tool_name == "compare_properties":
            actions["comparison_table"] = result.get("comparison", [])

        elif tool_name == "simulate_mortgage":
            actions["mortgage_calc"] = {
                "monthly_payment": result["monthly_payment"],
                "down_payment": result["down_payment"],
                "loan_amount": result["loan_amount"],
                "total_cost": result["total_cost"],
                "annual_rate": result["annual_rate"],
            }

        elif tool_name == "check_subsidy_eligibility":
            actions["subsidy_info"] = result

        elif tool_name == "collect_contact_info":
            actions["form_data"] = result

        elif tool_name == "navigate_page":
            if result.get("scroll_to"):
                actions["scroll_to"] = result["scroll_to"]
            if result.get("highlight"):
                actions["highlight"] = result["highlight"]

        elif tool_name == "consultar_deuda" and isinstance(result, dict):
            panel = _build_debt_panel(result)
            if panel:
                actions["panel"] = panel

    return actions


def _status_badge(status: str, status_label: str, days_overdue: int) -> dict:
    """Map a credit status to a side-panel badge {kind, label}.

    ``kind`` ∈ {aldia, mora, libre} — drives the badge color in the widget.
    For mora, the day count is appended ("En mora · 11 días") when available.
    """
    s = (status or "").lower()
    label = status_label or ""
    if "mora" in s:
        d = int(days_overdue or 0)
        suffix = f" · {d} día{'s' if d != 1 else ''}" if d > 0 else ""
        return {"kind": "mora", "label": (label or "En mora") + suffix}
    if "cancel" in s or s == "sin_deuda" or "sin deuda" in label.lower():
        return {"kind": "libre", "label": label or "Sin deuda"}
    return {"kind": "aldia", "label": label or "Al día"}


def _debt_card(c: dict) -> dict:
    """One credit → one side-panel card (already-formatted strings + a badge).

    Reads the already-shaped fields from ``consultar_deuda`` (single summary or a
    ``_credit_brief`` entry) so the widget paints without any number logic.
    """
    return {
        "loan_number": c.get("loan_number") or c.get("account_id") or "",
        "currency": c.get("currency", "PEN"),
        "currency_symbol": c.get("currency_symbol", "S/"),
        "balance_formatted": c.get("balance_formatted", ""),
        "next_due_date": c.get("next_due_date"),
        "next_installment_formatted": c.get("next_installment_formatted", ""),
        "banco": c.get("banco"),
        "cci_masked": c.get("cci_masked"),
        "badge": _status_badge(c.get("status", ""), c.get("status_label", ""), c.get("days_overdue", 0)),
    }


def _build_debt_panel(summary: dict) -> dict | None:
    """Build the contextual side-panel payload from a ``consultar_deuda`` result.

    Tenant-agnostic / core: any tenant whose ``consultar_deuda`` returns this
    shape gets the panel for free. Multi-deuda → one card per credit; a grupal
    credit attaches its codeudores to the (single) card. Returns None when there
    is nothing meaningful to show.
    """
    if not summary.get("account_id"):
        return None

    if summary.get("has_multiple_credits") and summary.get("credits"):
        cards = [_debt_card(c) for c in summary["credits"]]
    else:
        cards = [_debt_card(summary)]

    if summary.get("is_grupal") and summary.get("codeudores"):
        cards[0]["is_grupal"] = True
        cards[0]["codeudores"] = summary["codeudores"]

    n = len(cards)
    title = "Tus créditos" if n > 1 else "Tu crédito"
    return {"type": "debt", "title": title, "count": n, "cards": cards}


def _detect_agent_ask(content: str) -> str | None:
    """Detect what the agent is asking for in its response."""
    import re
    c = content.lower()
    # Generic data ask ("me los puedes compartir/proporcionar/dar")
    if re.search(r"me los puedes (compartir|proporcionar|dar|pasar|facilitar)", c):
        # Detect which data is being asked based on what's missing in context
        if re.search(r"(nombre|telefono|numero|celular|whatsapp|correo|email)", c):
            return "contact_data"
        return "contact_data"  # generic ask = needs contact info
    # Name ask
    if re.search(r"(tu nombre|como te llamas|con quien|a nombre de|necesito.*nombre)", c):
        return "name"
    # Email ask
    if re.search(r"(tu correo|tu email|e-?mail|correo electr)", c):
        return "email"
    # Phone ask
    if re.search(r"(tu (numero|telefono|celular)|por whatsapp|a que numero)", c):
        return "phone"
    # Schedule ask
    if re.search(r"(que dia|cuando|manana o tarde|entre semana|fin de semana|que horario|prefieres)", c) and re.search(r"(visita|agendar|cita)", c):
        return "schedule"
    # District ask
    if re.search(r"(que (zona|distrito)|en donde|donde te (interesa|gustaria))", c):
        return "district"
    # Bedrooms ask
    if re.search(r"(cuantos (dorm|cuarto|habitaci)|2 o 3 dorm|tipo de departamento)", c):
        return "bedrooms"
    # Purpose ask
    if re.search(r"(para vivir|como inversion|para ti o|vivir o invertir)", c):
        return "purpose"
    # Budget ask
    if re.search(r"(presupuesto|rango de precio|cuanto.*invertir)", c):
        return "budget"
    return None


# Response chips per detected ask
_ASK_CHIPS: dict[str, list[dict]] = {
    "contact_data": [
        {"id": "give-data", "label": "Si, con gusto", "value": "Si, claro. Mi nombre es"},
        {"id": "later", "label": "Primero mas info", "value": "Primero quiero ver mas informacion del proyecto"},
        {"id": "whatsapp", "label": "Prefiero WhatsApp", "value": "Prefiero que me contacten por WhatsApp"},
    ],
    "schedule": [
        {"id": "sat-am", "label": "Sabado en la manana", "value": "El sabado en la manana"},
        {"id": "sat-pm", "label": "Sabado en la tarde", "value": "El sabado en la tarde"},
        {"id": "weekday", "label": "Entre semana", "value": "Un dia entre semana por la tarde"},
        {"id": "flexible", "label": "Soy flexible", "value": "Cualquier dia me viene bien"},
    ],
    "purpose": [
        {"id": "live", "label": "Para vivir", "value": "Para vivir con mi familia"},
        {"id": "invest", "label": "Inversion", "value": "Como inversion"},
        {"id": "both", "label": "Ambos", "value": "Estoy evaluando ambas opciones"},
    ],
    "bedrooms": [
        {"id": "1d", "label": "1 dormitorio", "value": "1 dormitorio"},
        {"id": "2d", "label": "2 dormitorios", "value": "2 dormitorios"},
        {"id": "3d", "label": "3 dormitorios", "value": "3 dormitorios"},
    ],
    "budget": [
        {"id": "b1", "label": "Menos de S/300k", "value": "Menos de S/300,000"},
        {"id": "b2", "label": "S/300k - S/500k", "value": "Entre S/300,000 y S/500,000"},
        {"id": "b3", "label": "Mas de S/500k", "value": "Mas de S/500,000"},
    ],
}


def build_quick_replies(
    lead_status: dict,
    ui_actions: dict,
    tool_results: list[tuple[str, dict]] | None = None,
    response_content: str = "",
) -> dict | None:
    """Generate contextual quick reply chips based on what the agent just asked.

    Priority: agent's question > tool results > lead state fallback.
    Returns a QuickReplySet: {type, buttons: [{id, label, value}]}
    """
    collected = lead_status.get("collected", {}) or {}
    buttons: list[dict] = []
    tools_called = {name for name, _ in (tool_results or [])}

    # PRIORITY 1: Match what the agent is asking
    if response_content:
        ask = _detect_agent_ask(response_content)
        if ask in _ASK_CHIPS:
            return {"type": "single_select", "buttons": _ASK_CHIPS[ask]}
        if ask == "district":
            try:
                import json
                from pathlib import Path
                projects_path = Path(__file__).resolve().parent.parent / "knowledge" / "projects.json"
                projects = json.loads(projects_path.read_text())["projects"]
                districts = list({p["district"] for p in projects})
                for d in districts[:4]:
                    buttons.append({"id": f"d-{d}", "label": d, "value": f"En {d}"})
                return {"type": "single_select", "buttons": buttons}
            except Exception:
                pass

    # PRIORITY 2: Data-driven chips from actual tool results

    cards = ui_actions.get("property_cards", [])
    detail = ui_actions.get("property_detail")
    mortgage = ui_actions.get("mortgage_calc")
    subsidy = ui_actions.get("subsidy_info")
    comparison = ui_actions.get("comparison_table", [])

    # After search: chips reference actual projects found
    if "search_properties" in tools_called and cards:
        if len(cards) == 1:
            name = cards[0].get("name", "el proyecto")
            price = cards[0].get("min_price", 0)
            buttons.append({"id": "detail", "label": f"Ver {name}", "value": f"Cuentame mas sobre {name}"})
            if price and price > 0:
                buttons.append({"id": "mortgage", "label": f"Cuota de {name}", "value": f"Simula la cuota mensual de {name}"})
            buttons.append({"id": "visit", "label": "Agendar visita", "value": f"Quiero visitar {name}"})
        else:
            # Show cheapest + most expensive as contrast, plus compare
            sorted_cards = sorted(cards, key=lambda c: c.get("min_price", 0))
            cheapest = sorted_cards[0].get("name", "")
            buttons.append({"id": f"p-{sorted_cards[0].get('slug', '')}", "label": f"Ver {cheapest}", "value": f"Cuentame mas sobre {cheapest}"})
            if len(sorted_cards) > 1:
                other = sorted_cards[-1].get("name", "")
                if other != cheapest:
                    buttons.append({"id": f"p-{sorted_cards[-1].get('slug', '')}", "label": f"Ver {other}", "value": f"Cuentame mas sobre {other}"})
            if len(cards) > 2:
                buttons.append({"id": "compare", "label": "Comparar todos", "value": "Compara estos proyectos"})
            buttons.append({"id": "mortgage", "label": "Simular cuota", "value": f"Simula la cuota del mas barato"})

    # After property detail: contextual to what the user just saw
    elif "get_property_detail" in tools_called and detail:
        name = detail.get("name") or detail.get("property_name", "el proyecto")
        has_typologies = bool(detail.get("typologies"))
        buttons.append({"id": "typologies", "label": "Ver tipologias", "value": f"Muestrame las tipologias de {name}"})
        buttons.append({"id": "mortgage", "label": "Simular cuota", "value": f"Simula la cuota de {name}"})
        if "email" in collected:
            buttons.append({"id": "visit", "label": "Agendar visita", "value": f"Quiero visitar {name}"})
        else:
            buttons.append({"id": "brochure", "label": "Enviar brochure", "value": f"Enviame el brochure de {name}"})

    # After mortgage: contextual to the actual simulation results
    elif mortgage:
        monthly = mortgage.get("monthly_payment", 0)
        if monthly > 3000:
            buttons.append({"id": "less", "label": "Cuota mas baja", "value": "Hay opciones con cuota mas baja?"})
        buttons.append({"id": "mivivienda", "label": "Aplico a subsidio?", "value": "Puedo acceder al bono MiVivienda?"})
        if "name" in collected and "phone" in collected:
            buttons.append({"id": "visit", "label": "Agendar visita", "value": "Quiero agendar una visita"})
        else:
            buttons.append({"id": "other", "label": "Otro proyecto", "value": "Que otros proyectos tienes?"})

    # After subsidy check: follow-up based on eligibility
    elif subsidy:
        if subsidy.get("mivivienda"):
            buttons.append({"id": "miv", "label": "Proyectos MiVivienda", "value": "Que proyectos aplican a MiVivienda?"})
        buttons.append({"id": "mortgage", "label": "Simular cuota", "value": "Simula la cuota con el bono incluido"})
        buttons.append({"id": "visit", "label": "Agendar visita", "value": "Quiero agendar una visita"})

    # After comparison: reference the actual projects compared
    elif comparison:
        for proj in comparison[:3]:
            name = proj.get("name", "")
            if name:
                buttons.append({"id": f"p-{proj.get('slug', '')}", "label": f"Mas de {name}", "value": f"Cuentame mas sobre {name}"})
        buttons.append({"id": "visit", "label": "Visitar uno", "value": "Quiero visitar el que mas me conviene"})

    # No tool called — lead-aware chips
    elif not tools_called:
        project_interest = collected.get("project_interest", "")

        # Has project interest → advance toward conversion
        if project_interest:
            buttons.append({"id": "mortgage", "label": "Simular cuota", "value": f"Simula la cuota de {project_interest}"})
            if "name" in collected:
                buttons.append({"id": "visit", "label": "Agendar visita", "value": f"Quiero visitar {project_interest}"})
            buttons.append({"id": "compare", "label": "Comparar opciones", "value": "Que otros proyectos tienes en esa zona?"})

        # Has district preference → search in that district
        elif "district" in collected:
            district = collected["district"]
            buttons.append({"id": "search", "label": f"Ver en {district}", "value": f"Muestrame departamentos en {district}"})
            buttons.append({"id": "budget", "label": "Con presupuesto", "value": "Tengo un presupuesto de"})
            buttons.append({"id": "all", "label": "Otras zonas", "value": "Que otras zonas tienen proyectos?"})

        # Fresh visitor — discovery with real data
        else:
            try:
                import json as _json
                from pathlib import Path as _Path
                projects_path = _Path(__file__).resolve().parent.parent / "knowledge" / "projects.json"
                projects = _json.loads(projects_path.read_text())["projects"]
                districts = list({p["district"] for p in projects})
                statuses = {p.get("construction_status", "").lower() for p in projects}

                for d in districts[:2]:
                    buttons.append({"id": f"d-{d}", "label": d, "value": f"Busco departamento en {d}"})
                if "entrega inmediata" in statuses:
                    buttons.append({"id": "inmediata", "label": "Entrega inmediata", "value": "Proyectos con entrega inmediata"})
                else:
                    buttons.append({"id": "all", "label": "Ver todos", "value": "Muestrame todos los proyectos"})
                buttons.append({"id": "budget", "label": "Tengo presupuesto", "value": "Busco departamento hasta"})
            except Exception:
                buttons.append({"id": "all", "label": "Ver proyectos", "value": "Muestrame todos los proyectos"})

    if not buttons:
        return None

    return {
        "type": "single_select",
        "buttons": buttons[:4],
    }
