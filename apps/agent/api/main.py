"""Sorelia FastAPI application."""

from dotenv import load_dotenv
load_dotenv()

import hashlib
import hmac
import time
from collections import defaultdict
from datetime import date

import re

import httpx
from fastapi import BackgroundTasks, FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from loguru import logger
from pydantic import BaseModel, Field, field_validator

from contextlib import asynccontextmanager

from config.settings import settings
from core.state import get_store
from core.agent import SoreliaAgent
from core.llm import LLMError, build_llm_provider
from core.email_service import EmailService
from core.whatsapp_service import WhatsAppService
from core.response_guard import guard_response
from core.response_builder import build_quick_replies
from core.whatsapp_formatter import format_for_whatsapp
from core.visitor_memory import VisitorMemory
# NOTE: visit_manager / google_calendar were real-estate-only (property visits)
# and are NOT ported. Their references are removed below (TODO if cobranza ever
# needs scheduled callbacks).
from tools import ToolRegistry
from api.dashboard import dashboard_router

# Store and services — initialised in lifespan with DB pool
store = get_store()
visitor_memory: VisitorMemory | None = None
email_service: EmailService | None = None
whatsapp_service: WhatsAppService | None = None
whatsapp_services: dict[str, WhatsAppService] = {}  # tenant_id → WhatsAppService


def get_whatsapp_service(tenant_id: str | None = None) -> WhatsAppService | None:
    """Resolve WhatsApp service for a tenant. Falls back to the default instance."""
    if tenant_id and tenant_id in whatsapp_services:
        return whatsapp_services[tenant_id]
    return whatsapp_service


def get_tenant_contact_phone(tenant_id: str | None = None) -> str:
    """Get contact phone from tenant config. Falls back to soul default."""
    if tenant_id:
        import json as _j
        from pathlib import Path as _P
        _td = _P("/app/tenants") / tenant_id
        if not _td.exists():
            _td = _P(__file__).resolve().parent.parent.parent / "tenants" / tenant_id
        if (_td / "tenant.config.json").exists():
            _tc = _j.loads((_td / "tenant.config.json").read_text())
            _phone = _tc.get("contact", {}).get("phone", "")
            if _phone:
                return _phone.replace("+", "")
    # TODO Fase 1: no domain default — return empty if tenant has no phone configured.
    return settings.soul.whatsapp.replace("+", "") if hasattr(settings, "soul") else ""


async def _register_whatsapp_webhook() -> None:
    """Register Sorelia's webhook URL with Evolution API on startup."""
    if not settings.whatsapp_api_url or not settings.whatsapp_instance:
        logger.info("WhatsApp webhook registration skipped — not configured")
        return

    url = f"{settings.whatsapp_api_url}/webhook/set/{settings.whatsapp_instance}"
    payload = {
        "webhook": {
            "url": settings.whatsapp_webhook_url,
            "webhook_by_events": False,
            "events": ["MESSAGES_UPSERT"],
            "enabled": True,
        },
    }
    try:
        async with httpx.AsyncClient(verify=False) as client:
            resp = await client.post(
                url,
                json=payload,
                headers={"apikey": settings.whatsapp_api_key, "Content-Type": "application/json"},
                timeout=10.0,
            )
        if resp.status_code in (200, 201):
            logger.info("WhatsApp webhook registered: {}", settings.whatsapp_webhook_url)
        else:
            logger.error("WhatsApp webhook registration failed: status={} body={}", resp.status_code, resp.text[:300])
    except httpx.RequestError as exc:
        logger.error("WhatsApp webhook registration request failed: {}", exc)


