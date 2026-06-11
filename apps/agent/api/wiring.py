"""Shared helpers and singletons for the Sorelia API.

This module contains:
  - Application singletons (store, visitor_memory, email_service, whatsapp_service,
    chathub_outbound_client) — initialized at import with defaults; lifespan replaces them.
  - Singleton accessors (get_whatsapp_service)
  - Tenant resolution helpers (_tenant_dir, _load_tenant_config, _tenant_project_uid,
    get_tenant_contact_phone, _delivery_for)
  - Conversation fallback (_fallback_response)
  - Analytics fire-and-forget (_emit_analytics, _spawn_analytics)
  - Startup helper (_register_whatsapp_webhook)

Routers access all of these via ``import api.main as m`` — main.py re-exports
everything from this module so ``m.get_whatsapp_service``, ``m._tenant_dir``, etc.
all resolve correctly.

Dependency direction: api.wiring → features, shared, tenancy (never → api.routers or api.main).
"""

from __future__ import annotations

import re

import httpx
from loguru import logger

from features.conversation.persistence.state import get_store
from features.conversation.persistence.visitor_memory import VisitorMemory
from shared.delivery.email_delivery import EmailService
from features.messaging.whatsapp_service import WhatsAppService
from features.messaging.chathub_outbound import ChathubOutboundClient
from shared.config.settings import settings

# Layer-3 gestion imports — at module level so they are patchable in tests.
from shared.persistence.persistence import append_gestion_event, upsert_gestion
from features.analytics.gestion_sink import record_gestion, record_gestion_event
from features.messaging.chathub_adapter import was_escalated

# ---------------------------------------------------------------------------
# Application singletons
# ---------------------------------------------------------------------------
# Initialized at module import with safe defaults; lifespan replaces them with
# DB-backed instances once the connection pool is ready.

store = get_store()
visitor_memory: VisitorMemory | None = None
email_service: EmailService | None = None
whatsapp_service: WhatsAppService | None = None
whatsapp_services: dict[str, WhatsAppService] = {}  # tenant_id → WhatsAppService

# ChatHub OUTBOUND — stateless config singleton; no-op/simulate when URL is unset.
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
# Tenant helpers
# ---------------------------------------------------------------------------

def _tenant_dir(tenant_id: str):
    """Locate a tenant directory in both Docker (/app/tenants) and dev layouts."""
    from pathlib import Path as _P

    d = _P("/app/tenants") / tenant_id
    if not d.exists():
        d = _P(__file__).resolve().parent.parent.parent.parent / "tenants" / tenant_id
    return d


