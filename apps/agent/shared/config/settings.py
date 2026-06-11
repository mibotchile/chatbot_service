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

    # ── Doris (Apache Doris / MySQL protocol) — real debt source for prestamype.
    # On any connection/query error the doris_debt_source falls back to the
    # seeded fixture (tenants/prestamype/mock/borrowers.json) so the demo never
    # breaks. Env prefix COBRANZA_ (e.g. COBRANZA_DORIS_HOST).
    doris_host: str = "127.0.0.1"
    doris_port: int = 9030
    doris_user: str = "root"
    doris_password: str = ""
    doris_db: str = "project_QUIdI0iwQY0l3pJwRKLB"

    # ── Doris analytics (WRITE) — bot interactions + LLM usage land here via the
    # pydoris Stream Load HTTP API. SEPARATE credentials (cobranza_rw) and the FE
    # *HTTP* port (8030, NOT the 9030 MySQL wire port the read path uses). If the
    # host is empty the analytics sink is a no-op (fire-and-forget never breaks
    # the chat). Env prefix COBRANZA_ (e.g. COBRANZA_ANALYTICS_HOST).
    analytics_host: str = ""
    analytics_port: int = 8030
    analytics_user: str = "cobranza_rw"
    analytics_password: str = ""
    analytics_db: str = "cobranza_analytics"

    csrf_secret: str = "dev-secret-change-in-prod"

    # Comprobante (payment voucher) storage. Uploaded images land under
    # <comprobante_dir>/<dni>/<nro_operacion>.<ext>; the dedup/audit JSON lives
    # here too (off /tmp so it persists in the mounted volume). In prod this
    # MUST be a mounted docker volume. Env: COBRANZA_COMPROBANTE_DIR.
    comprobante_dir: str = "/app/data/comprobantes"

    dashboard_key: str = ""

    webhook_lead_url: str = ""

    mail_api_url: str = (
        ""  # Internal SendGrid proxy: https://apiintranet.mibot.cl:8085/api/v2/mail_sengrid/send
    )
    notification_email: str = ""  # set per deployment (collections team inbox)

    whatsapp_api_url: str = ""  # Evolution API: http://mila_evolution:8080
    whatsapp_api_key: str = ""  # Evolution API key
    whatsapp_instance: str = ""  # Evolution instance name — legacy single-tenant
    whatsapp_webhook_url: str = (
        "http://agent:8000/api/v1/webhooks/whatsapp"  # URL Evolution sends webhooks to
    )

    # Multi-tenant WhatsApp: JSON mapping instance_name → tenant config
    # Format: {"instance-a": {"tenant_id": "demo", "mode": "all"},
    #          "instance-b": {"tenant_id": "clienteX", "mode": "website_leads_only"}}
    # mode: "all" = respond to everyone, "website_leads_only" = only respond if first
    #       message matches a trigger phrase (from website CTA links)
    whatsapp_tenants: str = "{}"

    daily_message_limit: int = 50  # per visitor/IP, resets at midnight

    # ── Hardened rate limiting (anti-abuse), all env COBRANZA_RL_* / COBRANZA_* ──
    # Defense is per *real client IP* (X-Forwarded-For first hop behind Traefik,
    # fallback connection IP). In-memory by default (fine for the single-container
    # staging deploy); if COBRANZA_REDIS_URL is set the design is ready to back
    # these counters with Redis later (not required now).
    #
    # Anti-enumeration of DNI (the identity gate is DNI-only → the top vector):
    #   · rate — max identification attempts per IP/hour;
    #   · diversity — > N DISTINCT DNIs/IP/hour ⇒ sweep ⇒ temporary block.
    rl_ident_per_hour: int = 6  # COBRANZA_RL_IDENT_PER_HOUR
    rl_distinct_dni_per_hour: int = 5  # COBRANZA_RL_DISTINCT_DNI_PER_HOUR
    rl_block_minutes: int = 15  # COBRANZA_RL_BLOCK_MINUTES (sweep block)
    # Short chat window (anti token-burn), on top of daily_message_limit.
    rl_chat_per_min: int = 12  # COBRANZA_RL_CHAT_PER_MIN
    # Upload cap (comprobantes/hour per IP).
    rl_upload_per_hour: int = 8  # COBRANZA_RL_UPLOAD_PER_HOUR
    # LLM spend cap per IP/day (USD). Accumulates the same cost_usd the analytics
    # sink records (config/pricing.compute_cost_usd). Over the cap ⇒ 429 until the
    # daily (UTC midnight) reset.
    daily_cost_cap_usd: float = 0.50  # COBRANZA_DAILY_COST_CAP_USD

    # Reverse-proxy path prefix (Traefik strip-prefix). Empty in local dev;
    # set to e.g. "/pubot-gj5w2a0p" behind the proxy so FastAPI builds correct
    # URLs and /docs works under the prefix. Env: COBRANZA_ROOT_PATH.
    root_path: str = ""

    # Public base URL for building externally-downloadable links (e.g. the
    # certificate PDF attached over WhatsApp, where there's no inbound request
    # to derive base_url from). In prod: https://demos.mibot.cl/pubot-gj5w2a0p
    # Env: COBRANZA_PUBLIC_BASE_URL.
    public_base_url: str = ""

    # Expose /docs, /redoc, /openapi.json. Default ON for local dev; set
    # COBRANZA_ENABLE_DOCS=false in prod to disable API surface enumeration.
    enable_docs: bool = True

    # Global CORS allowlist (always permitted, every tenant). The demo origin is
    # always allowed. Per-tenant embed origins (the client's own website domains)
    # are declared in each tenants/<id>/tenant.config.json under `embed_origins`
    # and merged on top of this at startup — see build_cors_origin_regex().
    cors_origins: list[str] = [
        "https://demos.mibot.cl",
    ]

    # ── ChatHub web publisher (camino C, Movistar pattern) — on web handoff Ada
    # pushes the visitor's last message into ChatHub's public incomingMessage
    # webhook so the asesor sees it in the panel. Fire-and-forget; DISABLED until
    # chathub_web_channel_id is set (the channel is registered in ChatHub apart).
    # Env prefix COBRANZA_ (e.g. COBRANZA_CHATHUB_WEB_CHANNEL_ID).
    chathub_webhook_url: str = "https://hook-whatsapp-prod.mibot.cl:5050/olimpo/incomingMessage"
    chathub_web_channel_id: str = ""  # empty ⇒ publisher is a no-op
    chathub_web_group: str = "1"  # destination queue/group identifier
    chathub_web_timeout: float = 10.0
    chathub_web_verify_ssl: bool = False

    # ── ChatHub OUTBOUND (envío de info por WhatsApp, REAL) — replaces Evolution.
    # When the URL is set + tenant is in prod (data_source=doris), enviar_info
    # delivers WhatsApp for real via ChatHub /messages/send. Empty ⇒ SIMULATED
    # (the current state: ChatHub needs the provisioned number + Firebase auth).
    # Env prefix COBRANZA_ (e.g. COBRANZA_CHATHUB_OUTBOUND_URL).
    chathub_outbound_url: str = ""
    chathub_outbound_token: str = ""  # Firebase/bearer token (when required)
    chathub_outbound_channel_id: str = ""  # ChatHub channel for the tenant number
    chathub_outbound_timeout: float = 10.0
    chathub_outbound_verify_ssl: bool = False

    # ── Layer-3 gestion inactivity sweep ──────────────────────────────────────
    # How often the sweep loop runs (seconds). Env: COBRANZA_GESTION_SWEEP_INTERVAL_SECONDS.
    gestion_sweep_interval_seconds: int = 300  # 5 minutes

    # Default per-tenant inactivity TTL (minutes) when tenant config does not
    # specify cobranza.gestion_inactivity_ttl_minutes.
    # Env: COBRANZA_GESTION_INACTIVITY_TTL_MINUTES.
    gestion_inactivity_ttl_minutes: int = 30

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
