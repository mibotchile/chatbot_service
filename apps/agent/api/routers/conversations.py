"""Chat, conversations, and page-context endpoints.

Extracted from api/main.py (PR6 thin-api split). All business logic is
preserved verbatim — only the module boundary moves.

Global state (store, visitor_memory, email_service, etc.) is accessed from
api.main at call-time to ensure lifespan-initialized singletons are used.
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from loguru import logger
from pydantic import BaseModel, Field, field_validator

from api.deps.widget_gate import require_publishable_key

router = APIRouter()

_UUID_PATTERN = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

_SUSPICIOUS_PATTERN = re.compile(
    r"(ignore previous|system prompt|you are now|forget your instructions)",
    re.IGNORECASE,
)


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


class PageContextRequest(BaseModel):
    project_slug: str | None = None
    project_name: str | None = None
    zone: str | None = None
    entry_source: str = "direct"


@router.post("/api/v1/chat", dependencies=[Depends(require_publishable_key())])
async def chat(request: Request, body: ChatRequest):
    import api.main as m

    # Session token verification (proof-of-origin)
    session_token = request.headers.get("X-Session-Token", "")
    valid, _token_visitor = m._verify_session_token(session_token)
    if not valid:
        return Response(status_code=401, content="Invalid or expired session token")

    # CSRF verification (defense in depth)
    token = request.headers.get("X-CSRF-Token", "")
    if not m._validate_csrf_token(token):
        return Response(status_code=403, content="Invalid CSRF token")

    client_ip = m._client_ip(request)

    # --- Granular per-IP guards (BEFORE spending tokens) ---
    # 1) Short chat window (anti token-burn) — cap messages/min per IP.
    _chat_decision = m.rate_limiter.check_chat_per_min(client_ip)
    if not _chat_decision.allowed:
        logger.warning("rate-limit chat: ip={} reason={}", client_ip, _chat_decision.reason)
        return m._too_many_requests(_chat_decision.retry_after, m._LIMIT_MSG_CHAT)
    # 2) Daily LLM-spend cap per IP — cut once accumulated cost exceeds the cap.
    _cost_decision = m.rate_limiter.check_daily_cost(client_ip)
    if not _cost_decision.allowed:
        logger.warning("rate-limit cost: ip={} reason={}", client_ip, _cost_decision.reason)
        return m._too_many_requests(_cost_decision.retry_after, m._LIMIT_MSG_COST)

    # --- Daily message limit (check BEFORE spending tokens) ---
    _daily_limit = m.settings.daily_message_limit

    if body.visitor_id and m.visitor_memory:
        allowed, remaining = await m.visitor_memory.check_daily_limit(body.visitor_id, limit=_daily_limit)
    else:
        allowed, remaining = m._check_ip_daily_limit(client_ip, _daily_limit)

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
                        {"id": "wa", "label": "Escribir por WhatsApp", "value": f"https://wa.me/{m.get_tenant_contact_phone(body.tenant_id)}?text=Hola%2C+quiero+regularizar+mi+situaci%C3%B3n"},
                        {"id": "call", "label": "Llamar ahora", "value": f"tel:+{m.get_tenant_contact_phone(body.tenant_id)}"},
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
    conv = await m.store.get_or_create_async(body.conversation_id, visitor_id=body.visitor_id)
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
    if body.visitor_id and m.visitor_memory:
        visitor_profile = await m.visitor_memory.get_visitor(body.visitor_id)
        if visitor_profile is None:
            # First visit -- create a stub record with entry source
            await m.visitor_memory.upsert_visitor(body.visitor_id, {"entry_source": entry_source})

        # Track project being viewed
        project_slug = (body.page_context or {}).get("project_slug")
        if project_slug:
            await m.visitor_memory.add_project_viewed(body.visitor_id, project_slug)

    # Snapshot lead level before processing to detect transitions
    debtor_level_before = conv.debtor.level

    # Try to use real agent, fallback to simple response
    has_error = False
    error_message: str | None = None
    # Load tenant config if specified
    _tenant_config = None
    if body.tenant_id:
        from tenancy.tenant_loader import TenantConfig
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

    from shared.config.settings import resolve_api_key
    _api_key = resolve_api_key(body.tenant_id)

    # ── Cobranza identity gate: resolve the campaign token (demo: mock source).
    # The token can arrive on the request or inside page_context (widget). It
    # resolves to a verified borrower profile; account_id is NEVER from the LLM.
    _token = body.campaign_token or (body.page_context or {}).get("campaign_token")
    if _token and not conv.identity_verified:
        # Tenant-aware: prestaunion→mock, prestamype→doris (fixture fallback).
        from features.cobranza.debt_source import resolve_token

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

    # Prefer the explicit public base URL (correct behind Traefik strip-prefix);
    # fall back to the request's base_url in local dev where it's unset.
    _download_base = m.settings.public_base_url.rstrip("/") or str(request.base_url).rstrip("/")

    from shared.llm import LLMError
    from api.tool_registry import ToolRegistry
    from features.conversation.agent import SoreliaAgent
    from features.conversation.response_guard import guard_response
    from features.conversation.response_builder import build_quick_replies

    try:
        provider = m.build_llm_provider(m.settings, api_key_override=_api_key)

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

        # Anti-enumeration: count + check each DNI identification attempt by IP
        # (rate + distinct-DNI sweep). Bound to the real client IP for this turn.
        def _ident_attempt(dni: str):
            return m.rate_limiter.check_identification(client_ip, dni)

        _deliverables, _delivery_mode = m._delivery_for(body.tenant_id)
        _agent_type = (
            _tenant_config.agent_type if _tenant_config is not None else "cobranza"
        )
        _agent_spec = m.agent_type_registry.get(_agent_type)
        registry = ToolRegistry(
            meilisearch_client=meili_client,
            lead_machine=conv.debtor,
            visitor_memory=m.visitor_memory,
            email_service=m.email_service,
            whatsapp_service=m.get_whatsapp_service(body.tenant_id),
            identity_verified=conv.identity_verified,
            debt_context=conv.debt_context,
            download_base_url=_download_base,
            tenant_id=body.tenant_id or "prestaunion",
            on_identity_resolved=_persist_identity,
            on_identification_attempt=_ident_attempt,
            deliverables=_deliverables,
            delivery_mode=_delivery_mode,
            chathub_outbound=m.chathub_outbound_client,
            gated_tools=_agent_spec.gated_tools,
            tools=_agent_spec.tools,
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
            debtor_state=conv.debtor.get_status(),
            page_context=enriched_page_context,
            channel=body.channel,
            session_state=conv.session_state,
        )
        content = result["content"]
        response_id = result["response_id"]
        ui_actions = result.get("ui_actions", {})
        tool_pairs = result.get("tool_pairs", [])
        suggested_replies = result.get("suggested_replies")
        # Resolved intent (canned path sets it; LLM path leaves it None). Captured
        # HERE because ``result`` is later rebound by a ``for ... result in
        # tool_pairs`` loop, so reading it at the chip block would be the tool dict.
        resolved_intent = (result.get("metadata") or {}).get("intent")
        # Accumulate this turn's LLM cost on the IP's daily bucket (same pricing
        # the analytics sink uses). Read by check_daily_cost on the NEXT request.
        try:
            from tenancy.pricing import compute_cost_usd

            _usage = result.get("usage") or {}
            _cost = compute_cost_usd(
                _usage.get("model", ""),
                _usage.get("input_tokens", 0),
                _usage.get("output_tokens", 0),
            )
            m.rate_limiter.add_cost(client_ip, _cost)
        except Exception:  # noqa: BLE001 — cost accounting must never break chat
            logger.opt(exception=True).warning("rate-limit cost accrual failed (ignored)")
        # Fire-and-forget analytics (non-blocking; never raises into the request).
        m._spawn_analytics(
            tenant_id=body.tenant_id,
            session_id=conv.conversation_id,
            channel=body.channel,
            user_text=body.text,
            result=result,
        )
        # Fire-and-forget Layer-3 gestion tracking (terminal hook + Doris sink).
        conv.tenant_id = body.tenant_id
        conv.channel = body.channel
        m._spawn_gestion(conv, result, tool_pairs)  # m dispatches to wiring._spawn_gestion
    except (KeyError, ValueError, TypeError, LLMError) as exc:
        logger.exception("Agent processing error (recoverable)")
        content = m._fallback_response(body.text, conv)
        response_id = f"fallback_{conv.conversation_id[:8]}"
        ui_actions = {}
        tool_pairs = []
        resolved_intent = None
        has_error = True
        error_message = "Error processing your request"
    except Exception:
        # Auth, config, DB, or unexpected errors must propagate
        logger.exception("Agent fatal error")
        raise

    content = guard_response(content, conv.history, conv.debtor.get_status())
    await conv.add_assistant_message_async(content)

    # Increment daily message counter (after successful response)
    if body.visitor_id and m.visitor_memory:
        await m.visitor_memory.increment_daily_count(body.visitor_id)
    else:
        m._increment_ip_daily_count(client_ip)

    # Persist denormalized debtor row for easy querying/export
    if m.store.db_pool is not None:
        try:
            from shared.persistence.persistence import upsert_debtor

            await upsert_debtor(
                m.store.db_pool,
                m.store.db_schema,
                conv.conversation_id,
                body.visitor_id,
                conv.debtor.collected,
                conv.debtor.level,
            )
        except Exception:
            logger.opt(exception=True).warning("Failed to persist denormalized debtor")

    # Track search history from tool results
    if body.visitor_id and m.visitor_memory:
        for tool_name, result in tool_pairs:
            if tool_name == "search_properties" and result.get("properties"):
                from datetime import datetime as _dt
                search_entry = {
                    "ts": _dt.now().isoformat(),
                    "district": result.get("filters_applied", {}).get("district"),
                    "bedrooms": result.get("filters_applied", {}).get("bedrooms"),
                    "total": result.get("total", 0),
                }
                await m.visitor_memory.add_search(body.visitor_id, search_entry)

    # Update visitor memory with any lead data collected during this turn
    if body.visitor_id and m.visitor_memory:
        debtor_status = conv.debtor.get_status()
        collected = debtor_status.get("collected", {})
        if collected:
            await m.visitor_memory.upsert_visitor(body.visitor_id, {
                "name": collected.get("name"),
                "email": collected.get("email"),
                "phone": collected.get("phone"),
                "lead_data": collected,
            })

    # --- Lead capture hook: send brochure + notify sales on DEBTOR transition ---
    debtor_level_after = conv.debtor.level
    _CONTACT_LEVELS = {"DEBTOR", "DEBTOR_VERIFIED"}
    if (
        debtor_level_after in _CONTACT_LEVELS
        and debtor_level_before not in _CONTACT_LEVELS
        and not conv.debtor_notified
    ):
        try:
            from shared.webhooks import on_lead_captured

            collected = conv.debtor.collected
            # TODO Fase 1: build a cobranza "case" context (account/debt summary).
            case_context = {"name": "", "brochure_url": "", "sales_agent": {}}

            actions = await on_lead_captured(
                lead_data=collected,
                project=case_context,
                conversation_id=conv.conversation_id,
                email_service=m.email_service,
                whatsapp_service=m.get_whatsapp_service(body.tenant_id),
                notification_email=m.settings.notification_email,
            )
            conv.debtor_notified = True
            logger.info("Lead captured: conversation={} actions={}", conv.conversation_id, actions)
        except Exception:
            logger.opt(exception=True).warning("Lead capture hook failed (non-blocking)")

    # ── Camino C (Movistar): on WEB handoff, publish the visitor's last message
    # into ChatHub so an asesor picks it up in the panel. Web-only, handoff-only,
    # best-effort (the publisher swallows all errors and has its own timeout, so
    # it never blocks/breaks the web response). NO-OP if no channel_id configured.
    from features.messaging.chathub_adapter import was_escalated
    if body.channel == "web" and was_escalated(tool_pairs):
        from features.messaging.chathub_web_publisher import publish_to_chathub

        _contact_name = (
            (conv.debt_context or {}).get("borrower_name")
            if conv.identity_verified and conv.debt_context
            else None
        ) or conv.conversation_id
        await publish_to_chathub(
            m.settings.chathub_web_channel_id,
            conv.conversation_id,
            _contact_name,
            body.text,
            {"type": "group", "identifier": m.settings.chathub_web_group},
        )

    # ── Quick-reply chips (data-driven, tenant-agnostic) ──────────────────
    # Precedence: a tenant that ships chips in its responses.json OWNS the
    # chips (per resolved intent / conversation state). The LLM's chips are
    # IGNORED for that tenant → zero hallucination (cures leftover off-domain
    # chips like "Ver proyectos" from the old real-estate engine). A tenant
    # WITHOUT chips keeps the legacy LLM-then-heuristic behavior (no break).
    quick_replies = None
    _tenant_owns_chips = False
    _tenant_chips = None
    try:
        from tenancy.responses_spec import ResponsesSpec
        from features.conversation.responses import resolve_chips

        _chip_spec = ResponsesSpec.from_dir(m._tenant_dir(body.tenant_id))
        _tenant_owns_chips = _chip_spec.has_chips
        if _tenant_owns_chips:
            _tenant_chips = resolve_chips(
                _chip_spec,
                intent=resolved_intent,
                identity_verified=bool(conv.identity_verified),
            )
    except Exception:  # noqa: BLE001 — chips must never break the chat response
        logger.opt(exception=True).warning("Tenant chip resolution failed (ignored)")

    if _tenant_owns_chips:
        # Tenant OWNS chips: render the resolved JSON chips (or none for this
        # turn). NEVER fall back to LLM/heuristic chips → zero hallucination.
        if _tenant_chips:
            buttons = [{"id": f"qr-{i}", "label": c, "value": c} for i, c in enumerate(_tenant_chips)]
            quick_replies = {"type": "single_select", "buttons": buttons[:4]}
    else:
        # Legacy path: LLM-generated chips (validated by tool), else heuristic.
        if suggested_replies:
            buttons = [{"id": f"qr-{i}", "label": opt, "value": opt} for i, opt in enumerate(suggested_replies)]
            quick_replies = {"type": "single_select", "buttons": buttons[:4]}
        if not quick_replies:
            quick_replies = build_quick_replies(conv.debtor.get_status(), ui_actions, tool_pairs, content)

    # Current identity state (updated this turn if the user identified via DNI).
    # Lets the widget refresh its identity strip without a token.
    _identity_state = {"verified": bool(conv.identity_verified)}
    if conv.identity_verified and conv.debt_context:
        _identity_state.update({
            "display_name": conv.debt_context.get("borrower_name", ""),
            "business_name": conv.debt_context.get("business_name", ""),
            "status_label": conv.debt_context.get("status_label", ""),
            # The user's own (already verified) DNI — lets the widget enable the
            # comprobante upload after a mid-chat DNI identification.
            "dni": conv.debt_context.get("dni", ""),
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
        "lead": conv.debtor.get_status(),
    }


@router.get("/api/v1/conversations/{conversation_id}/messages", dependencies=[Depends(require_publishable_key())])
async def get_conversation_messages(request: Request, conversation_id: str):
    """Return conversation history from backend state (source of truth)."""
    import re as _re
    if not _re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", conversation_id):
        return Response(status_code=400, content="Invalid conversation ID format")

    import api.main as m
    conv = await m.store.get_or_create_async(conversation_id)

    # Only return if the conversation actually has history
    if not conv.history:
        return {"messages": [], "page_context": {}, "debtor_status": {}}

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
        "debtor_status": conv.debtor.get_status(),
    }


@router.post("/api/v1/page-context", dependencies=[Depends(require_publishable_key())])
async def page_context(request: Request, body: PageContextRequest):
    import api.main as m

    token = request.headers.get("X-CSRF-Token", "")
    if not m._validate_csrf_token(token):
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
