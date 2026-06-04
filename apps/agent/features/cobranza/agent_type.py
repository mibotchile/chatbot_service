"""COBRANZA_AGENT_TYPE — cobranza AgentTypeSpec descriptor.

This module declares the single cobranza entry for the AgentTypeRegistry.
It lives in features/cobranza/ (its bounded context) and is wired into the
registry at the composition root (api/main.py via tenancy/agent_types/registry).

Dependency direction: features/cobranza → shared/ports (allowed).
"""

from __future__ import annotations

from shared.ports.agent_type_registry import AgentTypeSpec
from features.cobranza.debtor import COBRANZA_SPEC

# Full ordered tool list for the cobranza domain (matches existing ToolRegistry).
COBRANZA_TOOLS: tuple[str, ...] = (
    "get_debtor_status",
    "navigate_page",
    "suggest_quick_replies",
    "collect_contact_info",
    "identificar_cliente",
    "consultar_deuda",
    "registrar_reclamo",
    "emitir_certificado_no_adeudo",
    "enviar_documento",
    "enviar_info",
    "validar_comprobante",
    "escalate_to_human",
)

# Tools that require a verified identity before they may execute.
# identificar_cliente is NOT gated — it is the mechanism that OPENS the gate.
# This must match _GATED_TOOLS in api/tool_registry.py exactly; S5 removes
# the module-global and sources this set from here instead.
COBRANZA_GATED_TOOLS: frozenset[str] = frozenset({
    "consultar_deuda",
    "registrar_reclamo",
    "emitir_certificado_no_adeudo",
    "enviar_documento",
    "enviar_info",
    "validar_comprobante",
})

COBRANZA_AGENT_TYPE = AgentTypeSpec(
    capture_spec=COBRANZA_SPEC,
    tools=COBRANZA_TOOLS,
    gated_tools=COBRANZA_GATED_TOOLS,
    skills=None,  # tenant-level skill overrides apply on top
    gate_model="hard_dni",
    projection_table="debtors",
)
