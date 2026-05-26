"""Tool registry for the cobranza agent's function calling.

Routes tool names to implementations. Generic tools (quick replies, navigate,
contact form, lead status) are real. Cobranza domain tools are STUBS (Fase 1).

The constructor keeps the engine's service-injection signature so api/main.py
wires it unchanged. Real-estate-only collaborators (meilisearch, visit_manager,
google_calendar) are accepted but ignored — kept for engine compatibility.
"""

from typing import Any

from tools.debt import get_account_status, get_debt_detail, get_payment_channels
from tools.payment import (
    check_discount_eligibility,
    register_payment_promise,
    simulate_payment_plan,
)


class ToolRegistry:
    """Routes tool calls to implementations. Accepts optional service clients."""

    def __init__(
        self,
        meilisearch_client=None,  # ignored (real-estate search; kept for compat)
        lead_machine=None,
        webhook_config=None,
        visitor_memory=None,
        email_service=None,
        whatsapp_service=None,
        visit_manager=None,  # ignored (real-estate visits; kept for compat)
        google_calendar=None,  # ignored (real-estate visits; kept for compat)
    ):
        self._lead_machine = lead_machine
        self._webhook_config = webhook_config
        self._visitor_memory = visitor_memory
        self._email_service = email_service
        self._whatsapp_service = whatsapp_service
        self._tools: dict[str, Any] = {
            # generic engine tools
            "get_lead_status": self._get_lead_status,
            "navigate_page": self._navigate_page,
            "suggest_quick_replies": self._suggest_quick_replies,
            "collect_contact_info": self._collect_contact_info,
            # cobranza domain tools (stubs)
            "get_debt_detail": self._get_debt_detail,
            "get_account_status": self._get_account_status,
            "get_payment_channels": self._get_payment_channels,
            "simulate_payment_plan": self._simulate_payment_plan,
            "check_discount_eligibility": self._check_discount_eligibility,
            "register_payment_promise": self._register_payment_promise,
            "escalate_to_human": self._escalate_to_human,
        }

    def has_tool(self, name: str) -> bool:
        return name in self._tools

    async def execute(self, name: str, args: dict) -> dict:
        if name not in self._tools:
            return {"error": f"Unknown tool: {name}"}
        return await self._tools[name](**args)

    # ── Generic engine tools ────────────────────────────────────────────

    async def _get_lead_status(self, conversation_id: str) -> dict:
        if self._lead_machine:
            return self._lead_machine.get_status()
        return {"level": "VISITOR", "collected": {}, "missing": ["name", "phone", "email"], "opportunities": []}

    async def _navigate_page(self, scroll_to: str, highlight: str = "") -> dict:
        return {"scroll_to": scroll_to, "highlight": highlight or None}

    async def _suggest_quick_replies(self, options: list[str]) -> dict:
        """Pass-through validation for now. TODO Fase 1: validate against real
        cobranza data (channels, plan options) like sorelia did with projects."""
        validated = [o for o in options[:4] if o and o.strip()]
        if not validated:
            validated = options[:3]
        return {"options": validated, "validated": True}

    async def _collect_contact_info(self, form_type: str = "contact") -> dict:
        """Return a form structure for the frontend to render inline."""
        forms = {
            "contact": {
                "form_id": "contact-capture",
                "title": "Datos de contacto",
                "sections": [{
                    "section_id": "contact",
                    "title": "",
                    "fields": [
                        {"field_id": "name", "label": "Nombre", "type": "text", "required": True},
                        {"field_id": "email", "label": "Correo", "type": "email", "required": False},
                        {"field_id": "phone", "label": "Telefono", "type": "tel", "required": True},
                    ],
                }],
            },
            # TODO Fase 2: identity verification form (document + control data)
            "identity": {
                "form_id": "identity-check",
                "title": "Verificacion de identidad",
                "sections": [{
                    "section_id": "identity",
                    "title": "",
                    "fields": [
                        {"field_id": "document_number", "label": "Documento", "type": "text", "required": True},
                    ],
                }],
            },
        }
        return forms.get(form_type, forms["contact"])

    # ── Cobranza domain tools (stubs) ───────────────────────────────────

    async def _get_debt_detail(self, account_id: str) -> dict:
        return await get_debt_detail(account_id)

    async def _get_account_status(self, account_id: str) -> dict:
        return await get_account_status(account_id)

    async def _get_payment_channels(self, account_id: str = "") -> dict:
        return await get_payment_channels(account_id)

    async def _simulate_payment_plan(self, account_id: str, installments: int | None = None) -> dict:
        return await simulate_payment_plan(account_id, installments)

    async def _check_discount_eligibility(self, account_id: str) -> dict:
        return await check_discount_eligibility(account_id)

    async def _register_payment_promise(self, account_id: str, amount: float, promise_date: str) -> dict:
        return await register_payment_promise(account_id, amount, promise_date)

    async def _escalate_to_human(self, reason: str) -> dict:
        """TODO Fase 1: route to a human collections agent (queue/handoff)."""
        return {"escalated": False, "reason": reason, "todo": "escalate_to_human not implemented — Fase 1"}