async def _process_whatsapp_message(phone: str, sender_name: str, text: str, message_id: str | None = None, tenant_id: str = "demo") -> None:
    """Process an inbound WhatsApp message in the background."""
    conversation_id = f"wa-{tenant_id}-{phone}"
    _wa_svc = get_whatsapp_service(tenant_id)
    _contact_phone = get_tenant_contact_phone(tenant_id)

    # Daily rate limit check using phone as visitor_id
    _daily_limit = settings.daily_message_limit
    if visitor_memory:
        allowed, remaining = await visitor_memory.check_daily_limit(conversation_id, limit=_daily_limit)
    else:
        allowed, remaining = _check_ip_daily_limit(phone, _daily_limit)

    if not allowed:
        if _wa_svc:
            await _wa_svc.send_text(
                phone,
                "Gracias por tu mensaje. Por hoy ya cubrimos bastante. "
                f"Escríbenos mañana o llámanos al {_contact_phone} para una atención directa.",
            )
        return

    # Get or create conversation state (same as web)
    conv = await store.get_or_create_async(conversation_id, visitor_id=conversation_id)
    await conv.add_user_message_async(text)

    # Auto-collect phone + name from WhatsApp (we already have them!)
    if phone and "phone" not in conv.lead.collected:
        conv.lead.collected["phone"] = phone
    if sender_name and "name" not in conv.lead.collected:
        conv.lead.collected["name"] = sender_name

    # Log suspicious input
    if _SUSPICIOUS_PATTERN.search(text):
        logger.warning("Suspicious WhatsApp input: phone={}", phone)

    lead_level_before = conv.lead.level

    try:
        provider = build_llm_provider(settings)

        # Meilisearch was real-estate property search — not used in cobranza.
        meili_client = None

        def _persist_identity_wa(_profile: dict) -> None:
            conv.identity_verified = True
            conv.debt_context = _profile
            logger.info(
                "Identity resolved via DNI (WhatsApp): conversation={} account={}",
                conv.conversation_id, _profile.get("account_id"),
            )

        registry = ToolRegistry(
            meilisearch_client=meili_client,
            lead_machine=conv.lead,
            visitor_memory=visitor_memory,
            email_service=email_service,
            whatsapp_service=_wa_svc,
            identity_verified=conv.identity_verified,
            debt_context=conv.debt_context,
            tenant_id=tenant_id,
            on_identity_resolved=_persist_identity_wa,
        )
        agent = SoreliaAgent(provider=provider, tool_registry=registry)

        enriched_page_context: dict = {}
        if conv.identity_verified and conv.debt_context:
            enriched_page_context["identity"] = {
                "verified": True,
                "borrower_name": conv.debt_context.get("borrower_name"),
                "business_name": conv.debt_context.get("business_name"),
                "loan_number": conv.debt_context.get("loan_number"),
                "status_label": conv.debt_context.get("status_label"),
            }
        else:
            enriched_page_context["identity"] = {"verified": False}
        if visitor_memory:
            visitor_profile = await visitor_memory.get_visitor(conversation_id)
            if visitor_profile:
                enriched_page_context["visitor"] = {
                    "name": visitor_profile.get("name") or sender_name,
                    "visit_count": visitor_profile.get("visit_count", 1),
                    "projects_viewed": visitor_profile.get("projects_viewed", []),
                }
            else:
                await visitor_memory.upsert_visitor(conversation_id, {"name": sender_name, "entry_source": "whatsapp"})

        result = await agent.process_message(
            text=text,
            conversation_id=conv.conversation_id,
            history=conv.history[:-1],
            lead_state=conv.lead.get_status(),
            page_context=enriched_page_context,
            channel="whatsapp",
        )
        content = result["content"]
        wa_ui_actions = result.get("ui_actions", {})
        wa_tool_pairs = result.get("tool_pairs", [])
    except Exception:
        logger.opt(exception=True).error("WhatsApp agent error for phone={}", phone)
        content = (
            "Disculpa, tuve un problema procesando tu mensaje. "
            f"Puedes intentar de nuevo o llamarnos al {_contact_phone}."
        )
        wa_ui_actions = {}
        wa_tool_pairs = []

    from core.response_guard import guard_response
    content = guard_response(content, conv.history, conv.lead.get_status())
    await conv.add_assistant_message_async(content)

    # Increment daily counter
    if visitor_memory:
        await visitor_memory.increment_daily_count(conversation_id)
    else:
        _increment_ip_daily_count(phone)

    # Send response back via WhatsApp
    if _wa_svc:
        await _wa_svc.send_text(phone, content, incoming_id=message_id)

        # Send formatted widgets (buttons, mortgage, subsidy, comparison)
        wa_quick_replies = build_quick_replies(conv.lead.get_status(), wa_ui_actions, wa_tool_pairs, content)
        wa_messages = format_for_whatsapp(wa_ui_actions, wa_quick_replies, phone)
        if wa_messages:
            await _wa_svc.send_formatted(wa_messages)

        # Track projects viewed and search history from tool results
        if visitor_memory:
            for tool_name, result in wa_tool_pairs:
                if tool_name == "search_properties" and result.get("properties"):
                    for p in result["properties"]:
                        slug = p.get("slug")
                        if slug:
                            await visitor_memory.add_project_viewed(conversation_id, slug)
                    # Track search history
                    from datetime import datetime as _dt
                    search_entry = {
                        "ts": _dt.now().isoformat(),
                        "district": result.get("filters_applied", {}).get("district"),
                        "bedrooms": result.get("filters_applied", {}).get("bedrooms"),
                        "total": result.get("total", 0),
                    }
                    await visitor_memory.add_search(conversation_id, search_entry)
                elif tool_name == "get_property_detail" and result.get("slug"):
                    await visitor_memory.add_project_viewed(conversation_id, result["slug"])
    else:
        logger.info("[WA-NO-SERVICE] Would send to {}: {}", phone, content[:100])

    # Persist denormalized lead
    if store.db_pool is not None:
        try:
            from core.persistence import upsert_lead

            await upsert_lead(
                store.db_pool,
                store.db_schema,
                conv.conversation_id,
                conversation_id,  # visitor_id = wa-phone
                conv.lead.collected,
                conv.lead.level,
            )
        except Exception:
            logger.opt(exception=True).warning("Failed to persist WhatsApp lead")

    # Update visitor memory with lead data
    if visitor_memory:
        collected = conv.lead.get_status().get("collected", {})
        if collected:
            await visitor_memory.upsert_visitor(conversation_id, {
                "name": collected.get("name") or sender_name,
                "email": collected.get("email"),
                "phone": collected.get("phone") or phone,
                "lead_data": collected,
            })

    # Lead capture hook (same as web)
    lead_level_after = conv.lead.level
    _CONTACT_LEVELS = {"LEAD", "LEAD_ENRICHED"}
    if (
        lead_level_after in _CONTACT_LEVELS
        and lead_level_before not in _CONTACT_LEVELS
        and not conv.lead_notified
    ):
        try:
            from core.webhooks import on_lead_captured

            collected = conv.lead.collected
            # TODO Fase 1: build a cobranza "case" context (account/debt summary)
            # to drive notifications. For now pass a neutral context.
            case_context = {"name": "", "brochure_url": "", "sales_agent": {}}

            actions = await on_lead_captured(
                lead_data=collected,
                project=case_context,
                conversation_id=conv.conversation_id,
                email_service=email_service,
                whatsapp_service=_wa_svc,
                notification_email=settings.notification_email,
            )
            conv.lead_notified = True
            logger.info("WhatsApp lead captured: phone={} tenant={} actions={}", phone, tenant_id, actions)
        except Exception:
            logger.opt(exception=True).warning("WhatsApp lead capture hook failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global store, visitor_memory, email_service, whatsapp_service, whatsapp_services

    # Email service — uses internal mail API proxy, degrades to logging if not set
    email_service = EmailService(api_url=settings.mail_api_url)

    # WhatsApp service — uses Evolution API, degrades to logging if not set
    if settings.whatsapp_api_url:
        whatsapp_service = WhatsAppService(
            api_url=settings.whatsapp_api_url,
            api_key=settings.whatsapp_api_key,
            instance_name=settings.whatsapp_instance,
        )
        # Build per-tenant WhatsApp services from whatsapp_tenants config
        import json as _json_lifespan
        _tenants_map = _json_lifespan.loads(settings.whatsapp_tenants)
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

    vm = VisitorMemory(
        database_url=settings.database_url,
        schema=settings.database_schema,
    )
    await vm.init()
    visitor_memory = vm

    # Reuse the asyncpg pool from VisitorMemory for conversation persistence
    db_pool = vm._pool
    if db_pool is not None:
        try:
            from core.persistence import ensure_tables

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

    # TODO Fase 1: visit_manager / google_calendar removed (real-estate visits).
    # If cobranza needs scheduled callbacks/PTP reminders, add an equivalent here.

    # Register WhatsApp webhook with Evolution API
    await _register_whatsapp_webhook()

    yield

    await vm.close()
    visitor_memory = None

# --- Rate Limiting ---

_RATE_LIMIT = 10  # max requests per window
_RATE_WINDOW = 60  # seconds
_RATE_LIMITED_PATHS = {"/api/v1/chat", "/api/v1/conversations/messages"}
_request_log: dict[str, list[float]] = defaultdict(list)


# --- Daily limit for visitors without visitor_id (IP fallback) ---
_daily_ip_counts: dict[str, tuple[str, int]] = {}  # ip -> (date_str, count)


def _check_ip_daily_limit(ip: str, limit: int) -> tuple[bool, int]:
    """In-memory daily limit check by IP.  Returns (allowed, remaining)."""
    today = date.today().isoformat()
    entry = _daily_ip_counts.get(ip)
    if entry is None or entry[0] != today:
        return True, limit
    count = entry[1]
    remaining = max(limit - count, 0)
    return remaining > 0, remaining


def _increment_ip_daily_count(ip: str) -> None:
    today = date.today().isoformat()
    entry = _daily_ip_counts.get(ip)
    if entry is None or entry[0] != today:
        _daily_ip_counts[ip] = (today, 1)
    else:
        _daily_ip_counts[ip] = (today, entry[1] + 1)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """In-memory per-IP rate limiter for chat endpoints."""

    async def dispatch(self, request: Request, call_next):
        if request.url.path not in _RATE_LIMITED_PATHS:
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"
        now = time.time()
        cutoff = now - _RATE_WINDOW

        # Prune old timestamps
        timestamps = _request_log[client_ip]
        _request_log[client_ip] = [t for t in timestamps if t > cutoff]

        if len(_request_log[client_ip]) >= _RATE_LIMIT:
            return Response(
                status_code=429,
                content="Too Many Requests. Limit: 10 requests per minute.",
            )

        _request_log[client_ip].append(now)
        return await call_next(request)


app = FastAPI(
    title="Sorelia API",
    version="0.1.0",
    lifespan=lifespan,
    # Behind Traefik strip-prefix the app lives under COBRANZA_ROOT_PATH
    # (e.g. /pubot-gj5w2a0p). Empty in local dev. Makes /docs + generated URLs
    # respect the prefix. uvicorn --root-path sets the same; this is the
    # belt-and-suspenders so it works even without the CLI flag.
    root_path=settings.root_path,
)

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled exception on {}", request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


app.add_middleware(RateLimitMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-CSRF-Token", "X-Session-Token", "X-Dashboard-Key"],
    expose_headers=["X-CSRF-Token"],
)

app.include_router(dashboard_router)

# CSRF — persistent secret from settings (survives restarts)
_CSRF_SECRET = settings.csrf_secret


def _generate_csrf_token() -> str:
    timestamp = str(int(time.time()))
    sig = hmac.new(_CSRF_SECRET.encode(), timestamp.encode(), hashlib.sha256).hexdigest()
    return f"{timestamp}_{sig}"


_CSRF_MAX_AGE = 3600  # tokens expire after 1 hour


def _validate_csrf_token(token: str) -> bool:
    if not token or "_" not in token:
        return False
    timestamp, sig = token.split("_", 1)
    # Verify signature
    expected = hmac.new(_CSRF_SECRET.encode(), timestamp.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return False
    # Verify token age
    try:
        token_time = int(timestamp)
    except ValueError:
        return False
    if abs(time.time() - token_time) > _CSRF_MAX_AGE:
        return False
    return True


# --- Session token (proof-of-origin) ---

_SESSION_TOKEN_MAX_AGE = 3600  # 1 hour


def _generate_session_token(visitor_id: str) -> str:
    """Create an HMAC-signed session token binding visitor_id + timestamp."""
    timestamp = str(int(time.time()))
    payload = f"{visitor_id}:{timestamp}"
    signature = hmac.new(
        _CSRF_SECRET.encode(), payload.encode(), hashlib.sha256
    ).hexdigest()
    return f"{payload}:{signature}"


def _verify_session_token(token: str, max_age: int = _SESSION_TOKEN_MAX_AGE) -> tuple[bool, str]:
    """Verify HMAC session token. Returns (valid, visitor_id)."""
    if not token:
        return False, ""
    parts = token.split(":")
    if len(parts) != 3:
        return False, ""
    visitor_id, timestamp_str, signature = parts
    # Check expiry
    try:
        token_time = int(timestamp_str)
    except ValueError:
        return False, ""
    if time.time() - token_time > max_age:
        return False, ""
    # Check HMAC
    payload = f"{visitor_id}:{timestamp_str}"
    expected = hmac.new(
        _CSRF_SECRET.encode(), payload.encode(), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return False, ""
    return True, visitor_id


# --- Routes ---

@app.get("/api/v1/security/csrf-token")
async def csrf_token(response: Response):
    token = _generate_csrf_token()
    response.headers["X-CSRF-Token"] = token
    response.set_cookie("csrf_token", token, httponly=True, samesite="lax", secure=True)
    return {"status": "ok"}


@app.get("/api/v1/security/session-token")
async def session_token(request: Request):
    """Issue a time-limited HMAC session token tied to visitor_id."""
    visitor_id = request.query_params.get("visitor_id", "anonymous")
    token = _generate_session_token(visitor_id)
    return {"token": token, "expires_in": _SESSION_TOKEN_MAX_AGE}


_SUSPICIOUS_PATTERN = re.compile(
    r"(ignore previous|system prompt|you are now|forget your instructions)",
    re.IGNORECASE,
)


_UUID_PATTERN = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class ChatRequest(BaseModel):
    channel: str = "web"
    tenant_id: str | None = None
    text: str = Field(..., min_length=1, max_length=500)
    conversation_id: str | None = None
    visitor_id: str | None = None
    previous_response_id: str | None = None
    page_context: dict | None = None
    # Cobranza: demo campaign token (e.g. "demo-juan"). The token IS the
    # identity — resolved server-side to a verified borrower profile. The
    # widget reads it from ?ct= and sends it on the first message.
    campaign_token: str | None = Field(default=None, max_length=64)

    @field_validator("campaign_token")
    @classmethod
    def validate_campaign_token(cls, v: str | None) -> str | None:
        if v is not None:
            v = v.strip()
            if not v:
                return None
            if not re.fullmatch(r"[A-Za-z0-9_\-\.]{1,64}", v):
                raise ValueError("campaign_token has invalid format")
        return v

    @field_validator("text")
    @classmethod
    def normalize_whitespace(cls, v: str) -> str:
        v = " ".join(v.split())
        if not v:
            raise ValueError("text must not be blank")
        return v

    @field_validator("visitor_id")
    @classmethod
    def validate_visitor_id(cls, v: str | None) -> str | None:
        if v is not None and not _UUID_PATTERN.match(v):
            raise ValueError("visitor_id must be a valid UUID")
        return v

    @field_validator("conversation_id")
    @classmethod
    def validate_conversation_id(cls, v: str | None) -> str | None:
        if v is not None and not _UUID_PATTERN.match(v):
            raise ValueError("conversation_id must be a valid UUID")
        return v


@app.post("/api/v1/chat")
async def chat(request: Request, body: ChatRequest):
    # Session token verification (proof-of-origin)
    session_token = request.headers.get("X-Session-Token", "")
    valid, _token_visitor = _verify_session_token(session_token)
    if not valid:
        return Response(status_code=401, content="Invalid or expired session token")

    # CSRF verification (defense in depth)
    token = request.headers.get("X-CSRF-Token", "")
    if not _validate_csrf_token(token):
        return Response(status_code=403, content="Invalid CSRF token")

    # --- Daily message limit (check BEFORE spending tokens) ---
    _daily_limit = settings.daily_message_limit
    client_ip = request.client.host if request.client else "unknown"

    if body.visitor_id and visitor_memory:
        allowed, remaining = await visitor_memory.check_daily_limit(body.visitor_id, limit=_daily_limit)
    else:
        allowed, remaining = _check_ip_daily_limit(client_ip, _daily_limit)

    if not allowed:
        return {
            "message": {
                "conversation_id": body.conversation_id or "limit",
                "content": (
                    "Gracias por escribirnos. Por hoy ya cubrimos bastante. "
                    "Para seguir ayudándote, te conecto con nuestro equipo de asesores, "
                    "que puede darte una atención personalizada. Escríbenos por WhatsApp y con gusto te atendemos."
                ),
                "response_id": "daily-limit",
                "metadata": {},
                "quick_replies": {
                    "type": "single_select",
                    "buttons": [
                        {"id": "wa", "label": "Escribir por WhatsApp", "value": f"https://wa.me/{get_tenant_contact_phone(body.tenant_id)}?text=Hola%2C+quiero+regularizar+mi+situaci%C3%B3n"},
                        {"id": "call", "label": "Llamar ahora", "value": f"tel:+{get_tenant_contact_phone(body.tenant_id)}"},
                    ],
                },
                "form_data": ui_actions.get("form_data"),
                "ui_actions": {},
            },
            "context": {},
            "context_updated": False,
            "has_error": False,
            "error_message": None,
            "lead": {},
        }

    # Get or create conversation state — async API loads from DB if available
    conv = await store.get_or_create_async(body.conversation_id, visitor_id=body.visitor_id)
    await conv.add_user_message_async(body.text)

    # Update page context if provided with message
    if body.page_context:
        conv.page_context = body.page_context

    # Log suspicious input patterns (non-blocking)
    if _SUSPICIOUS_PATTERN.search(body.text):
        logger.warning("Suspicious input pattern: conversation={}", conv.conversation_id)

    # Load visitor profile if visitor_id provided
    entry_source = (body.page_context or {}).get("entry_source", "direct")
    visitor_profile: dict | None = None
    if body.visitor_id and visitor_memory:
        visitor_profile = await visitor_memory.get_visitor(body.visitor_id)
        if visitor_profile is None:
            # First visit -- create a stub record with entry source
            await visitor_memory.upsert_visitor(body.visitor_id, {"entry_source": entry_source})

        # Track project being viewed
        project_slug = (body.page_context or {}).get("project_slug")
        if project_slug:
            await visitor_memory.add_project_viewed(body.visitor_id, project_slug)

    # Snapshot lead level before processing to detect transitions
    lead_level_before = conv.lead.level

    # Try to use real agent, fallback to simple response
    has_error = False
    error_message: str | None = None
    # Load tenant config if specified
    _tenant_config = None
    if body.tenant_id:
        from core.tenant_loader import TenantConfig
        from pathlib import Path as _TPath
        # Check both /app/tenants (Docker) and relative path (dev)
        _tdir = _TPath("/app/tenants") / body.tenant_id
        if not _tdir.exists():
            _tdir = _TPath(__file__).resolve().parent.parent.parent / "tenants" / body.tenant_id
        if (_tdir / "tenant.config.json").exists():
            try:
                _tenant_config = TenantConfig.from_directory(_tdir)
                logger.info("Loaded tenant config: {}", body.tenant_id)
            except Exception:
                logger.opt(exception=True).warning("Failed to load tenant config: {}", body.tenant_id)

    from config.settings import resolve_api_key
    _api_key = resolve_api_key(body.tenant_id)

    # ── Cobranza identity gate: resolve the campaign token (demo: mock source).
    # The token can arrive on the request or inside page_context (widget). It
    # resolves to a verified borrower profile; account_id is NEVER from the LLM.
    _token = body.campaign_token or (body.page_context or {}).get("campaign_token")
    if _token and not conv.identity_verified:
        from integrations.mock_debt_source import resolve_token

        _profile = resolve_token(_token, tenant_id=body.tenant_id or "prestaunion")
        if _profile:
            conv.identity_verified = True
            conv.debt_context = _profile
            logger.info(
                "Identity resolved: conversation={} account={}",
                conv.conversation_id, _profile.get("account_id"),
            )
        else:
            logger.info("Campaign token did not resolve: conversation={}", conv.conversation_id)

    _download_base = str(request.base_url).rstrip("/")

    try:
        provider = build_llm_provider(settings, api_key_override=_api_key)

        # Meilisearch was real-estate property search — not used in cobranza.
        meili_client = None

        # Persist a mid-conversation DNI identification back to the state so the
        # next turn starts already verified (gate stays open across turns).
        def _persist_identity(_profile: dict) -> None:
            conv.identity_verified = True
            conv.debt_context = _profile
            logger.info(
                "Identity resolved via DNI: conversation={} account={}",
                conv.conversation_id, _profile.get("account_id"),
            )

        registry = ToolRegistry(
            meilisearch_client=meili_client,
            lead_machine=conv.lead,
            visitor_memory=visitor_memory,
            email_service=email_service,
            whatsapp_service=get_whatsapp_service(body.tenant_id),
            identity_verified=conv.identity_verified,
            debt_context=conv.debt_context,
            download_base_url=_download_base,
            tenant_id=body.tenant_id or "prestaunion",
            on_identity_resolved=_persist_identity,
        )
        agent = SoreliaAgent(provider=provider, tool_registry=registry, tenant=_tenant_config)

        # Inject visitor context into page_context so the agent sees it
        enriched_page_context = dict(conv.page_context or {})
        if visitor_profile:
            enriched_page_context["visitor"] = {
                "name": visitor_profile.get("name"),
                "visit_count": visitor_profile.get("visit_count", 1),
                "projects_viewed": visitor_profile.get("projects_viewed", []),
            }
        # Inject identity status so the prompt knows the gate state (cobranza).
        if conv.identity_verified and conv.debt_context:
            enriched_page_context["identity"] = {
                "verified": True,
                "borrower_name": conv.debt_context.get("borrower_name"),
                "business_name": conv.debt_context.get("business_name"),
                "loan_number": conv.debt_context.get("loan_number"),
                "status_label": conv.debt_context.get("status_label"),
            }
        else:
            enriched_page_context["identity"] = {"verified": False}

        result = await agent.process_message(
            text=body.text,
            conversation_id=conv.conversation_id,
            history=conv.history[:-1],  # exclude the message we just added
            lead_state=conv.lead.get_status(),
            page_context=enriched_page_context,
            channel=body.channel,
        )
        content = result["content"]
        response_id = result["response_id"]
        ui_actions = result.get("ui_actions", {})
        tool_pairs = result.get("tool_pairs", [])
        suggested_replies = result.get("suggested_replies")
    except (KeyError, ValueError, TypeError, LLMError) as exc:
        logger.exception("Agent processing error (recoverable)")
        content = _fallback_response(body.text, conv)
        response_id = f"fallback_{conv.conversation_id[:8]}"
        ui_actions = {}
        tool_pairs = []
        has_error = True
        error_message = "Error processing your request"
    except Exception:
        # Auth, config, DB, or unexpected errors must propagate
        logger.exception("Agent fatal error")
        raise

    content = guard_response(content, conv.history, conv.lead.get_status())
    await conv.add_assistant_message_async(content)

    # Increment daily message counter (after successful response)
    if body.visitor_id and visitor_memory:
        await visitor_memory.increment_daily_count(body.visitor_id)
    else:
        _increment_ip_daily_count(client_ip)

    # Persist denormalized lead row for easy querying/export
    if store.db_pool is not None:
        try:
            from core.persistence import upsert_lead

            await upsert_lead(
                store.db_pool,
                store.db_schema,
                conv.conversation_id,
                body.visitor_id,
                conv.lead.collected,
                conv.lead.level,
            )
        except Exception:
            logger.opt(exception=True).warning("Failed to persist denormalized lead")

    # Track search history from tool results
    if body.visitor_id and visitor_memory:
        for tool_name, result in tool_pairs:
            if tool_name == "search_properties" and result.get("properties"):
                from datetime import datetime as _dt
                search_entry = {
                    "ts": _dt.now().isoformat(),
                    "district": result.get("filters_applied", {}).get("district"),
                    "bedrooms": result.get("filters_applied", {}).get("bedrooms"),
                    "total": result.get("total", 0),
                }
                await visitor_memory.add_search(body.visitor_id, search_entry)

    # Update visitor memory with any lead data collected during this turn
    if body.visitor_id and visitor_memory:
        lead_status = conv.lead.get_status()
        collected = lead_status.get("collected", {})
        if collected:
            await visitor_memory.upsert_visitor(body.visitor_id, {
                "name": collected.get("name"),
                "email": collected.get("email"),
                "phone": collected.get("phone"),
                "lead_data": collected,
            })

    # --- Lead capture hook: send brochure + notify sales on LEAD transition ---
    lead_level_after = conv.lead.level
    _CONTACT_LEVELS = {"LEAD", "LEAD_ENRICHED"}
    if (
        lead_level_after in _CONTACT_LEVELS
        and lead_level_before not in _CONTACT_LEVELS
        and not conv.lead_notified
    ):
        try:
            from core.webhooks import on_lead_captured

            collected = conv.lead.collected
            # TODO Fase 1: build a cobranza "case" context (account/debt summary).
            case_context = {"name": "", "brochure_url": "", "sales_agent": {}}

            actions = await on_lead_captured(
                lead_data=collected,
                project=case_context,
                conversation_id=conv.conversation_id,
                email_service=email_service,
                whatsapp_service=get_whatsapp_service(body.tenant_id),
                notification_email=settings.notification_email,
            )
            conv.lead_notified = True
            logger.info("Lead captured: conversation={} actions={}", conv.conversation_id, actions)
        except Exception:
            logger.opt(exception=True).warning("Lead capture hook failed (non-blocking)")

    # Use LLM-generated chips (validated by tool), fallback to heuristic
    quick_replies = None
    if suggested_replies:
        buttons = [{"id": f"qr-{i}", "label": opt, "value": opt} for i, opt in enumerate(suggested_replies)]
        quick_replies = {"type": "single_select", "buttons": buttons[:4]}
    if not quick_replies:
        from core.response_builder import build_quick_replies
        quick_replies = build_quick_replies(conv.lead.get_status(), ui_actions, tool_pairs, content)

    # Current identity state (updated this turn if the user identified via DNI).
    # Lets the widget refresh its identity strip without a token.
    _identity_state = {"verified": bool(conv.identity_verified)}
    if conv.identity_verified and conv.debt_context:
        _identity_state.update({
            "display_name": conv.debt_context.get("borrower_name", ""),
            "business_name": conv.debt_context.get("business_name", ""),
            "status_label": conv.debt_context.get("status_label", ""),
        })

    # Downloadable document produced this turn (certificate). Surfaced as a
    # structured field so the widget can render a download chip regardless of
    # how the LLM phrased its reply (don't tie UI affordance to LLM wording).
    _document = None
    for _tname, _tres in tool_pairs:
        if isinstance(_tres, dict) and _tres.get("download_url") and _tres.get("filename"):
            _document = {"download_url": _tres["download_url"], "filename": _tres["filename"]}

    return {
        "message": {
            "conversation_id": conv.conversation_id,
            "content": content,
            "response_id": response_id,
            "metadata": {},
            "quick_replies": quick_replies or None,
            "form_data": ui_actions.get("form_data"),
            "ui_actions": ui_actions,
            "identity": _identity_state,
            "document": _document,
        },
        "context": {},
        "context_updated": True,
        "has_error": has_error,
        "error_message": error_message,
        "lead": conv.lead.get_status(),
    }


# Frontend compatibility alias — agent-client.ts calls this path
@app.post("/api/v1/conversations/messages")
async def chat_compat(request: Request, body: ChatRequest):
    return await chat(request, body)


# ── Cobranza: certificate download (no-debt certificate PDF) ──
@app.get("/api/v1/cobranza/certificate/{filename}")
async def download_certificate(filename: str):
    """Serve a generated no-debt certificate PDF. Filename is sanitized to a
    safe pattern so it cannot escape the certificates directory."""
    from pathlib import Path as _CPath

    if not re.fullmatch(r"[A-Za-z0-9_\-]+\.pdf", filename):
        return Response(status_code=400, content="Invalid filename")
    path = _CPath("/tmp/prestaunion_certificates") / filename
    if not path.exists():
        return Response(status_code=404, content="Certificate not found")
    return FileResponse(path, media_type="application/pdf", filename=filename)


# ── Cobranza: list registered reclamos (demo visibility) ──
@app.get("/api/v1/cobranza/reclamos")
async def list_reclamos():
    """Return the (mock) Libro de Reclamaciones entries so the demo can show them."""
    from pathlib import Path as _RPath

    path = _RPath("/tmp/prestaunion_reclamos.json")
    if not path.exists():
        return {"reclamos": []}
    try:
        import json as _json
        return {"reclamos": _json.loads(path.read_text(encoding="utf-8"))}
    except (ValueError, OSError):
        return {"reclamos": []}


def _fallback_response(text: str, conv) -> str:
    """Simple fallback when LLM is unavailable. Domain-neutral (config-driven copy is TODO)."""
    lead = conv.lead
    if lead.level == "VISITOR":
        return "Hola, gracias por escribir. En un momento te ayudo con tu consulta."
    return "Entendido. En este momento no puedo consultar el detalle, pero te ayudo por WhatsApp."


@app.get("/api/v1/conversations/{conversation_id}/messages")
async def get_conversation_messages(request: Request, conversation_id: str):
    """Return conversation history from backend state (source of truth)."""
    # Validate conversation_id format (must be UUID)
    import re as _re
    if not _re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", conversation_id):
        return Response(status_code=400, content="Invalid conversation ID format")

    conv = await store.get_or_create_async(conversation_id)

    # Only return if the conversation actually has history
    if not conv.history:
        return {"messages": [], "page_context": {}, "lead_status": {}}

    messages = [
        {
            "role": msg["role"],
            "content": msg["content"],
            "timestamp": None,
        }
        for msg in conv.history
    ]

    return {
        "messages": messages,
        "page_context": conv.page_context,
        "lead_status": conv.lead.get_status(),
    }


class PageContextRequest(BaseModel):
    project_slug: str | None = None
    project_name: str | None = None
    zone: str | None = None
    entry_source: str = "direct"


@app.post("/api/v1/page-context")
async def page_context(request: Request, body: PageContextRequest):
    token = request.headers.get("X-CSRF-Token", "")
    if not _validate_csrf_token(token):
        return Response(status_code=403, content="Invalid CSRF token")

    # TODO Fase 1: make greeting tenant/soul-driven.
    greeting = "Hola, gracias por escribir. ¿En qué te puedo ayudar?"

    return {
        "initial_message": greeting,
        "conversation_metadata": {
            "project_slug": body.project_slug,
            "entry_source": body.entry_source,
        },
    }


# --- WhatsApp Webhook ---

# Media message types that we acknowledge but don't process
_WA_MEDIA_TYPES = {"imageMessage", "videoMessage", "audioMessage", "documentMessage", "stickerMessage"}


@app.post("/api/v1/webhooks/whatsapp")
async def whatsapp_webhook(request: Request, background_tasks: BackgroundTasks):
    """Receive incoming WhatsApp messages from Evolution API."""
    try:
        payload = await request.json()
    except Exception:
        return {"status": "ignored", "reason": "invalid json"}

    event = payload.get("event")
    instance_name = payload.get("instance", "unknown")
    logger.info("WhatsApp webhook: event={} instance={}", event, instance_name)

    if event != "messages.upsert":
        return {"status": "ignored", "reason": f"event={event}"}

    data = payload.get("data", {})
    key = data.get("key", {})

    # CRITICAL: filter out our own outgoing messages to prevent infinite loops
    from_me = key.get("fromMe", True)
    logger.debug("WhatsApp webhook detail: fromMe={} remoteJid={}", from_me, key.get("remoteJid", ""))
    if from_me:
        return {"status": "ignored", "reason": "fromMe"}

    remote_jid = key.get("remoteJid", "")

    # Ignore group messages
    if "@g.us" in remote_jid:
        return {"status": "ignored", "reason": "group"}

    # Ignore broadcast messages
    if "broadcast" in remote_jid.lower() or "@broadcast" in remote_jid:
        return {"status": "ignored", "reason": "broadcast"}

    # Ignore status updates
    if remote_jid == "status@broadcast":
        return {"status": "ignored", "reason": "status"}

    # Extract phone number from JID
    phone = remote_jid.split("@")[0] if "@" in remote_jid else remote_jid
    if not phone or not phone.isdigit():
        return {"status": "ignored", "reason": "invalid phone"}

    sender_name = data.get("pushName", "")
    message_type = data.get("messageType", "")
    message_data = data.get("message", {})

    # --- Multi-tenant routing (BEFORE media/slash handling) ---
    instance_name = payload.get("instance", settings.whatsapp_instance)
    from config.settings import resolve_whatsapp_tenant
    tenant_wa = resolve_whatsapp_tenant(instance_name)
    if not tenant_wa:
        return {"status": "ignored", "reason": f"unmapped instance: {instance_name}"}

    tenant_id = tenant_wa["tenant_id"]
    wa_mode = tenant_wa.get("mode", "all")
    _wa_svc = get_whatsapp_service(tenant_id)

    # --- Personal contact filter (skip saved contacts of the phone owner) ---
    # 1. Manual exclusion list from tenant config (fast, no API call)
    import json as _json
    from pathlib import Path as _Path
    _tenant_dir = _Path("/app/tenants") / tenant_id
    if not _tenant_dir.exists():
        _tenant_dir = _Path(__file__).resolve().parent.parent.parent / "tenants" / tenant_id
    _excluded_phones: list[str] = []
    if (_tenant_dir / "tenant.config.json").exists():
        _tc = _json.loads((_tenant_dir / "tenant.config.json").read_text())
        _excluded_phones = _tc.get("agent", {}).get("whatsapp", {}).get("excluded_phones", [])
    _phone_normalized = phone.lstrip("+").replace(" ", "")
    if any(_phone_normalized == p.lstrip("+").replace(" ", "") for p in _excluded_phones):
        logger.info("WhatsApp filtered: phone={} tenant={} — excluded phone (manual list)", phone, tenant_id)
        return {"status": "filtered", "reason": "excluded phone"}

    # 2. Dynamic check: is this a saved contact in the phone's address book?
    if _wa_svc:
        is_contact = await _wa_svc.is_saved_contact(phone)
        if is_contact:
            logger.info("WhatsApp filtered: phone={} tenant={} — saved contact (address book)", phone, tenant_id)
            return {"status": "filtered", "reason": "saved contact"}

    # Extract text from different message types
    text = None
    if message_type == "conversation":
        text = message_data.get("conversation", "")
    elif message_type == "extendedTextMessage":
        text = message_data.get("extendedTextMessage", {}).get("text", "")

    # Handle media messages gracefully
    if text is None and message_type in _WA_MEDIA_TYPES:
        if _wa_svc:
            await _wa_svc.send_text(
                phone,
                "Recibí tu imagen/archivo, por ahora solo puedo responder mensajes de texto. "
                "Escríbeme tu consulta y con gusto te ayudo.",
            )
        return {"status": "ack", "reason": "media", "tenant_id": tenant_id}

    if not text or not text.strip():
        return {"status": "ignored", "reason": "empty text"}

    text = text.strip()

    # Slash commands for WhatsApp (tenant-aware)
    if text.startswith("/"):
        cmd = text.lower().strip()
        if cmd in ("/reset", "/nuevo", "/nueva", "/clear"):
            conversation_id = f"wa-{tenant_id}-{phone}"
            # Clear conversation state
            conv = await store.get_or_create_async(conversation_id, visitor_id=conversation_id)
            conv.history.clear()
            conv.lead = type(conv.lead)()  # fresh lead machine
            if _wa_svc:
                await _wa_svc.send_text(phone, "Listo, empezamos de cero! En que te puedo ayudar? 😊")
            return {"status": "reset", "tenant_id": tenant_id}
        if cmd in ("/menu", "/help", "/ayuda"):
            if _wa_svc:
                await _wa_svc.send_text(
                    phone,
                    "*Comandos disponibles:*\n\n"
                    "/nuevo - Empezar conversacion nueva\n"
                    "/menu - Ver este menu\n\n"
                    "O simplemente escribeme lo que buscas!",
                )
            return {"status": "menu", "tenant_id": tenant_id}

    # --- Website-leads-only filter ---
    # In this mode, the agent only responds to conversations whose first message
    # matches a trigger phrase (from the website WhatsApp CTA links).
    # Personal contacts of the phone owner are ignored.
    if wa_mode == "website_leads_only":
        conversation_id = f"wa-{tenant_id}-{phone}"
        existing = await store.get_or_create_async(conversation_id, visitor_id=conversation_id)
        is_new_conversation = len(existing.history) == 0

        if is_new_conversation:
            # Load trigger phrases from tenant config
            import json as _json
            from pathlib import Path as _Path
            _tenant_dir = _Path("/app/tenants") / tenant_id
            if not _tenant_dir.exists():
                _tenant_dir = _Path(__file__).resolve().parent.parent.parent / "tenants" / tenant_id
            _trigger_phrases: list[str] = []
            _fallback_reply = ""
            if (_tenant_dir / "tenant.config.json").exists():
                _tc = _json.loads((_tenant_dir / "tenant.config.json").read_text())
                _wa_cfg = _tc.get("agent", {}).get("whatsapp", {})
                _trigger_phrases = _wa_cfg.get("trigger_phrases", [])
                _fallback_reply = _wa_cfg.get("fallback_reply", "")

            text_lower = text.lower()
            has_trigger = any(t.lower() in text_lower for t in _trigger_phrases)

            if not has_trigger:
                logger.info("WhatsApp filtered: phone={} instance={} — no trigger phrase (mode=website_leads_only)", phone, instance_name)
                if _fallback_reply and _wa_svc:
                    await _wa_svc.send_text(phone, _fallback_reply)
                return {"status": "filtered", "reason": "no trigger phrase"}

    logger.info("WhatsApp inbound: phone={} name={} tenant={} text='{}'", phone, sender_name, tenant_id, text[:80])

    # Process in background so we don't block Evolution API
    message_id = data.get("key", {}).get("id")
    background_tasks.add_task(_process_whatsapp_message, phone, sender_name, text, message_id, tenant_id)

    return {"status": "queued", "tenant_id": tenant_id}


# ── Demo frontend (PrestaUnion landing + chat widget) ──
# Mounted LAST so it never shadows API routes. Serving same-origin removes any
# CORS friction for the demo. Path resolves in both Docker and local-dev.
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
