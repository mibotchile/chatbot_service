"""Format ui_actions into WhatsApp-native message payloads.

Converts structured widget data (property cards, mortgage calc, subsidy info,
quick replies, comparison tables) into Evolution API message formats:
- Interactive buttons (≤3 options)
- Interactive lists (4+ options)
- Formatted text blocks
- Media with captions
"""

from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# Public: one-call entry point
# ---------------------------------------------------------------------------

def format_for_whatsapp(
    ui_actions: dict[str, Any],
    quick_replies: dict[str, Any] | None,
    phone: str,
) -> list[dict[str, Any]]:
    """Convert ui_actions + quick_replies into a list of WA message payloads.

    Each payload has a "type" key indicating the Evolution API endpoint:
      - "text"        → /message/sendText
      - "media"       → /message/sendMedia
      - "buttons"     → /message/sendButtons  (≤3 reply buttons)
      - "list"        → /message/sendList      (4-10 list rows)

    Returns an empty list if nothing to send.
    """
    messages: list[dict[str, Any]] = []

    if ui_actions.get("mortgage_calc"):
        messages.append(_format_mortgage_calc(ui_actions["mortgage_calc"], phone))

    if ui_actions.get("subsidy_info"):
        messages.append(_format_subsidy_info(ui_actions["subsidy_info"], phone))

    if ui_actions.get("comparison_table"):
        messages.extend(_format_comparison_gallery(ui_actions["comparison_table"], phone))

    if quick_replies and quick_replies.get("buttons"):
        messages.append(_format_quick_replies(quick_replies, phone))

    return messages


# ---------------------------------------------------------------------------
# Quick Replies → Interactive Buttons or List
# ---------------------------------------------------------------------------

def _format_quick_replies(
    quick_replies: dict[str, Any],
    phone: str,
) -> dict[str, Any]:
    """Convert quick_replies to WA interactive buttons (≤3) or list (4+)."""
    buttons = quick_replies.get("buttons", [])

    if len(buttons) <= 3:
        return {
            "type": "buttons",
            "phone": phone,
            "payload": {
                "number": phone,
                "title": "Opciones",
                "description": "Elige una opcion:",
                "buttons": [
                    {
                        "type": "reply",
                        "displayText": btn["label"],
                        "id": btn.get("id", f"btn-{i}"),
                    }
                    for i, btn in enumerate(buttons[:3])
                ],
            },
        }

    # 4+ buttons → list message
    return {
        "type": "list",
        "phone": phone,
        "payload": {
            "number": phone,
            "title": "Opciones",
            "description": "Elige una opcion:",
            "buttonText": "Ver opciones",
            "sections": [
                {
                    "title": "Opciones",
                    "rows": [
                        {
                            "title": btn["label"][:24],
                            "description": btn.get("value", "")[:72],
                            "rowId": btn.get("id", f"row-{i}"),
                        }
                        for i, btn in enumerate(buttons[:10])
                    ],
                }
            ],
        },
    }


# ---------------------------------------------------------------------------
# Mortgage Calc → Formatted Text
# ---------------------------------------------------------------------------

def _format_mortgage_calc(
    calc: dict[str, Any],
    phone: str,
) -> dict[str, Any]:
    """Format mortgage simulation as WhatsApp structured text."""
    monthly = _fmt_soles(calc.get("monthly_payment", 0))
    down = _fmt_soles(calc.get("down_payment", 0))
    loan = _fmt_soles(calc.get("loan_amount", 0))
    total = _fmt_soles(calc.get("total_cost", 0))
    rate = calc.get("annual_rate", 0)

    text = (
        "*Simulacion de cuota*\n\n"
        f"Cuota mensual: *S/ {monthly}*\n"
        f"Inicial: S/ {down}\n"
        f"Prestamo: S/ {loan}\n"
        f"Tasa anual: {rate}%\n"
        f"Costo total: S/ {total}"
    )

    return {
        "type": "text",
        "phone": phone,
        "payload": {"number": phone, "text": text},
    }


# ---------------------------------------------------------------------------
# Subsidy Info → Formatted Text
# ---------------------------------------------------------------------------

def _format_subsidy_info(
    info: dict[str, Any],
    phone: str,
) -> dict[str, Any]:
    """Format subsidy eligibility as WhatsApp structured text."""
    check = "\u2705"
    cross = "\u274c"

    lines = ["*Subsidios habitacionales*\n"]
    lines.append(f"{check if info.get('mivivienda') else cross} MiVivienda")
    lines.append(f"{check if info.get('techo_propio') else cross} Techo Propio")
    lines.append(f"{check if info.get('bono_buen_pagador') else cross} Bono Buen Pagador")

    bono = info.get("bono_amount")
    if bono and bono > 0:
        lines.append(f"\nBono disponible: *S/ {_fmt_soles(bono)}*")

    return {
        "type": "text",
        "phone": phone,
        "payload": {"number": phone, "text": "\n".join(lines)},
    }


# ---------------------------------------------------------------------------
# Comparison Table → Media Gallery with Captions
# ---------------------------------------------------------------------------

def _format_comparison_gallery(
    comparison: list[dict[str, Any]],
    phone: str,
) -> list[dict[str, Any]]:
    """Format comparison table as sequential media messages with captions."""
    messages: list[dict[str, Any]] = []

    for project in comparison[:4]:
        slug = project.get("slug", "")
        name = project.get("name", "Proyecto")
        district = project.get("district", "")
        price = _fmt_soles(project.get("price_from", 0))
        delivery = project.get("delivery") or "Por confirmar"
        highlight = project.get("highlight", "")

        caption = f"*{name}* — {district}\nDesde S/ {price} · Entrega: {delivery}"
        if highlight:
            caption += f"\n{highlight}"

        hero_url = f"/images/projects/{slug}/hero.webp"

        messages.append({
            "type": "media",
            "phone": phone,
            "payload": {
                "number": phone,
                "mediatype": "image",
                "media": hero_url,
                "caption": caption,
            },
        })

    return messages


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt_soles(amount: float | int) -> str:
    """Format number as Peruvian soles (no decimals, comma thousands)."""
    return f"{int(round(amount)):,}"
