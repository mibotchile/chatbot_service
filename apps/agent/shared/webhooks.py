"""Dramatiq actors for async webhook delivery + lead capture hooks.

Actors:
    send_crm_webhook     - fires when lead transitions (PRE_LEAD, LEAD, LEAD_ENRICHED)
    send_visit_request   - fires when user schedules a visit
    send_brochure_request - fires when user requests a brochure

Functions:
    on_lead_captured     - fires when lead reaches CONTACT level (has name + email/phone)

Broker is configured from settings.redis_url. Each actor retries up to 3 times
with exponential backoff (1s min, 30s max).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
import dramatiq
from dramatiq.brokers.stub import StubBroker
from loguru import logger

if TYPE_CHECKING:
    from shared.delivery.email_delivery import EmailService


def _setup_broker() -> None:
    """Configure Redis broker unless a StubBroker is already active (tests)."""
    current = dramatiq.get_broker()
    if isinstance(current, StubBroker):
        return
    from dramatiq.brokers.redis import RedisBroker
    from shared.config.settings import settings
    broker = RedisBroker(url=settings.redis_url)
    dramatiq.set_broker(broker)


_setup_broker()


def _post_webhook(url: str, payload: dict, event: str) -> None:
    """Synchronous HTTP POST — dramatiq actors run in threads, not async."""
    if not url:
        logger.debug("Webhook URL empty for event={}, skipping", event)
        return

    logger.info("Sending webhook event={} to {}", event, url)
    try:
        resp = httpx.post(url, json=payload, timeout=10.0)
        resp.raise_for_status()
        logger.info("Webhook delivered event={} status={}", event, resp.status_code)
    except httpx.HTTPStatusError as exc:
        logger.error(
            "Webhook HTTP error event={} status={} body={}",
            event,
            exc.response.status_code,
            exc.response.text[:200],
        )
        raise
    except httpx.RequestError as exc:
        logger.error("Webhook request failed event={} error={}", event, exc)
        raise


@dramatiq.actor(max_retries=3, min_backoff=1_000, max_backoff=30_000)
def send_crm_webhook(
    url: str,
    conversation_id: str,
    previous_level: str,
    new_level: str,
    collected: dict,
) -> None:
    """Fire when lead level changes."""
    _post_webhook(
        url=url,
        payload={
            "event": "lead_transition",
            "conversation_id": conversation_id,
            "previous_level": previous_level,
            "new_level": new_level,
            "collected": collected,
        },
        event="lead_transition",
    )


@dramatiq.actor(max_retries=3, min_backoff=1_000, max_backoff=30_000)
def send_visit_request(
    url: str,
    phone: str,
    property_slug: str,
    preferred_date: str,
    conversation_id: str = "",
) -> None:
    """Fire when user schedules a visit."""
    _post_webhook(
        url=url,
        payload={
            "event": "visit_scheduled",
            "phone": phone,
            "property_slug": property_slug,
            "preferred_date": preferred_date,
            "conversation_id": conversation_id,
        },
        event="visit_scheduled",
    )


@dramatiq.actor(max_retries=3, min_backoff=1_000, max_backoff=30_000)
def send_brochure_request(
    url: str,
    email: str,
    property_slug: str,
    conversation_id: str = "",
) -> None:
    """Fire when user requests a brochure."""
    _post_webhook(
        url=url,
        payload={
            "event": "brochure_requested",
            "email": email,
            "property_slug": property_slug,
            "conversation_id": conversation_id,
        },
        event="brochure_requested",
    )


async def on_lead_captured(
    lead_data: dict,
    project: dict,
    conversation_id: str,
    email_service: EmailService | None = None,
    whatsapp_service: Any | None = None,
    notification_email: str = "ventas@novainmobiliaria.pe",
) -> dict:
    """Fire when lead gives contact info (name + email or phone).

    Sends brochure to customer (via email and/or WhatsApp) and notifies sales team.
    Returns a summary of actions taken.
    """
    actions: dict = {
        "brochure_sent": False,
        "sales_notified": False,
        "wa_brochure_sent": False,
        "wa_sales_notified": False,
    }

    project_name = project.get("name", "Proyecto Nova")
    brochure_url = project.get("brochure_url", "")
    sales_agent = project.get("sales_agent", {})
    customer_email = lead_data.get("email")
    customer_phone = lead_data.get("phone")
    customer_name = lead_data.get("name", "estimado/a cliente")

    # --- Email channel ---
    if email_service:
        # 1. Send brochure to customer if we have email + brochure_url
        if customer_email and brochure_url:
            sent = await email_service.send_brochure(
                to_email=customer_email,
                customer_name=customer_name,
                project_name=project_name,
                brochure_url=brochure_url,
                sales_agent=sales_agent,
            )
            actions["brochure_sent"] = sent
            logger.info(
                "Brochure email {} to {} for {}",
                "sent" if sent else "FAILED",
                customer_email,
                project_name,
            )

        # 2. Notify sales team via email
        if notification_email:
            notified = await email_service.notify_sales_agent(
                agent_email=notification_email,
                lead=lead_data,
                project_name=project_name,
                conversation_id=conversation_id,
            )
            actions["sales_notified"] = notified
    else:
        logger.warning("on_lead_captured: no EmailService -- skipping email delivery")

    # --- WhatsApp channel ---
    if whatsapp_service and customer_phone:
        # 3. Send brochure via WhatsApp if project has brochure_url
        if brochure_url:
            try:
                wa_sent = await whatsapp_service.send_brochure(
                    phone=customer_phone,
                    customer_name=customer_name,
                    project_name=project_name,
                    brochure_url=brochure_url,
                    sales_agent=sales_agent,
                )
                actions["wa_brochure_sent"] = wa_sent
                logger.info(
                    "WhatsApp brochure {} to {} for {}",
                    "sent" if wa_sent else "FAILED",
                    customer_phone,
                    project_name,
                )
            except Exception:
                logger.opt(exception=True).warning("WhatsApp brochure send failed (non-blocking)")

        # 4. Notify sales agent via WhatsApp
        sales_phone = sales_agent.get("phone", "")
        if sales_phone:
            try:
                wa_notified = await whatsapp_service.handoff_to_sales(
                    sales_phone=sales_phone,
                    lead=lead_data,
                    project_name=project_name,
                )
                actions["wa_sales_notified"] = wa_notified
            except Exception:
                logger.opt(exception=True).warning("WhatsApp sales notification failed (non-blocking)")
    elif customer_phone and not whatsapp_service:
        logger.info(
            "Lead phone captured: {} -- WhatsApp not configured (conversation={})",
            customer_phone,
            conversation_id,
        )

    return actions
