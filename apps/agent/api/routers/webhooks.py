"""WhatsApp webhook endpoint.

Extracted from api/main.py (PR6 thin-api split). All business logic is
preserved verbatim — only the module boundary moves.
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Request
from loguru import logger

router = APIRouter()

# Media message types that we acknowledge but don't process
_WA_MEDIA_TYPES = {"imageMessage", "videoMessage", "audioMessage", "documentMessage", "stickerMessage"}


@router.post("/api/v1/webhooks/whatsapp")
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
    instance_name = payload.get("instance")
    import api.main as m
    from shared.config.settings import resolve_whatsapp_tenant
    tenant_wa = resolve_whatsapp_tenant(instance_name)
    if not tenant_wa:
        return {"status": "ignored", "reason": f"unmapped instance: {instance_name}"}

    tenant_id = tenant_wa["tenant_id"]
    wa_mode = tenant_wa.get("mode", "all")
    _wa_svc = m.get_whatsapp_service(tenant_id)

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
            conv = await m.store.get_or_create_async(conversation_id, visitor_id=conversation_id)
            conv.history.clear()
            conv.debtor = type(conv.debtor)()  # fresh debtor state machine
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
        existing = await m.store.get_or_create_async(conversation_id, visitor_id=conversation_id)
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


async def _process_whatsapp_message(
    phone: str, sender_name: str, text: str,
    message_id: str | None = None, tenant_id: str = "demo",
) -> None:
    """Process an inbound WhatsApp message in the background."""
    import re as _re
    from features.conversation.agent import SoreliaAgent
    from features.conversation.response_guard import guard_response
    from features.conversation.response_builder import build_quick_replies
    from features.messaging.whatsapp_formatter import format_for_whatsapp
    from shared.llm import build_llm_provider
    from shared.tool_registry import ToolRegistry

    import api.main as m

    _SUSPICIOUS_PATTERN = _re.compile(
        r"(ignore previous|system prompt|you are now|forget your instructions)",
        _re.IGNORECASE,
    )

    conversation_id = f"wa-{tenant_id}-{phone}"
    _wa_svc = m.get_whatsapp_service(tenant_id)
    _contact_phone = m.get_tenant_contact_phone(tenant_id)

    # Daily rate limit check using phone as visitor_id
    _daily_limit = m.settings.daily_message_limit
    if m.visitor_memory:
        allowed, remaining = await m.visitor_memory.check_daily_limit(conversation_id, limit=_daily_limit)
    else:
        allowed, remaining = m._check_ip_daily_limit(phone, _daily_limit)

    if not allowed:
        if _wa_svc:
            await _wa_svc.send_text(
                phone,
                "Gracias por tu mensaje. Por hoy ya cubrimos bastante. "
                f"Escríbenos mañana o llámanos al {_contact_phone} para una atención directa.",
            )
        return

    conv = await m.store.get_or_create_async(conversation_id, visitor_id=conversation_id)
    await conv.add_user_message_async(text)

    if phone and "phone" not in conv.debtor.collected:
        conv.debtor.collected["phone"] = phone
    if sender_name and "name" not in conv.debtor.collected:
        conv.debtor.collected["name"] = sender_name

    if _SUSPICIOUS_PATTERN.search(text):
        logger.warning("Suspicious WhatsApp input: phone={}", phone)

    debtor_level_before = conv.debtor.level

    try:
        provider = build_llm_provider(m.settings)
        meili_client = None

        def _persist_identity_wa(_profile: dict) -> None:
            conv.identity_verified = True
            conv.debt_context = _profile
            logger.info(
                "Identity resolved via DNI (WhatsApp): conversation={} account={}",
                conv.conversation_id, _profile.get("account_id"),
            )

        _deliverables, _delivery_mode = m._delivery_for(tenant_id)
        registry = ToolRegistry(
            meilisearch_client=meili_client,
            lead_machine=conv.debtor,
            visitor_memory=m.visitor_memory,
            email_service=m.email_service,
            whatsapp_service=_wa_svc,
            identity_verified=conv.identity_verified,
            debt_context=conv.debt_context,
            download_base_url=m.settings.public_base_url,
            tenant_id=tenant_id,
            on_identity_resolved=_persist_identity_wa,
            deliverables=_deliverables,
            delivery_mode=_delivery_mode,
            chathub_outbound=m.chathub_outbound_client,
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
        if m.visitor_memory:
            visitor_profile = await m.visitor_memory.get_visitor(conversation_id)
            if visitor_profile:
                enriched_page_context["visitor"] = {
                    "name": visitor_profile.get("name") or sender_name,
                    "visit_count": visitor_profile.get("visit_count", 1),
                    "projects_viewed": visitor_profile.get("projects_viewed", []),
                }
            else:
                await m.visitor_memory.upsert_visitor(conversation_id, {"name": sender_name, "entry_source": "whatsapp"})

        result = await agent.process_message(
            text=text,
            conversation_id=conv.conversation_id,
            history=conv.history[:-1],
            debtor_state=conv.debtor.get_status(),
            page_context=enriched_page_context,
            channel="whatsapp",
            session_state=conv.session_state,
        )
        content = result["content"]
        wa_ui_actions = result.get("ui_actions", {})
        wa_tool_pairs = result.get("tool_pairs", [])
        m._spawn_analytics(
            tenant_id=tenant_id, session_id=conv.conversation_id,
            channel="whatsapp", user_text=text, result=result,
        )
    except Exception:
        logger.opt(exception=True).error("WhatsApp agent error for phone={}", phone)
        content = (
            "Disculpa, tuve un problema procesando tu mensaje. "
            f"Puedes intentar de nuevo o llamarnos al {_contact_phone}."
        )
        wa_ui_actions = {}
        wa_tool_pairs = []

    content = guard_response(content, conv.history, conv.debtor.get_status())
    await conv.add_assistant_message_async(content)

    if m.visitor_memory:
        await m.visitor_memory.increment_daily_count(conversation_id)
    else:
        m._increment_ip_daily_count(phone)

    if _wa_svc:
        await _wa_svc.send_text(phone, content, incoming_id=message_id)

        wa_quick_replies = build_quick_replies(conv.debtor.get_status(), wa_ui_actions, wa_tool_pairs, content)
        wa_messages = format_for_whatsapp(wa_ui_actions, wa_quick_replies, phone)
        if wa_messages:
            await _wa_svc.send_formatted(wa_messages)

        if m.visitor_memory:
            for tool_name, result in wa_tool_pairs:
                if tool_name == "search_properties" and result.get("properties"):
                    for p in result["properties"]:
                        slug = p.get("slug")
                        if slug:
                            await m.visitor_memory.add_project_viewed(conversation_id, slug)
                    from datetime import datetime as _dt
                    search_entry = {
                        "ts": _dt.now().isoformat(),
                        "district": result.get("filters_applied", {}).get("district"),
                        "bedrooms": result.get("filters_applied", {}).get("bedrooms"),
                        "total": result.get("total", 0),
                    }
                    await m.visitor_memory.add_search(conversation_id, search_entry)
                elif tool_name == "get_property_detail" and result.get("slug"):
                    await m.visitor_memory.add_project_viewed(conversation_id, result["slug"])
    else:
        logger.info("[WA-NO-SERVICE] Would send to {}: {}", phone, content[:100])

    if m.store.db_pool is not None:
        try:
            from shared.persistence.persistence import upsert_debtor

            await upsert_debtor(
                m.store.db_pool, m.store.db_schema,
                conv.conversation_id, conversation_id,
                conv.debtor.collected, conv.debtor.level,
            )
        except Exception:
            logger.opt(exception=True).warning("Failed to persist WhatsApp debtor")

    if m.visitor_memory:
        collected = conv.debtor.get_status().get("collected", {})
        if collected:
            await m.visitor_memory.upsert_visitor(conversation_id, {
                "name": collected.get("name") or sender_name,
                "email": collected.get("email"),
                "phone": collected.get("phone") or phone,
                "lead_data": collected,
            })

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
            case_context = {"name": "", "brochure_url": "", "sales_agent": {}}
            actions = await on_lead_captured(
                lead_data=collected, project=case_context,
                conversation_id=conv.conversation_id,
                email_service=m.email_service, whatsapp_service=_wa_svc,
                notification_email=m.settings.notification_email,
            )
            conv.debtor_notified = True
            logger.info("WhatsApp lead captured: phone={} tenant={} actions={}", phone, tenant_id, actions)
        except Exception:
            logger.opt(exception=True).warning("WhatsApp lead capture hook failed")
