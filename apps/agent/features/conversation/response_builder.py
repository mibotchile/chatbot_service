"""Build structured responses with UI actions from tool results."""

from typing import Any


def build_ui_actions(tool_calls_results: list[tuple[str, dict]]) -> dict:
    """Build ui_actions from tool execution results.

    Args:
        tool_calls_results: list of (tool_name, result_dict) tuples

    Returns:
        dict with panel, form_data, scroll_to, highlight
    """
    actions: dict[str, Any] = {}

    for tool_name, result in tool_calls_results:
        if tool_name == "collect_contact_info":
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
        # Full destination account so the borrower can transfer the payment.
        "cci": c.get("cci"),
        "cci_masked": c.get("cci_masked"),
        "badge": _status_badge(
            c.get("status", ""), c.get("status_label", ""), c.get("days_overdue", 0)
        ),
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
    return None


# Response chips per detected ask
_ASK_CHIPS: dict[str, list[dict]] = {
    "contact_data": [
        {"id": "give-data", "label": "Si, con gusto", "value": "Si, claro. Mi nombre es"},
        {
            "id": "later",
            "label": "Primero mas info",
            "value": "Primero quiero ver mas informacion del proyecto",
        },
        {
            "id": "whatsapp",
            "label": "Prefiero WhatsApp",
            "value": "Prefiero que me contacten por WhatsApp",
        },
    ],
}


def build_quick_replies(
    debtor_status: dict,
    ui_actions: dict,
    tool_results: list[tuple[str, dict]] | None = None,
    response_content: str = "",
) -> dict | None:
    """Generate contextual quick reply chips based on what the agent just asked.

    Priority: agent's question > lead state fallback.
    Returns a QuickReplySet: {type, buttons: [{id, label, value}]}
    """
    buttons: list[dict] = []

    # PRIORITY 1: Match what the agent is asking
    if response_content:
        ask = _detect_agent_ask(response_content)
        if ask in _ASK_CHIPS:
            return {"type": "single_select", "buttons": _ASK_CHIPS[ask]}

    if not buttons:
        return None

    return {
        "type": "single_select",
        "buttons": buttons[:4],
    }
