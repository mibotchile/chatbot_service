"""Dramatiq actors for async webhook delivery.

Actors:
    send_crm_webhook     - fires when lead transitions (PRE_LEAD, LEAD, LEAD_ENRICHED)

Broker is configured from settings.redis_url. Each actor retries up to 3 times
with exponential backoff (1s min, 30s max).
"""

from __future__ import annotations

import httpx
import dramatiq
from dramatiq.brokers.stub import StubBroker
from loguru import logger


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
