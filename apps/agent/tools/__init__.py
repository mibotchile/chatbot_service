"""Tool registry for the cobranza agent's function calling.

Routes tool names to implementations. Generic tools (quick replies, navigate,
contact form, lead status) are real engine tools. The cobranza domain tools
(consultar_deuda, registrar_reclamo, emitir_certificado_no_adeudo) live in
``tools/cobranza.py``.

SECURITY — hard identity gate (non-negotiable):
  - The registry is constructed with ``identity_verified`` + ``debt_context``
    (the verified borrower profile resolved server-side from the campaign
    token). The LLM never sees or dictates ``account_id``.
  - If ``identity_verified`` is False, every cobranza tool short-circuits to
    ``{"blocked": "identity_required"}`` WITHOUT touching any data. This is the
    real defense — the prompt is only the soft layer.

The constructor keeps the engine's service-injection signature so api/main.py
wires it unchanged. Real-estate-only collaborators (meilisearch, visit_manager,
google_calendar) are accepted but ignored — kept for engine compatibility.
"""

from typing import Any

from tools.cobranza import (
    consultar_deuda,
    emitir_certificado_no_adeudo,
    registrar_reclamo,
)

# Tools that require a verified identity before they may execute.
_GATED_TOOLS = {"consultar_deuda", "registrar_reclamo", "emitir_certificado_no_adeudo"}


class ToolRegistry:
    """Routes tool calls to implementations. Enforces the identity gate."""

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
        *,
        identity_verified: bool = False,
        debt_context: dict | None = None,
        download_base_url: str = "",
    ):
        self._lead_machine = lead_machine
        self._webhook_config = webhook_config
        self._visitor_memory = visitor_memory
        self._email_service = email_service
        self._whatsapp_service = whatsapp_service
        # Identity gate state — injected server-side, never from the LLM.
        self._identity_verified = identity_verified
        self._debt_context = debt_context or {}
        self._download_base_url = download_base_url
        self._tools: dict[str, Any] = {
            # generic engine tools
            "get_lead_status": self._get_lead_status,
            "navigate_page": self._navigate_page,
            "suggest_quick_replies": self._suggest_quick_replies,
            "collect_contact_info": self._collect_contact_info,
            # cobranza domain tools (gated)
            "consultar_deuda": self._consultar_deuda,
            "registrar_reclamo": self._registrar_reclamo,
            "emitir_certificado_no_adeudo": self._emitir_certificado_no_adeudo,
            "escalate_to_human": self._escalate_to_human,
        }

    def has_tool(self, name: str) -> bool:
        return name in self._tools

    async def execute(self, name: str, args: dict) -> dict:
        if name not in self._tools:
            return {"error": f"Unknown tool: {name}"}
        # ── HARD GATE: no verified identity → gated tools never run ──
        if name in _GATED_TOOLS and not self._identity_verified:
            return {
                "blocked": "identity_required",
                "message": (
                    "Para consultar datos de la cuenta necesito que ingrese por "
                    "el enlace seguro que le enviamos. Sin ese enlace no puedo "
                    "mostrar información de su préstamo."
                ),
            }
        return await self._tools[name](**args)

    # ── Generic engine tools ────────────────────────────────────────────

    async def _get_lead_status(self, conversation_id: str) -> dict:
        if self._lead_machine:
            return self._lead_machine.get_status()
        return {
            "level": "VISITOR",
            "collected": {},
            "missing": ["name", "phone", "email"],
            "opportunities": [],
        }

    async def _navigate_page(self, scroll_to: str, highlight: str = "") -> dict:
        return {"scroll_to": scroll_to, "highlight": highlight or None}

    async def _suggest_quick_replies(self, options: list[str]) -> dict:
        validated = [o for o in options[:4] if o and o.strip()]
        if not validated:
            validated = options[:3]
        return {"options": validated, "validated": True}

    async def _collect_contact_info(self, form_type: str = "contact") -> dict:
        """Return a form structure for the frontend to render inline.

        In the demo, identity is resolved by token — this is only used for the
        cold (unverified) channel to offer a contact fallback.
        """
        return {
            "form_id": "contact-capture",
            "title": "Datos de contacto",
            "sections": [{
                "section_id": "contact",
                "title": "",
                "fields": [
                    {"field_id": "name", "label": "Nombre", "type": "text", "required": True},
                    {"field_id": "phone", "label": "Telefono", "type": "tel", "required": True},
                ],
            }],
        }

    # ── Cobranza domain tools (identity-gated; profile injected server-side) ──

    async def _consultar_deuda(self) -> dict:
        return await consultar_deuda(self._debt_context)

    async def _registrar_reclamo(self, tipo: str, descripcion: str) -> dict:
        return await registrar_reclamo(self._debt_context, tipo, descripcion)

    async def _emitir_certificado_no_adeudo(self) -> dict:
        return await emitir_certificado_no_adeudo(
            self._debt_context, download_base_url=self._download_base_url
        )

    async def _escalate_to_human(self, reason: str) -> dict:
        """Derive to a human collections agent (demo: acknowledged, not routed)."""
        return {
            "escalated": True,
            "reason": reason,
            "message": "Le derivo con un asesor de PrestaUnion. En breve se comunicarán con usted.",
        }