def _load_tenant_config(tenant_id: str) -> dict | None:
    """Read the raw tenant.config.json for a tenant. None if missing/invalid."""
    import json as _j

    if not re.fullmatch(r"[a-z0-9_\-]{1,64}", tenant_id or ""):
        return None
    cfg_path = _tenant_dir(tenant_id) / "tenant.config.json"
    if not cfg_path.exists():
        return None
    try:
        return _j.loads(cfg_path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def _tenant_project_uid(tenant_id: str | None) -> str | None:
    """Resolve the tenant's project_uid (None if tenant/key missing)."""
    if not tenant_id:
        return None
    cfg = _load_tenant_config(tenant_id)
    return cfg.get("project_uid") if cfg else None


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
    return settings.soul.whatsapp.replace("+", "") if hasattr(settings, "soul") else ""


def _delivery_for(tenant_id: str | None) -> tuple[dict, str]:
    """Resolve (deliverables_spec, delivery_mode) for a tenant."""
    if not tenant_id:
        return {}, "simulate"
    cfg = _load_tenant_config(tenant_id) or {}
    data_source = (cfg.get("data_source") or "mock").strip().lower()
    delivery_mode = "simulate" if data_source == "mock" else "real"
    deliverables: dict = {}
    try:
        from tenancy.responses_spec import ResponsesSpec
        spec = ResponsesSpec.from_dir(_tenant_dir(tenant_id))
        deliverables = spec.deliverables or {}
    except Exception:
        logger.opt(exception=True).warning("Failed to load deliverables for {}", tenant_id)
    return deliverables, delivery_mode


def _fallback_response(text: str, conv) -> str:
    """Simple fallback when LLM is unavailable."""
    lead = conv.debtor
    if lead.level == "VISITOR":
        return "Hola, gracias por escribir. En un momento te ayudo con tu consulta."
    return "Entendido. En este momento no puedo consultar el detalle, pero te ayudo por WhatsApp."


# ---------------------------------------------------------------------------
# Analytics helpers
# ---------------------------------------------------------------------------

_analytics_tasks: set = set()


async def _emit_analytics(*, tenant_id, session_id, channel, user_text, result) -> None:
    """Fire the analytics sink for one completed turn (fire-and-forget)."""
    import uuid as _uuid
    from features.analytics import analytics_sink

    try:
        if not analytics_sink.analytics_enabled():
            return
        project_uid = _tenant_project_uid(tenant_id)
        interaction_id = str(_uuid.uuid4())
        usage = result.get("usage") or {}
        _source = result.get("response_source") or "llm"
        _tools = list(result.get("tools_called") or [])
        _tools.append(f"source:{_source}")
        await analytics_sink.record_interaction(
            project_uid=project_uid, tenant_id=tenant_id or "",
            session_id=session_id, channel=channel,
            interaction_id=interaction_id, user_text=user_text,
            assistant_text=result.get("content", ""),
            tools_called=_tools, latency_ms=result.get("latency_ms"),
        )
        await analytics_sink.record_llm_usage(
            project_uid=project_uid, tenant_id=tenant_id or "",
            session_id=session_id, interaction_id=interaction_id,
            provider=usage.get("provider", ""), model=usage.get("model", ""),
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
        )
    except Exception:
        logger.opt(exception=True).warning("analytics emit failed (ignored)")


def _spawn_analytics(**kwargs) -> None:
    """Schedule _emit_analytics as a background task (keeps a ref to avoid GC)."""
    import asyncio as _asyncio

    try:
        task = _asyncio.create_task(_emit_analytics(**kwargs))
        _analytics_tasks.add(task)
        task.add_done_callback(_analytics_tasks.discard)
    except RuntimeError:
        pass


# ---------------------------------------------------------------------------
# Gestion (Layer-3) fire-and-forget helpers
# ---------------------------------------------------------------------------

async def _emit_gestion(conv, result, tool_pairs) -> None:
    """Write one turn's gestion data to Postgres + Doris (fire-and-forget).

    Detection logic is fully data-driven via TERMINAL_SIGNALS and
    INTENT_TO_CAPABILITY from gestion_catalog — no strings hardcoded here.

    Uses the module-level ``store`` singleton (same pattern as _emit_analytics).

    Guards:
      - store.db_pool is None → silent no-op.
      - Any exception is caught and logged; never propagates to the request path.
    """
    from datetime import datetime as _dt, timezone as _tz

    try:
        if store.db_pool is None:
            return

        from features.analytics.gestion_catalog import (
            TERMINAL_SIGNALS,
            INTENT_TO_CAPABILITY,
            EventType,
            Outcome,
        )
        from features.analytics.gestion_derivation import derive_outcome

        pool = store.db_pool
        schema = store.db_schema
        conversation_id = conv.conversation_id
        session_state = conv.session_state or {}
        resolved_intent = (result.get("metadata") or {}).get("intent")

        # -- Detect terminal signals (data-driven from TERMINAL_SIGNALS) --
        tool_names = {name for name, _ in (tool_pairs or [])}

        commitment_registered = bool(
            tool_names & set(TERMINAL_SIGNALS.get("commitment_registered", []))
        )
        proof_submitted = bool(
            tool_names & set(TERMINAL_SIGNALS.get("proof_submitted", []))
        )
        identity_failed = any(
            session_state.get(k) for k in TERMINAL_SIGNALS.get("identity_failed", [])
        )
        fallback_exhausted = any(
            session_state.get(k) for k in TERMINAL_SIGNALS.get("fallback_exhausted", [])
        )
        escalated = was_escalated(list(tool_pairs or []))

        # info_provided: intent matches known info intents
        info_provided = resolved_intent in TERMINAL_SIGNALS.get("info_provided_intents", [])
        # identified: intent matches identity intents
        identified = resolved_intent in TERMINAL_SIGNALS.get("identified_intents", [])

        is_terminal = (
            commitment_registered
            or proof_submitted
            or identity_failed
            or fallback_exhausted
            or escalated
        )

        # -- Check if already closed (first-close-wins guard) --
        existing = await pool.fetchrow(
            f"SELECT closed_at, capabilities_used FROM \"{schema}\".gestiones"
            " WHERE conversation_id = $1",
            conversation_id,
        )
        already_closed = existing is not None and existing["closed_at"] is not None

        # -- Determine capability for this turn --
        capability = INTENT_TO_CAPABILITY.get(resolved_intent or "")
        if not capability:
            # Fall back to first matching tool name
            for t in tool_names:
                if t in INTENT_TO_CAPABILITY:
                    capability = INTENT_TO_CAPABILITY[t]
                    break

        # -- Append capability_used event (every non-empty turn) --
        cap_event_row = None
        if capability:
            cap_event_row = await append_gestion_event(
                pool, schema, conversation_id,
                event_type=EventType.capability_used,
                intent=resolved_intent,
                capability=capability,
            )
            await record_gestion_event(event=cap_event_row)

        # -- Upsert snapshot (open state or terminal close) --
        now = _dt.now(_tz.utc)
        upsert_fields: dict = {
            "tenant_id": getattr(conv, "tenant_id", None),
            "channel": getattr(conv, "channel", None),
            "project_uid": _tenant_project_uid(getattr(conv, "tenant_id", None)),
            "schema_version": 1,
            "closed_at": None,
            "outcome": None,
            "outcome_reason": None,
            "escalated": escalated or None,
        }
        if capability:
            upsert_fields["capabilities_used"] = [capability]

        terminal_event_row = None
        if is_terminal and not already_closed:
            outcome, reason = derive_outcome(
                session_state=session_state,
                resolved_intent=resolved_intent,
                was_escalated=escalated,
                identity_failed=identity_failed,
                commitment_registered=commitment_registered,
                proof_submitted=proof_submitted,
                fallback_exhausted=fallback_exhausted,
                identified=identified,
                info_provided=info_provided,
            )
            outcome_val = outcome.value if hasattr(outcome, "value") else str(outcome)
            upsert_fields["closed_at"] = now
            upsert_fields["outcome"] = outcome_val
            upsert_fields["outcome_reason"] = reason

            terminal_event_row = await append_gestion_event(
                pool, schema, conversation_id,
                event_type=EventType.terminal,
                intent=resolved_intent,
                capability=capability,
                payload={"outcome": outcome_val, "outcome_reason": reason},
            )
            await record_gestion_event(event=terminal_event_row)

        await upsert_gestion(pool, schema, conversation_id, fields=upsert_fields)

        # -- Send snapshot to Doris using the fields we just upserted --
        # Avoids an extra round-trip; the sink only needs stable scalar fields.
        doris_snapshot = {
            "conversation_id": conversation_id,
            "tenant_id": upsert_fields.get("tenant_id") or "",
            "project_uid": upsert_fields.get("project_uid") or "",
            "channel": upsert_fields.get("channel") or "",
            "capabilities_used": upsert_fields.get("capabilities_used") or [],
            "outcome": upsert_fields.get("outcome") or "",
            "outcome_reason": upsert_fields.get("outcome_reason") or "",
            "escalated": escalated,
            "schema_version": 1,
            "closed_at": upsert_fields.get("closed_at"),
            "updated_at": now,
        }
        await record_gestion(snapshot=doris_snapshot)

    except Exception:
        logger.opt(exception=True).warning("_emit_gestion failed (ignored)")


def _spawn_gestion(conv, result, tool_pairs) -> None:
    """Schedule _emit_gestion as a background task (fire-and-forget).

    Mirrors _spawn_analytics exactly: asyncio.create_task, ref kept in
    _analytics_tasks to prevent GC before completion.
    """
    import asyncio as _asyncio

    try:
        task = _asyncio.create_task(_emit_gestion(conv, result, tool_pairs))
        _analytics_tasks.add(task)
        task.add_done_callback(_analytics_tasks.discard)
    except RuntimeError:
        pass


# ---------------------------------------------------------------------------
# Startup helpers
# ---------------------------------------------------------------------------

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
                url, json=payload,
                headers={"apikey": settings.whatsapp_api_key, "Content-Type": "application/json"},
                timeout=10.0,
            )
        if resp.status_code in (200, 201):
            logger.info("WhatsApp webhook registered: {}", settings.whatsapp_webhook_url)
        else:
            logger.error("WhatsApp webhook registration failed: status={} body={}", resp.status_code, resp.text[:300])
    except httpx.RequestError as exc:
        logger.error("WhatsApp webhook registration request failed: {}", exc)
