"""Fase 0 smoke tests: the app imports and a bare tenant loads.

No cobranza logic is exercised here — only that the scaffold is wireable.
"""

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TENANTS = _REPO_ROOT / "tenants"


def test_app_imports():
    """The FastAPI app object imports without ImportError."""
    from api.main import app

    assert app is not None
    assert app.title == "Sorelia API"  # engine product title (unchanged)


def test_empty_tenant_loads():
    """The _template tenant loads via the engine loader with empty knowledge."""
    from tenancy.tenant_loader import TenantConfig

    tenant = TenantConfig.from_directory(_TENANTS / "_template")

    assert tenant.slug == "_template"
    # No knowledge JSONs in the template → engine degrades to empty.
    assert tenant.faq == []
    assert tenant.financial == {}
    assert tenant.sales_arsenal == {}
    # Soul comes through with cobranza defaults / tenant overrides.
    assert tenant.soul.role == "agente de cobranza"
    assert tenant.soul.company == "Entidad Demo"
    # Guardrails override from the template is picked up.
    assert "deudor" in tenant.guardrails.lower()


def test_tool_registry_has_cobranza_tools():
    """ToolRegistry exposes generic + the 3 consolidated cobranza tools."""
    from api.tool_registry import ToolRegistry

    reg = ToolRegistry()
    assert reg.has_tool("consultar_deuda")
    assert reg.has_tool("registrar_reclamo")
    assert reg.has_tool("emitir_certificado_no_adeudo")
    assert reg.has_tool("suggest_quick_replies")
    # Old Fase-0 stubs and real-estate tools must be gone.
    assert not reg.has_tool("get_debt_detail")
    assert not reg.has_tool("simulate_payment_plan")
    assert not reg.has_tool("search_properties")


def test_lead_machine_uses_cobranza_fields():
    """The lead machine tracks cobranza interest fields, not real-estate ones."""
    from features.cobranza.debtor import INTEREST_FIELDS

    assert "debt_amount" in INTEREST_FIELDS
    assert "account_id" in INTEREST_FIELDS
    assert "district" not in INTEREST_FIELDS


def test_system_prompt_builds_with_empty_state():
    """build_system_prompt runs with empty lead/page context (no KB present)."""
    from features.conversation.prompts import build_system_prompt

    prompt = build_system_prompt(debtor_state={}, page_context={})
    assert "IDENTIDAD" in prompt
    assert "agente de cobranza" in prompt


def test_tool_name_is_get_debtor_status():
    """The registered tool name and schema name must be get_debtor_status (LLM contract)."""
    from api.tool_registry import ToolRegistry
    from shared.config.tools_schema import TOOL_DEFINITIONS

    # Schema name
    schema_names = [t["name"] for t in TOOL_DEFINITIONS]
    assert "get_debtor_status" in schema_names, "get_debtor_status missing from TOOL_DEFINITIONS"
    assert "get_lead_status" not in schema_names, "old get_lead_status must be removed from schema"

    # Registry dispatch key
    reg = ToolRegistry()
    assert reg.has_tool("get_debtor_status"), "ToolRegistry must dispatch get_debtor_status"
    assert not reg.has_tool("get_lead_status"), "old get_lead_status key must be removed"
