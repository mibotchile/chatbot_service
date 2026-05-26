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

from typing import Any, Callable

from integrations.mock_debt_source import resolve_dni
from tools.cobranza import (
    consultar_deuda,
    emitir_certificado_no_adeudo,
    enviar_documento,
    registrar_reclamo,
)

# Tools that require a verified identity before they may execute.
# identificar_cliente is NOT gated — it is the mechanism that OPENS the gate.
_GATED_TOOLS = {
    "consultar_deuda",
    "registrar_reclamo",
    "emitir_certificado_no_adeudo",
    "enviar_documento",
}


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
        tenant_id: str = "prestaunion",
        on_identity_resolved: Callable[[dict], None] | None = None,
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
        self._tenant_id = tenant_id
        # Callback to persist a mid-conversation DNI identification back to the
        # ConversationState (so the next turn starts already verified).
        self._on_identity_resolved = on_identity_resolved
        self._tools: dict[str, Any] = {
            # generic engine tools
            "get_lead_status": self._get_lead_status,
            "navigate_page": self._navigate_page,
            "suggest_quick_replies": self._suggest_quick_replies,
            "collect_contact_info": self._collect_contact_info,
            # identity (NOT gated — this is what opens the gate)
            "identificar_cliente": self._identificar_cliente,
            # cobranza domain tools (gated)
            "consultar_deuda": self._consultar_deuda,
            "registrar_reclamo": self._registrar_reclamo,
            "emitir_certificado_no_adeudo": self._emitir_certificado_no_adeudo,
            "enviar_documento": self._enviar_documento,
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
                    "Para ver los datos de tu cuenta necesito identificarte. "
                    "Por favor, indícame tu número de DNI."
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

    # ── Identity (NOT gated): DNI-first identification ──────────────────

    async def _identificar_cliente(self, dni: str) -> dict:
        """Resolve a DNI to a verified profile and OPEN the gate (server-side).

        The DNI is the value the USER typed; the account_id is resolved
        server-side from the fixture, never dictated by the LLM. On success it
        mutates this registry's identity (so gated tools work in the same loop)
        and persists via the callback (so the next turn stays verified).
        """
        profile = resolve_dni(dni, tenant_id=self._tenant_id)
        if not profile:
            return {
                "identified": False,
                "reason": "dni_not_found",
                "message": (
                    "No encontré una cuenta con ese DNI. Verifica el número "
                    "(son 8 dígitos) o, si prefieres, te derivo con un asesor."
                ),
            }
        # Open the gate for this request and persist for the next turn.
        self._identity_verified = True
        self._debt_context = profile
        if self._on_identity_resolved:
            self._on_identity_resolved(profile)
        return {
            "identified": True,
            "borrower_name": profile.get("borrower_name"),
            "business_name": profile.get("business_name"),
            "status_label": profile.get("status_label"),
            "message": (
                f"¡Gracias, {profile.get('borrower_name', '').split(' ')[0]}! "
                f"Ya verifiqué tu identidad. ¿En qué te ayudo con tu préstamo?"
            ),
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

    async def _enviar_documento(self, tipo: str, canal: str) -> dict:
        return await enviar_documento(
            self._debt_context, tipo, canal,
            email_service=self._email_service,
            whatsapp_service=self._whatsapp_service,
            download_base_url=self._download_base_url,
        )

    async def _escalate_to_human(self, reason: str) -> dict:
        """Derive to a human collections agent (demo: acknowledged, not routed)."""
        return {
            "escalated": True,
            "reason": reason,
            "message": "Te derivo con un asesor de PrestaUnion. En breve se comunicarán contigo.",
        }
