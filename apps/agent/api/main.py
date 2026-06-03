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
from features.conversation.persistence.state import get_store
from features.conversation.persistence.visitor_memory import VisitorMemory
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

    db_pool = vm._pool
    if db_pool is not None:
        try:
            from shared.persistence.persistence import ensure_tables
            await ensure_tables(db_pool, settings.database_schema)
            store = get_store(db_pool=db_pool, db_schema=settings.database_schema)
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
    allow_headers=["Content-Type", "X-CSRF-Token", "X-Session-Token", "X-Dashboard-Key"],
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


# ── Demo frontend (PrestaUnion landing + chat widget) ──
# Mounted LAST so it never shadows API routes.
def _mount_demo_frontend() -> None:
    from pathlib import Path as _FPath
    from fastapi.staticfiles import StaticFiles

    for candidate in (_FPath("/app/frontend"),
                      _FPath(__file__).resolve().parent.parent.parent.parent / "frontend"):
        if candidate.exists():
            app.mount("/", StaticFiles(directory=str(candidate), html=True), name="demo")
            logger.info("Demo frontend mounted from {}", candidate)
            return
    logger.info("Demo frontend directory not found — skipping static mount")


_mount_demo_frontend()
