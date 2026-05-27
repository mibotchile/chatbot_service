"""Sorelia configuration via pydantic-settings."""

from __future__ import annotations

import json

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # ── LLM provider selection (Strategy adapter; NOT LiteLLM) ──
    # COBRANZA_LLM_PROVIDER = "anthropic" (default) | "openai"
    llm_provider: str = "anthropic"

    anthropic_api_key: str = ""  # Default/fallback API key
    anthropic_model: str = "claude-haiku-4-5-20251001"

    # Per-tenant API keys: JSON {"tenant_id": "sk-ant-..."}
    # Falls back to anthropic_api_key if tenant not mapped
    anthropic_tenant_keys: str = "{}"

    # OpenAI (active only when llm_provider == "openai")
    openai_api_key: str = ""  # COBRANZA_OPENAI_API_KEY
    openai_model: str = "gpt-4o"

    database_url: str = "postgresql://nexo_ai:pass@localhost:5432/nexo"
    database_schema: str = "dev"

    meilisearch_url: str = "http://localhost:7700"
    meilisearch_api_key: str = ""

    redis_url: str = "redis://localhost:6379/6"

    csrf_secret: str = "dev-secret-change-in-prod"

    dashboard_key: str = ""

    webhook_lead_url: str = ""
    webhook_visit_url: str = ""
    webhook_brochure_url: str = ""

    mail_api_url: str = ""  # Internal SendGrid proxy: https://apiintranet.mibot.cl:8085/api/v2/mail_sengrid/send
    notification_email: str = ""  # set per deployment (collections team inbox)

    whatsapp_api_url: str = ""  # Evolution API: http://mila_evolution:8080
    whatsapp_api_key: str = ""  # Evolution API key
    whatsapp_instance: str = ""  # Evolution instance name — legacy single-tenant
    whatsapp_webhook_url: str = "http://agent:8000/api/v1/webhooks/whatsapp"  # URL Evolution sends webhooks to

    # Multi-tenant WhatsApp: JSON mapping instance_name → tenant config
    # Format: {"instance-a": {"tenant_id": "demo", "mode": "all"},
    #          "instance-b": {"tenant_id": "clienteX", "mode": "website_leads_only"}}
    # mode: "all" = respond to everyone, "website_leads_only" = only respond if first
    #       message matches a trigger phrase (from website CTA links)
    whatsapp_tenants: str = "{}"

    daily_message_limit: int = 50  # per visitor/IP, resets at midnight

    # Reverse-proxy path prefix (Traefik strip-prefix). Empty in local dev;
    # set to e.g. "/pubot-gj5w2a0p" behind the proxy so FastAPI builds correct
    # URLs and /docs works under the prefix. Env: COBRANZA_ROOT_PATH.
    root_path: str = ""

    # Public base URL for building externally-downloadable links (e.g. the
    # certificate PDF attached over WhatsApp, where there's no inbound request
    # to derive base_url from). In prod: https://demos.mibot.cl/pubot-gj5w2a0p
    # Env: COBRANZA_PUBLIC_BASE_URL.
    public_base_url: str = ""

    cors_origins: list[str] = [
        "http://localhost:4321",
        "http://localhost:4322",
        "http://localhost:3000",
        "http://localhost:8099",
        "https://demos.mibot.cl",
    ]

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="COBRANZA_",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()


def resolve_whatsapp_tenant(instance_name: str) -> dict | None:
    """Resolve tenant config from WhatsApp instance name.

    Returns dict with 'tenant_id' and 'mode', or None if not mapped.
    Falls back to legacy single-instance config.
    """
    tenants = json.loads(settings.whatsapp_tenants)
    if instance_name in tenants:
        return tenants[instance_name]
    # Legacy fallback: single instance maps to default tenant

    if instance_name == settings.whatsapp_instance:
        return {"tenant_id": "demo", "mode": "all"}
    return None


def resolve_api_key(tenant_id: str | None = None) -> str:
    """Resolve the API key for the ACTIVE provider.

    OpenAI uses a single key. Anthropic supports per-tenant keys
    (anthropic_tenant_keys JSON), falling back to anthropic_api_key.
    """
    if (settings.llm_provider or "anthropic").lower() == "openai":
        return settings.openai_api_key

    keys = json.loads(settings.anthropic_tenant_keys)
    if tenant_id and tenant_id in keys:
        return keys[tenant_id]
    # No fallback — each tenant must have its own key
    if keys:
        return next(iter(keys.values()))  # single-tenant shortcut
    return settings.anthropic_api_key
