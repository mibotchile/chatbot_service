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
# S5 will read this from here; for now it documents the cobranza tool surface.
COBRANZA_TOOLS: tuple[str, ...] = (
    "consultar_deuda",
    "validar_comprobante",
    "registrar_reclamo",
    "emitir_certificado_no_adeudo",
    "enviar_documento",
    "subir_comprobante",
)

COBRANZA_AGENT_TYPE = AgentTypeSpec(
    capture_spec=COBRANZA_SPEC,
    tools=COBRANZA_TOOLS,
    skills=None,  # tenant-level skill overrides apply on top
    gate_model="hard_dni",
    projection_table="debtors",
)
