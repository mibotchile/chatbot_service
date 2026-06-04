"""Tenant configuration loader — multi-tenant whitelabel support.

Each tenant gets isolated identity, knowledge, and branding.
Tenants are loaded from directories or constructed programmatically.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from tenancy.soul import AgentSoul
from tenancy.responses_spec import ResponsesSpec


@dataclass
class TenantConfig:
    """Bundle of tenant-specific configuration and knowledge."""

    slug: str
    soul: AgentSoul
    project_uid: str | None = None
    skills: list[str] | None = None
    excluded_tools: list[str] | None = None
    faq: list[dict] = field(default_factory=list)
    financial: dict = field(default_factory=dict)
    sales_arsenal: dict = field(default_factory=dict)
    company: dict = field(default_factory=dict)
    guardrails: str = ""
    # Agent type — determines which AgentTypeSpec (tools, gate, spec) to load.
    # Default "cobranza" keeps zero behavior change for all existing tenants.
    agent_type: str = "cobranza"
    # Curated-responses feature (tenant-agnostic). ``response_mode`` is the flag;
    # ``responses`` is the loaded responses.json spec (empty when the tenant has
    # none → mode degrades to "llm", current behavior, nothing breaks).
    response_mode: str = "llm"
    responses: ResponsesSpec = field(default_factory=ResponsesSpec)

    @classmethod
    def from_directory(cls, tenant_dir: str | Path) -> TenantConfig:
        """Load tenant config from a directory.

        Expected structure::

            tenant_dir/
                tenant.config.json
                knowledge/
                    faq.json
                    sales_arsenal.json
                    financial_literacy.json
                    company.json
                guardrails.md          (optional — overrides base)
        """
        tenant_dir = Path(tenant_dir)
        config = json.loads((tenant_dir / "tenant.config.json").read_text())
        knowledge_dir = tenant_dir / "knowledge"

        soul = AgentSoul.from_tenant_config(config)

        def _load_json(name: str, key: str | None = None) -> dict | list:
            path = knowledge_dir / name
            if not path.exists():
                return [] if key else {}
            data = json.loads(path.read_text())
            return data.get(key, []) if key else data

        faq = _load_json("faq.json", "faqs")
        financial = _load_json("financial_literacy.json")
        sales_arsenal = _load_json("sales_arsenal.json")
        company = _load_json("company.json")

        guardrails = ""
        guardrails_path = tenant_dir / "guardrails.md"
        if guardrails_path.exists():
            guardrails = guardrails_path.read_text()

        # Per-tenant skill and tool overrides
        agent_cfg = config.get("agent", {})
        skills = agent_cfg.get("skills", None)
        excluded_tools = agent_cfg.get("excluded_tools", None)

        # Agent type — defaults to "cobranza" when not declared (zero-change for
        # all existing tenants). Future tenants declare agent_type in config.
        agent_type = (config.get("agent_type") or "cobranza").strip().lower()

        # Curated-responses feature: flag from tenant.config.json + responses.json.
        # Default "llm" (current behavior) when the tenant ships neither.
        response_mode = (config.get("response_mode") or "llm").strip().lower()
        responses = ResponsesSpec.from_dir(tenant_dir, response_mode=response_mode)

        return cls(
            slug=config.get("slug", tenant_dir.name),
            soul=soul,
            project_uid=config.get("project_uid"),
            skills=skills,
            excluded_tools=excluded_tools,
            faq=faq if isinstance(faq, list) else [],
            financial=financial if isinstance(financial, dict) else {},
            sales_arsenal=sales_arsenal if isinstance(sales_arsenal, dict) else {},
            company=company if isinstance(company, dict) else {},
            guardrails=guardrails,
            agent_type=agent_type,
            response_mode=response_mode,
            responses=responses,
        )
