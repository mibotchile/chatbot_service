"""Chathub inbound endpoint — ``POST /{bot_path}/chat`` (Olimpo contract).

chathub (the WhatsApp transport) calls ``${OLIMPO_URL}/<botPath>/chat`` with an
Olimpo-style payload (see ``integrations/chathub_adapter``). This router exposes
that route, runs the EXISTING cobranza engine (SoreliaAgent + ToolRegistry +
identity gate + analytics sink) via ``_run_chathub_engine_turn``, and returns one
of the three chathub shapes.

Scope: INBOUND only. Outbound (/messages/send, campaigns, templates) is a later
step and lives elsewhere.

Behind Traefik the container is served under COBRANZA_ROOT_PATH (strip-prefix),
so the externally reachable URL is:
    ${OLIMPO_URL}/<botPath>/chat
  = https://demos.mibot.cl<root_path>/<botPath>/chat
e.g. https://demos.mibot.cl/pubot-c02e78e1/prestamype/chat
(OLIMPO_URL = https://demos.mibot.cl/pubot-c02e78e1, botPath = /prestamype/).

State binding (chathub_conversation_id → conversation) is durable via the engine
``store`` (Postgres-backed when a pool is present; in-memory otherwise). A Redis
backend (COBRANZA_REDIS_URL) is a separate, optional step — the engine already
supports it through ``get_store`` but it is NOT required for staging.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from loguru import logger

from integrations.chathub_adapter import (
    CHATHUB_TOKEN_HEADER,
    ChathubChatAdapter,
    ChathubChatRequest,
    check_auth,
    resolve_tenant,
)

chathub_router = APIRouter()


def _tenant_exists(tenant_id: str) -> bool:
    """True when the tenant has a loadable config (404 guard for routing)."""
    import api.main as m  # late import: live services + helpers

    return m._load_tenant_config(tenant_id) is not None


async def _run_chathub_engine_turn(
    *,
    text: str,
    tenant_id: str,
    conversation_id: str,
    campaign_token: str | None,
    channel: str,
    chathub_conversation_id: str,
    chathub_project_id: str,
    channel_id: str,
) -> dict:
    """Run one engine turn for a chathub message — reuses the /api/v1/chat core.

    Mirrors the engine wiring in ``api.main.chat`` (token gate → ToolRegistry →
    SoreliaAgent → analytics), minus the web-only concerns (CSRF/session token,
    visitor_memory, quick-reply chips, lead webhooks). Returns the engine result
    dict (SoreliaAgent.process_message output) so the adapter can shape it."""
    import api.main as m  # late import: live module-level services

    from config.settings import resolve_api_key
    from core.agent import SoreliaAgent
    from core.llm import build_llm_provider
    from core.response_guard import guard_response
    from integrations.debt_source import resolve_token
    from tools import ToolRegistry

    # Conversation state (durable across turns via the engine store).
    conv = await m.store.get_or_create_async(conversation_id, visitor_id=conversation_id)
    await conv.add_user_message_async(text)

    # Tenant config (drives prompt + tool exclusions).
    tenant_config = None
    cfg = m._load_tenant_config(tenant_id)
    if cfg is not None:
        try:
            from core.tenant_loader import TenantConfig

            tenant_config = TenantConfig.from_directory(m._tenant_dir(tenant_id))
        except Exception:
            logger.opt(exception=True).warning("chathub: failed to load tenant config {}", tenant_id)

    # ── Identity gate: resolve the CT- token (second factor that closes the
    # WhatsApp gate). DNI-first remains available via the engine's
    # identificar_cliente tool (same gate). ──
    if campaign_token and not conv.identity_verified:
        # The deep-link marker is "CT-"; the underlying campaign token may be
        # seeded WITH or WITHOUT that prefix. Try the raw value first, then the
        # bare value (prefix stripped) so either seeding convention resolves.
        candidates = [campaign_token]
        if campaign_token.startswith("CT-"):
            candidates.append(campaign_token[3:])
        profile = None
        for cand in candidates:
            profile = resolve_token(cand, tenant_id=tenant_id)
            if profile:
                break
        if profile:
            conv.identity_verified = True
            conv.debt_context = profile
            logger.info(
                "chathub identity resolved via token: conv={} account={}",
                conversation_id, profile.get("account_id"),
            )
        else:
            logger.info("chathub campaign token did not resolve: conv={}", conversation_id)

    def _persist_identity(profile: dict) -> None:
        conv.identity_verified = True
        conv.debt_context = profile

    api_key = resolve_api_key(tenant_id)
    download_base = m.settings.public_base_url.rstrip("/")

    try:
        provider = build_llm_provider(m.settings, api_key_override=api_key)
        _deliverables, _delivery_mode = m._delivery_for(tenant_id)
        registry = ToolRegistry(
            lead_machine=conv.lead,
            visitor_memory=m.visitor_memory,
            email_service=m.email_service,
            whatsapp_service=m.get_whatsapp_service(tenant_id),
            identity_verified=conv.identity_verified,
            debt_context=conv.debt_context,
            download_base_url=download_base,
            tenant_id=tenant_id,
            on_identity_resolved=_persist_identity,
            deliverables=_deliverables,
            delivery_mode=_delivery_mode,
            chathub_outbound=m.chathub_outbound_client,
        )
        agent = SoreliaAgent(provider=provider, tool_registry=registry, tenant=tenant_config)

        page_context: dict = {}
        if conv.identity_verified and conv.debt_context:
            page_context["identity"] = {
                "verified": True,
                "borrower_name": conv.debt_context.get("borrower_name"),
                "business_name": conv.debt_context.get("business_name"),
                "loan_number": conv.debt_context.get("loan_number"),
                "status_label": conv.debt_context.get("status_label"),
            }
        else:
            page_context["identity"] = {"verified": False}

        result = await agent.process_message(
            text=text,
            conversation_id=conv.conversation_id,
            history=conv.history[:-1],
            lead_state=conv.lead.get_status(),
            page_context=page_context,
            channel=channel,
        )
    except Exception:
        logger.opt(exception=True).error("chathub engine error: conv={}", conversation_id)
        # Degrade to a safe text turn — never 500 the transport.
        result = {
            "content": (
                "Disculpa, tuve un inconveniente procesando tu mensaje. "
                "Inténtalo de nuevo en un momento, por favor."
            ),
            "ui_actions": {},
            "tool_pairs": [],
            "usage": {},
        }

    content = guard_response(result.get("content", ""), conv.history, conv.lead.get_status())
    result["content"] = content
    await conv.add_assistant_message_async(content)

    # Analytics — reuse the existing fire-and-forget sink (channel=whatsapp keeps
    # chathub turns in the same project_uid/cost/datetime stream). NEVER blocks.
    m._spawn_analytics(
        tenant_id=tenant_id,
        session_id=conv.conversation_id,
        channel=channel,
        user_text=text,
        result=result,
    )

    return result


# Single shared adapter (stateless — engine state lives in the store).
_adapter = ChathubChatAdapter(engine_runner=_run_chathub_engine_turn)


@chathub_router.post("/{bot_path}/chat")
async def chathub_chat(bot_path: str, body: ChathubChatRequest, request: Request):
    """Inbound chathub message → cobranza engine → chathub response shape.

    - Auth: optional shared secret via ``X-Chathub-Token`` (open when
      COBRANZA_CHATHUB_TOKEN is unset — compat with chathub's current client).
    - Tenant routing: ``bot_path`` → tenant_id (COBRANZA_CHATHUB_BOTPATH_MAP or
      sanitized-slug fallback). Unknown tenant → 404.
    """
    import api.main as m

    if not check_auth(request.headers.get(CHATHUB_TOKEN_HEADER)):
        logger.warning("chathub: bad/missing shared secret for bot_path={}", bot_path)
        return JSONResponse(status_code=401, content={"detail": "Unauthorized"})

    tenant_id = resolve_tenant(bot_path, _tenant_exists)
    if tenant_id is None:
        logger.warning("chathub: unresolved bot_path={} (no tenant)", bot_path)
        return JSONResponse(status_code=404, content={"detail": "Unknown bot path"})

    tenant_cfg = m._load_tenant_config(tenant_id)
    return await _adapter.handle(body=body, tenant_id=tenant_id, tenant_cfg=tenant_cfg)
