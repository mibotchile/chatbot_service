"""Sorelia FastAPI application — thin orchestration layer.

Responsibilities:
  - App object creation (FastAPI + lifespan + middleware + CORS)
  - Lifespan: initialize DB pool, services, and update singleton module globals
  - Router includes (dashboard, chathub, security, conversations, cobranza, webhooks)
  - Mutable singletons (store, visitor_memory, email_service, whatsapp_service, ...)
    live here so the lifespan can update them and routers can read via late import.

Business logic lives in api/routers/*.py.
Middleware helpers live in api/middleware.py.
Pure helpers (tenant, analytics, startup) live in api/wiring.py.

Routers use ``import api.main as m`` to access singletons and helpers — all names
are available directly or re-exported here.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from dotenv import load_dotenv
load_dotenv()

import json as _json
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from loguru import logger

from shared.config.settings import settings
from features.conversation.persistence.state import get_store as _get_store_impl
from features.cobranza.debtor import COBRANZA_SPEC as _COBRANZA_SPEC


def get_store(
    redis_url: str | None = None,
    db_pool=None,
    db_schema: str = "dev",
    capture_spec=None,
):
    """Composition-root wrapper: defaults capture_spec to COBRANZA_SPEC."""
    return _get_store_impl(
        redis_url=redis_url,
        db_pool=db_pool,
        db_schema=db_schema,
        capture_spec=capture_spec if capture_spec is not None else _COBRANZA_SPEC,
    )
from features.conversation.persistence.visitor_memory import VisitorMemory
from shared.ports.agent_type_registry import AgentTypeRegistry, AgentTypeSpec  # noqa: F401
from tenancy.agent_types.registry import InCodeAgentTypeRegistry
from features.cobranza.agent_type import COBRANZA_AGENT_TYPE
from shared.delivery.email_delivery import EmailService
from features.messaging.whatsapp_service import WhatsAppService
from features.messaging.chathub_outbound import ChathubOutboundClient
from features.analytics.dashboard import dashboard_router
from api.routers.chathub import chathub_router
from api.routers.security import router as security_router
from api.routers.conversations import router as conversations_router
from api.routers.cobranza import router as cobranza_router
from api.routers.webhooks import router as webhooks_router

# Re-export middleware helpers — tests and routers access these as ``m.X``.
from shared.llm import build_llm_provider  # noqa: F401
from api.middleware import (  # noqa: F401
    _CSRF_SECRET,
    _request_log,
    _client_ip,
    _check_ip_daily_limit,
    _increment_ip_daily_count,
    RateLimitMiddleware,
    SecurityHeadersMiddleware,
    rate_limiter,
    _too_many_requests,
    _LIMIT_MSG_CHAT,
    _LIMIT_MSG_COST,
    _LIMIT_MSG_UPLOAD,
    _LIMIT_MSG_GENERIC,
    _generate_csrf_token,
    _validate_csrf_token,
    _generate_session_token,
    _verify_session_token,
    _SESSION_TOKEN_MAX_AGE,
)

# Re-export pure helpers from wiring — routers access these as ``m.X``.
from api.wiring import (  # noqa: F401
    get_tenant_contact_phone,
    _tenant_dir,
    _load_tenant_config,
    _tenant_project_uid,
    _delivery_for,
    _fallback_response,
    _analytics_tasks,
    _emit_analytics,
    _spawn_analytics,
    _register_whatsapp_webhook,
)

# ---------------------------------------------------------------------------
# Mutable singletons — lifespan updates these; routers read via ``m.X``.
# These MUST live in this module so attribute writes in lifespan are visible
# to routers that do ``import api.main as m; m.store``.
# ---------------------------------------------------------------------------

store = get_store()
# AgentTypeRegistry singleton — wired at composition root; swappable without
# touching any consumer. api/ wires the cobranza entry here so tenancy/ stays
# pure (no features import); swap for a DB-backed impl right here.
agent_type_registry: AgentTypeRegistry = InCodeAgentTypeRegistry({"cobranza": COBRANZA_AGENT_TYPE})
visitor_memory: VisitorMemory | None = None
email_service: EmailService | None = None
whatsapp_service: WhatsAppService | None = None
whatsapp_services: dict[str, WhatsAppService] = {}  # tenant_id → WhatsAppService

chathub_outbound_client = ChathubOutboundClient(
    url=settings.chathub_outbound_url,
    token=settings.chathub_outbound_token,
    channel_id=settings.chathub_outbound_channel_id,
    timeout=settings.chathub_outbound_timeout,
    verify_ssl=settings.chathub_outbound_verify_ssl,
)


def get_whatsapp_service(tenant_id: str | None = None) -> WhatsAppService | None:
    """Resolve WhatsApp service for a tenant. Falls back to the default instance."""
    if tenant_id and tenant_id in whatsapp_services:
        return whatsapp_services[tenant_id]
    return whatsapp_service


# ---------------------------------------------------------------------------
# Lifespan: initialize DB pool + services
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    global store, visitor_memory, email_service, whatsapp_service, whatsapp_services

    email_service = EmailService(api_url=settings.mail_api_url)

    if settings.whatsapp_api_url:
        whatsapp_service = WhatsAppService(
            api_url=settings.whatsapp_api_url,
            api_key=settings.whatsapp_api_key,
            instance_name=settings.whatsapp_instance,
        )
        _tenants_map = _json.loads(settings.whatsapp_tenants)
        for _inst_name, _tenant_cfg in _tenants_map.items():
            _tid = _tenant_cfg.get("tenant_id")
            if _tid and _inst_name != settings.whatsapp_instance:
                whatsapp_services[_tid] = WhatsAppService(
                    api_url=settings.whatsapp_api_url,
                    api_key=settings.whatsapp_api_key,
                    instance_name=_inst_name,
                )
                logger.info("WhatsApp service registered: tenant={} instance={}", _tid, _inst_name)
    else:
        whatsapp_service = None

    vm = VisitorMemory(database_url=settings.database_url, schema=settings.database_schema)
    await vm.init()
    visitor_memory = vm
    app.state.visitor_memory = vm

    db_pool = vm._pool
    if db_pool is not None:
        try:
            from shared.persistence.persistence import ensure_tables
            # Resolve spec from registry (default agent_type = "cobranza")
            _agent_spec = agent_type_registry.get("cobranza")
            _default_spec = _agent_spec.capture_spec
            await ensure_tables(
                db_pool,
                settings.database_schema,
                projection_table=_agent_spec.projection_table,
            )
            store = get_store(
                db_pool=db_pool,
                db_schema=settings.database_schema,
                capture_spec=_default_spec,
            )
            logger.info("PostgreSQL persistence active (schema={})", settings.database_schema)
        except Exception:
            logger.opt(exception=True).warning(
                "Failed to init persistence tables — falling back to in-memory"
            )
            store = get_store()
    else:
        logger.warning("No DB pool available — running with in-memory state only")

    await _register_whatsapp_webhook()

    yield

    await vm.close()
    visitor_memory = None
    app.state.visitor_memory = None


# ---------------------------------------------------------------------------
# App creation
# ---------------------------------------------------------------------------

_docs_kwargs = (
    {}
    if settings.enable_docs
    else {"docs_url": None, "redoc_url": None, "openapi_url": None}
)

app = FastAPI(
    title="Sorelia API",
    version="0.1.0",
    lifespan=lifespan,
    root_path=settings.root_path,
    **_docs_kwargs,
)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled exception on {}", request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RateLimitMiddleware)

from shared.config.cors import build_cors_origin_regex

_cors_origin_regex = build_cors_origin_regex(settings.cors_origins)
logger.info("CORS allow_origin_regex={}", _cors_origin_regex)

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=_cors_origin_regex,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-CSRF-Token", "X-Session-Token", "X-Dashboard-Key", "X-Publishable-Key"],
    expose_headers=["X-CSRF-Token"],
)

app.include_router(dashboard_router)
app.include_router(security_router)
app.include_router(conversations_router)
app.include_router(cobranza_router)
app.include_router(webhooks_router)
# Chathub inbound (POST /{bot_path}/chat). Registered AFTER literal API routes
# so the path-param route never shadows them.
app.include_router(chathub_router)


# ── Demo frontend (landing + chat widget) ──────────────────────────────────
# GET / is registered as an explicit route BEFORE the StaticFiles mount so
# Starlette's router picks it up before the catch-all static handler.
# The route reads DEFAULT_TENANT once at startup, selects the per-tenant
# index.html (frontend/tenants/<tenant>/index.html) when it exists, falls
# back to the generic frontend/index.html otherwise, replaces the __TENANT__
# sentinel with the resolved tenant name, and caches the result in memory.
# All other assets (widget.js, embed.js, favicon, /assets/…) continue to be
# served by the StaticFiles mount unchanged.
def _mount_demo_frontend() -> None:
    import os as _os
    from pathlib import Path as _FPath
    from fastapi import Request as _Request
    from fastapi.responses import HTMLResponse as _HTMLResponse, RedirectResponse as _RedirectResponse, FileResponse as _FileResponse
    from fastapi.staticfiles import StaticFiles

    for candidate in (_FPath("/app/frontend"),
                      _FPath(__file__).resolve().parent.parent.parent.parent / "frontend"):
        if not candidate.exists():
            continue

        # Resolve tenant from environment.  "prestaunion" is the documented
        # dev fallback only — production containers MUST set DEFAULT_TENANT.
        tenant = _os.environ.get("DEFAULT_TENANT", "prestaunion")

        # Select per-tenant file when available, otherwise use the generic one.
        per_tenant_path = candidate / "tenants" / tenant / "index.html"
        generic_path = candidate / "index.html"
        source = per_tenant_path if per_tenant_path.exists() else generic_path

        if not source.exists():
            logger.warning(
                "Demo frontend: neither tenants/{}/index.html nor index.html found — "
                "skipping GET / handler",
                tenant,
            )
        else:
            _raw_html = source.read_bytes()
            # Replace __TENANT__ sentinel (existing logic).
            _cached_html = _raw_html.replace(b'"__TENANT__"', b'"' + tenant.encode() + b'"')

            # Replace __PK__ sentinel with the DEFAULT_TENANT's current publishable key.
            # The key is PUBLIC — the widget needs it to authenticate gated API calls.
            # Accepts publishable_keys list or legacy scalar publishable_key.
            def _resolve_default_pk(slug: str) -> str:
                cfg = _load_tenant_config(slug)
                if cfg is None:
                    return ""
                keys_list = cfg.get("publishable_keys")
                if isinstance(keys_list, list) and keys_list:
                    for _e in keys_list:
                        if isinstance(_e, dict) and _e.get("status") == "current":
                            return _e.get("key", "")
                    first = keys_list[0]
                    return first.get("key", "") if isinstance(first, dict) else ""
                scalar = cfg.get("publishable_key")
                return scalar if isinstance(scalar, str) else ""

            _default_pk = _resolve_default_pk(tenant)
            _cached_html = _cached_html.replace(b'"__PK__"', b'"' + _default_pk.encode() + b'"')

            @app.get("/", include_in_schema=False)
            async def _serve_root() -> _HTMLResponse:  # noqa: RUF029
                return _HTMLResponse(
                    content=_cached_html,
                    media_type="text/html; charset=utf-8",
                )

            logger.info(
                "Demo frontend GET / → {} (tenant={} pk={}...)", source.name, tenant, _default_pk[:12]
            )

        # ── Widget distribution routes ─────────────────────────────────────
        # These explicit routes MUST be registered BEFORE the catch-all
        # StaticFiles mount, or Starlette's router will never reach them.
        #
        # WIDGET_VERSION is injected at Docker build time (ARG → ENV).
        # Falls back to "dev" for local runs so tests and the dev server work
        # without a Node build step.
        #
        # Minification provides size reduction and mild deterrence only —
        # it is NOT a security control. No real secret lives in widget.min.js.
        _widget_version = _os.environ.get("WIDGET_VERSION", "dev")
        _widget_min_path = candidate / "widget.min.js"

        @app.get("/widget/{version}/widget.min.js", include_in_schema=False)
        async def _serve_versioned_widget(version: str) -> _FileResponse:  # noqa: RUF029
            if version != _widget_version:
                from fastapi import HTTPException as _HTTPException
                raise _HTTPException(status_code=404, detail="Widget version not found")
            if not _widget_min_path.exists():
                from fastapi import HTTPException as _HTTPException
                raise _HTTPException(status_code=404, detail="widget.min.js not built yet")
            return _FileResponse(
                path=str(_widget_min_path),
                media_type="application/javascript",
                headers={"Cache-Control": "public, max-age=31536000, immutable"},
            )

        @app.get("/widget.js", include_in_schema=False)
        async def _serve_legacy_widget_alias(request: Request) -> _RedirectResponse:  # noqa: RUF029
            # Permanent-ish redirect (302 = temporary, allows clients to re-check
            # on next deploy when WIDGET_VERSION changes). Cache-Control: no-cache
            # so browsers always re-check this redirect target after a deploy.
            # Prefix root_path so the target stays under the slug when the app is
            # behind a strip-prefix proxy (Traefik /pubot-c02e78e1); empty locally.
            _root = request.scope.get("root_path", "")
            return _RedirectResponse(
                url=f"{_root}/widget/{_widget_version}/widget.min.js",
                status_code=302,
                headers={"Cache-Control": "no-cache"},
            )

        logger.info(
            "Widget routes registered: WIDGET_VERSION={} widget.min.js={}",
            _widget_version,
            "found" if _widget_min_path.exists() else "missing (use Docker build for prod)",
        )

        app.mount("/", StaticFiles(directory=str(candidate), html=True), name="demo")
        logger.info("Demo frontend mounted from {}", candidate)
        return

    logger.info("Demo frontend directory not found — skipping static mount")


_mount_demo_frontend()
