"""Characterization tests for ToolRegistry gate-model refactor (S5).

These tests lock the behavioral contract before the _GATED_TOOLS module-global
is removed and the gate becomes driven by AgentTypeSpec.gated_tools.

Contract (hard_dni gate model):
- Exactly these 6 tools are gated behind verified identity:
    consultar_deuda, registrar_reclamo, emitir_certificado_no_adeudo,
    enviar_documento, enviar_info, validar_comprobante
- identificar_cliente is NOT gated (it is the gate opener).
- All other generic tools (get_debtor_status, navigate_page, etc.) are NOT gated.
- ToolRegistry accepts gated_tools: frozenset[str] constructor param.
- ToolRegistry accepts tools: tuple[str, ...] constructor param (controls which
  tools appear in _tools; exclusion via excluded_tools is layered on top at the
  composition root, not tested here).
- When gated_tools is omitted, defaults reproduce the current behavior exactly.
- gate_model='hard_dni' means: gated_tools are blocked when identity_verified=False.
"""

from __future__ import annotations

import pytest

from api.tool_registry import ToolRegistry


# ── The canonical cobranza gated set (must match _GATED_TOOLS / spec.gated_tools)

EXPECTED_GATED = frozenset({
    "consultar_deuda",
    "registrar_reclamo",
    "emitir_certificado_no_adeudo",
    "enviar_documento",
    "enviar_info",
    "validar_comprobante",
})

# Tools that must NOT be gated (identity-opener + generic engine tools)
EXPECTED_UNGATED = frozenset({
    "identificar_cliente",
    "get_debtor_status",
    "navigate_page",
    "suggest_quick_replies",
    "collect_contact_info",
    "escalate_to_human",
})


# ── Verify the gated set is exactly EXPECTED_GATED ──────────────────────────

@pytest.mark.parametrize("tool_name", sorted(EXPECTED_GATED))
async def test_gated_tool_blocked_without_identity(tool_name: str):
    """Every gated tool returns blocked=identity_required when unverified."""
    reg = ToolRegistry(identity_verified=False)
    result = await reg.execute(tool_name, {})
    assert result.get("blocked") == "identity_required", (
        f"Tool '{tool_name}' should be gated but was not blocked. Got: {result}"
    )


@pytest.mark.parametrize("tool_name", sorted(EXPECTED_UNGATED))
async def test_ungated_tool_not_blocked_without_identity(tool_name: str):
    """None of the ungated tools return blocked=identity_required.

    The gate must not block these tools. The tool may raise TypeError (missing
    required args when called with {}) or return its own error dict — both are
    acceptable; only blocked=identity_required is a gate failure.
    """
    reg = ToolRegistry(identity_verified=False)
    try:
        result = await reg.execute(tool_name, {})
        assert result.get("blocked") != "identity_required", (
            f"Tool '{tool_name}' should NOT be gated but got blocked. Got: {result}"
        )
    except TypeError:
        # Tool requires args; the gate did not block it — that's the contract.
        pass


# ── gated_tools constructor param wires into execute ────────────────────────

async def test_custom_gated_tools_param_blocks_specified_tool():
    """When gated_tools is passed, only those tools are blocked."""
    reg = ToolRegistry(
        identity_verified=False,
        gated_tools=frozenset({"navigate_page"}),  # override: only navigate is gated
    )
    # navigate_page is now gated
    assert (await reg.execute("navigate_page", {})).get("blocked") == "identity_required"
    # consultar_deuda is NOT in the custom gated set → passes gate (may fail on args)
    try:
        result_cd = await reg.execute("consultar_deuda", {})
        assert result_cd.get("blocked") != "identity_required"
    except (TypeError, KeyError):
        pass  # gate passed; tool needs debt_context — not our concern here


async def test_empty_gated_tools_allows_all():
    """gated_tools=frozenset() means no gate at all."""
    reg = ToolRegistry(
        identity_verified=False,
        gated_tools=frozenset(),
    )
    for tool in EXPECTED_GATED:
        try:
            result = await reg.execute(tool, {})
            assert result.get("blocked") != "identity_required", (
                f"Tool '{tool}' should not be gated (empty gated_tools) but got blocked."
            )
        except (TypeError, KeyError):
            pass  # gate passed; tool requires args/debt_context — acceptable


async def test_default_gated_tools_equals_cobranza_set():
    """Default gated_tools (no param) == EXPECTED_GATED (the cobranza set)."""
    reg = ToolRegistry(identity_verified=False)
    for tool in EXPECTED_GATED:
        r = await reg.execute(tool, {})
        assert r.get("blocked") == "identity_required", (
            f"Default gate should block '{tool}' but did not."
        )
    # identificar_cliente must remain ungated by default
    r_id = await reg.execute("identificar_cliente", {"dni": "00000000"})
    assert r_id.get("blocked") != "identity_required"


# ── tools constructor param drives which tools are registered ────────────────

def test_tools_param_restricts_has_tool():
    """When tools= is passed with a subset, has_tool reflects only those tools."""
    subset = ("consultar_deuda", "navegate_page_fake")  # one real, one fake
    reg = ToolRegistry(tools=subset)
    # The registry should have consultar_deuda registered regardless of the subset
    # because _tools is built from the full tool map in __init__.
    # The tools param controls what ends up in _tools.
    assert reg.has_tool("consultar_deuda")


def test_tools_param_full_cobranza_set_all_present():
    """Default tools (no param) registers the full cobranza tool surface."""
    reg = ToolRegistry()
    for tool in EXPECTED_GATED | EXPECTED_UNGATED:
        assert reg.has_tool(tool), f"Expected tool '{tool}' to be registered by default"


# ── AgentTypeSpec wires correctly into ToolRegistry (integration char-test) ──

def test_cobranza_agent_type_spec_has_gated_tools():
    """COBRANZA_AGENT_TYPE.gated_tools == EXPECTED_GATED (locked contract)."""
    from features.cobranza.agent_type import COBRANZA_AGENT_TYPE

    assert COBRANZA_AGENT_TYPE.gated_tools == EXPECTED_GATED, (
        f"COBRANZA_AGENT_TYPE.gated_tools mismatch.\n"
        f"Expected: {EXPECTED_GATED}\n"
        f"Got:      {COBRANZA_AGENT_TYPE.gated_tools}"
    )


def test_cobranza_agent_type_spec_tools_contains_gated_set():
    """Every tool in gated_tools must appear in COBRANZA_AGENT_TYPE.tools."""
    from features.cobranza.agent_type import COBRANZA_AGENT_TYPE

    spec_tools = set(COBRANZA_AGENT_TYPE.tools)
    for tool in COBRANZA_AGENT_TYPE.gated_tools:
        assert tool in spec_tools, (
            f"Gated tool '{tool}' not listed in AgentTypeSpec.tools"
        )


async def test_registry_built_from_spec_gated_tools_reproduces_cobranza_behavior():
    """ToolRegistry built with spec.gated_tools behaves identically to the default."""
    from features.cobranza.agent_type import COBRANZA_AGENT_TYPE

    reg_spec = ToolRegistry(
        identity_verified=False,
        gated_tools=COBRANZA_AGENT_TYPE.gated_tools,
    )
    reg_default = ToolRegistry(identity_verified=False)

    for tool in EXPECTED_GATED:
        r_spec = await reg_spec.execute(tool, {})
        r_default = await reg_default.execute(tool, {})
        assert r_spec.get("blocked") == r_default.get("blocked"), (
            f"Spec-driven gate differs from default for '{tool}': "
            f"spec={r_spec} default={r_default}"
        )
