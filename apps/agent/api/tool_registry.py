"""Concrete tool registry for the cobranza agent's function calling.

Lives in api/ because it imports from features/ (api→features is allowed).
The abstract interface (ToolRegistryPort + NullToolRegistry) lives in
shared/ports/tool_registry.py so feature modules can depend on the port
without creating a shared→features violation.

Routes tool names to implementations. Generic tools (quick replies, navigate,
contact form, lead status) are real engine tools. The cobranza domain tools
(consultar_deuda, registrar_reclamo, emitir_certificado_no_adeudo) live in
``features/cobranza/tools.py``.

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

from features.cobranza.debt_source import resolve_dni
from features.cobranza.tools import (
    consultar_deuda,
    emitir_certificado_no_adeudo,
    enviar_documento,
    enviar_info,
    registrar_reclamo,
)
from features.comprobantes.validator import validar_comprobante

# Default gated tools for the cobranza domain (hard_dni gate model).
# Sourced from COBRANZA_AGENT_TYPE.gated_tools at composition roots; this
# constant is the fallback when ToolRegistry is constructed without an
# explicit gated_tools param (e.g. unit tests, backward-compat callers).
# identificar_cliente is NOT here — it is the mechanism that OPENS the gate.
_DEFAULT_GATED_TOOLS: frozenset[str] = frozenset({
    "consultar_deuda",
    "registrar_reclamo",
    "emitir_certificado_no_adeudo",
    "enviar_documento",
    "enviar_info",
    "validar_comprobante",
})


class ToolRegistry:
    """Routes tool calls to implementations. Enforces the identity gate.

    Gate model (per-domain):
        gate_model='hard_dni' → tools in gated_tools are blocked until
        identity_verified=True. The gate set is passed via the gated_tools
        param (sourced from AgentTypeSpec.gated_tools at composition roots).
        Defaults to _DEFAULT_GATED_TOOLS (the cobranza set) for backward
        compatibility.

        identificar_cliente is NEVER in gated_tools — it is the tool that
        opens the gate.
    """

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
        on_identification_attempt: Callable[[str], Any] | None = None,
        # Envío de info bajo demanda (CORE, data-driven). ``deliverables`` is the
        # tenant's _deliverables spec; ``delivery_mode`` is "simulate" (demo/mock)
        # or "real" (prod/doris); ``chathub_outbound`` is the WhatsApp REAL client
        # (Evolution retired). All optional — feature is a no-op without them.
        deliverables: dict | None = None,
        delivery_mode: str = "simulate",
        chathub_outbound=None,
        # Per-domain gate (S5): set of tool names blocked until identity_verified.
        # Sourced from AgentTypeSpec.gated_tools at composition roots.
        # Default = _DEFAULT_GATED_TOOLS (cobranza set) for backward compat.
        gated_tools: frozenset[str] | None = None,
        # Per-domain tool surface (S5): controls which tools are registered.
        # Sourced from AgentTypeSpec.tools at composition roots.
        # Default = None (register all cobranza tools, current behavior).
        tools: tuple[str, ...] | None = None,
    ):
        self._lead_machine = lead_machine
        self._webhook_config = webhook_config
        self._visitor_memory = visitor_memory
        self._email_service = email_service
        self._whatsapp_service = whatsapp_service
        self._deliverables = deliverables or {}
        self._delivery_mode = (delivery_mode or "simulate").strip().lower()
        self._chathub_outbound = chathub_outbound
        # Identity gate state — injected server-side, never from the LLM.
        self._identity_verified = identity_verified
        self._debt_context = debt_context or {}
        self._download_base_url = download_base_url
        self._tenant_id = tenant_id
        # Callback to persist a mid-conversation DNI identification back to the
        # ConversationState (so the next turn starts already verified).
        self._on_identity_resolved = on_identity_resolved
        # Anti-enumeration hook: called with the typed DNI BEFORE resolution. If
        # it returns a decision with allowed=False, the attempt is rejected
        # WITHOUT touching the data source (rate / DNI-sweep protection). None in
        # contexts without rate limiting (e.g. unit tests, WhatsApp).
        self._on_identification_attempt = on_identification_attempt
        # Per-domain gate: tools blocked until identity_verified=True.
        # Defaults to _DEFAULT_GATED_TOOLS (cobranza set) when not explicitly set.
        self._gated_tools: frozenset[str] = (
            gated_tools if gated_tools is not None else _DEFAULT_GATED_TOOLS
        )
        _all_tools: dict[str, Any] = {
            # generic engine tools
            "get_debtor_status": self._get_debtor_status,
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
            "enviar_info": self._enviar_info,
            "validar_comprobante": self._validar_comprobante,
            "escalate_to_human": self._escalate_to_human,
        }
        # Per-domain tool surface: when tools= is provided, restrict _tools to
        # only the names declared for this agent type (preserving impl lookup).
        # Names not found in _all_tools are silently skipped (future tools).
        if tools is not None:
            tools_set = set(tools)
            self._tools: dict[str, Any] = {
                k: v for k, v in _all_tools.items() if k in tools_set
            }
        else:
            self._tools = _all_tools

    def has_tool(self, name: str) -> bool:
        return name in self._tools

    async def execute(self, name: str, args: dict) -> dict:
        if name not in self._tools:
            return {"error": f"Unknown tool: {name}"}
        # ── HARD GATE: no verified identity → gated tools never run ──
        if name in self._gated_tools and not self._identity_verified:
            return {
                "blocked": "identity_required",
                "message": (
                    "Para ver los datos de tu cuenta necesito identificarte. "
                    "Por favor, indícame tu número de DNI."
                ),
            }
        return await self._tools[name](**args)

    # ── Generic engine tools ────────────────────────────────────────────

    async def _get_debtor_status(self, conversation_id: str) -> dict:
        if self._lead_machine:
            return self._lead_machine.get_status()
        return {
            "level": "VISITOR",
            "collected": {},
            "missing": ["name", "phone", "email"],
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

        Anti-enumeration: the attempt is counted/checked BEFORE resolution. A
        rate or DNI-sweep violation short-circuits with a neutral message
        (no internal detail) and never queries the data source.
        """
        # ── Anti-enumeration gate (counts + checks BEFORE touching data) ──
        if self._on_identification_attempt is not None:
            decision = self._on_identification_attempt(dni)
            if decision is not None and not getattr(decision, "allowed", True):
                return {
                    "identified": False,
                    "reason": "rate_limited",
                    "retry_after": getattr(decision, "retry_after", 0),
                    "message": (
                        "Por seguridad, hiciste varios intentos en poco tiempo. "
                        "Espera un momento e inténtalo de nuevo, o escríbenos por "
                        "WhatsApp y un asesor te ayuda."
                    ),
                }
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

    async def _enviar_documento(self, tipo: str, destino: str = "", canal: str = "") -> dict:
        return await enviar_documento(
            self._debt_context, tipo, destino, canal,
            email_service=self._email_service,
            whatsapp_service=self._whatsapp_service,
            download_base_url=self._download_base_url,
        )

    async def _enviar_info(self, tipo: str = "", canal: str = "") -> dict:
        """Send a data-driven deliverable to the verified borrower's REGISTERED
        destination (email/phone), masked. ``tipo`` is a key in the tenant's
        _deliverables spec; ``canal`` ∈ {correo, whatsapp}. Demo (mock) simulates;
        prod (doris) sends for real. Identity/destination come from debt_context."""
        return await enviar_info(
            self._debt_context, tipo, canal,
            deliverables=self._deliverables,
            delivery_mode=self._delivery_mode,
            email_service=self._email_service,
            chathub_outbound=self._chathub_outbound,
        )

    async def _validar_comprobante(
        self,
        monto: float,
        nro_operacion: str,
        cuenta_destino: str | None = None,
        account_type: str = "cci",
        cci: str | None = None,
    ) -> dict:
        """Validate a payment voucher for the verified borrower (PrestamYpe).

        The credit/identity come from the verified ``debt_context``; only the
        voucher fields (account_type, cuenta_destino, monto, nro_operacion) come
        from the user. ``cci`` is accepted as a legacy alias for
        ``cuenta_destino``.
        """
        return await validar_comprobante(
            self._debt_context,
            monto=monto,
            nro_operacion=nro_operacion,
            cuenta_destino=cuenta_destino,
            account_type=account_type,
            cci=cci,
        )

    async def _escalate_to_human(self, reason: str = "Cliente solicitó hablar con un asesor") -> dict:
        """Derive to a human collections agent (demo: acknowledged, not routed)."""
        return {
            "escalated": True,
            "reason": reason,
            "message": "Te derivo con un asesor. En breve se comunicarán contigo.",
        }
