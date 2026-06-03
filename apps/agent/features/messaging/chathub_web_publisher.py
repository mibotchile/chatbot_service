"""ChatHub web publisher — publishes a LANDING WEB conversation into ChatHub on
handoff (camino C, Movistar Chile pattern).

When a web (landing) conversation escalates to a human, Ada pushes the visitor's
last message into ChatHub's public ``incomingMessage`` webhook so the asesor sees
it in the panel as if it had arrived from the customer. This is the inverse of the
inbound adapter (``chathub_adapter``): there ChatHub feeds US; here WE feed ChatHub.

Contract (verified) — ``POST <webhook>/olimpo/incomingMessage`` with an
``IncomingOlimpoMessage`` body:

    {
      "metadata": {},
      "channel":  {"id": "<channel_id>"},
      "receiver": {"type": "group", "identifier": "<cola>"},
      "contact":  {"id": "<session_id>", "name": "<nombre>"},
      "message":  {"from": "<session_id>", "id": "<uuid>",
                   "timestamp": "YYYY-MM-DD HH:MM:SS",
                   "type": "text", "text": {"body": "<texto>"}}
    }

Quirks honored (replicating the legacy/Movistar behavior):
  · No auth (Content-Type json). The host's SSL may be self-signed, so we default
    to ``verify=False`` (COBRANZA_CHATHUB_WEB_VERIFY_SSL) with the urllib3 warning
    suppressed — same as ``core.whatsapp_service``.
  · Fire-and-forget: ChatHub answers ``true`` and nothing useful. We NEVER raise —
    a publish failure must not break the response to the web visitor.
  · Disabled by default: if ``COBRANZA_CHATHUB_WEB_CHANNEL_ID`` is empty the
    publisher is a NO-OP (logs once and returns False). The channel is registered
    in ChatHub separately and only consumed here via config.
  · Like Movistar, this fires ONLY on handoff, sending the customer's last message
    plus the destination ``receiver`` (the queue/group).

TODO (design note): if full-history mirroring is wanted later (not just the
handoff turn), call ``publish_to_chathub`` on EVERY web turn instead of only on
handoff. For now we replicate Movistar: handoff-only.
"""

from __future__ import annotations

import uuid
import warnings
from datetime import datetime, timezone

import httpx
from loguru import logger

from shared.config.settings import settings

_LOG_PREFIX = "[chathub-web-publisher]"


def _now_olimpo_ts() -> str:
    """ChatHub/Olimpo timestamp format: ``YYYY-MM-DD HH:MM:SS`` in UTC."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def build_incoming_payload(
    *,
    channel_id: str,
    session_id: str,
    contact_name: str,
    text: str,
    receiver: dict,
) -> dict:
    """Assemble the exact ``IncomingOlimpoMessage`` body for the webhook.

    ``message.id`` is a fresh uuid4; ``message.from`` mirrors the contact id
    (the web session id) so ChatHub attributes the bubble to the same contact."""
    return {
        "metadata": {},
        "channel": {"id": channel_id},
        "receiver": receiver,
        "contact": {"id": session_id, "name": contact_name or session_id},
        "message": {
            "from": session_id,
            "id": str(uuid.uuid4()),
            "timestamp": _now_olimpo_ts(),
            "type": "text",
            "text": {"body": text or ""},
        },
    }


async def publish_to_chathub(
    channel_id: str,
    session_id: str,
    contact_name: str,
    text: str,
    receiver: dict,
    *,
    timeout: float | None = None,
) -> bool:
    """Publish one web message into ChatHub (fire-and-forget).

    NO-OP (returns False) when ``channel_id`` is empty (publisher disabled).
    NEVER raises: any error is logged with the ``[chathub-web-publisher]`` prefix
    and swallowed so the web response is unaffected. Returns True only on a 2xx.
    """
    if not channel_id:
        logger.info("{} disabled (no channel_id) — skipping publish", _LOG_PREFIX)
        return False

    url = settings.chathub_webhook_url
    verify_ssl = settings.chathub_web_verify_ssl
    effective_timeout = timeout if timeout is not None else settings.chathub_web_timeout

    payload = build_incoming_payload(
        channel_id=channel_id,
        session_id=session_id,
        contact_name=contact_name,
        text=text,
        receiver=receiver,
    )

    try:
        with warnings.catch_warnings():
            # Self-signed host: suppress the urllib3/httpx insecure-request noise
            # (same posture as core.whatsapp_service).
            warnings.simplefilter("ignore")
            async with httpx.AsyncClient(verify=verify_ssl, timeout=effective_timeout) as client:
                resp = await client.post(url, json=payload)
        if resp.status_code // 100 == 2:
            logger.info(
                "{} published | channel={} session={} group={}",
                _LOG_PREFIX, channel_id, session_id, receiver.get("identifier"),
            )
            return True
        logger.warning(
            "{} non-2xx ({}) publishing session={} — swallowed",
            _LOG_PREFIX, resp.status_code, session_id,
        )
        return False
    except Exception as exc:  # noqa: BLE001 — fire-and-forget, must never propagate
        logger.warning(
            "{} publish failed for session={}: {} — swallowed", _LOG_PREFIX, session_id, exc
        )
        return False
