"""Doris sink for Layer-3 gestion tracking.

Maps Postgres rows (gestiones snapshot + gestion_events journal) to the
corresponding Doris OLAP tables via the existing analytics_sink._async_write
transport.

CONTRACT (same as analytics_sink):
  - Every public function is fire-and-forget.
  - ALL work is wrapped in try/except — on ANY error we log and return.
  - A sink failure must NEVER surface to the chat request.
  - If analytics is not configured, _async_write is a cheap no-op already.

Doris table names:
  - bot_gestiones       — one row per conversation snapshot
  - bot_gestion_events  — one row per journal event

datetime_utc is derived from the PG timestamp field (closed_at for snapshots,
ts for events). If the field is already a string it is used as-is; if it is a
datetime it is formatted as ``%Y-%m-%d %H:%M:%S`` (UTC, no tz info — Doris
DATETIME has no timezone).
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from loguru import logger

from features.analytics import analytics_sink

_GESTIONES_TABLE = "bot_gestiones"
_EVENTS_TABLE = "bot_gestion_events"


def _fmt_dt(value: Any) -> str:
    """Format a datetime (or already-formatted string) to Doris DATETIME string."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        # Strip tz info, format as UTC string
        return value.strftime("%Y-%m-%d %H:%M:%S")
    # Assume already a formatted string or castable to str
    return str(value)


def _json_str(value: Any) -> str:
    """JSON-encode a value that may be a list/dict or already a JSON string."""
    if isinstance(value, str):
        # Already serialised (e.g. asyncpg returns JSONB as str in some drivers)
        return value
    return json.dumps(value, ensure_ascii=False)


def _to_doris_gestion(snapshot: dict) -> dict:
    """Map a gestiones PG row to a Doris bot_gestiones row.

    Arrays (capabilities_used) are JSON-encoded strings.
    datetime_utc is derived from closed_at when present, else updated_at.
    """
    ts_source = snapshot.get("closed_at") or snapshot.get("updated_at")
    return {
        "datetime_utc": _fmt_dt(ts_source),
        "conversation_id": snapshot.get("conversation_id") or "",
        "tenant_id": snapshot.get("tenant_id") or "",
        "project_uid": snapshot.get("project_uid") or "",
        "channel": snapshot.get("channel") or "",
        "document": snapshot.get("document") or "",
        "account_id": snapshot.get("account_id") or "",
        "credit_state": snapshot.get("credit_state") or "",
        "outcome": snapshot.get("outcome") or "",
        "outcome_reason": snapshot.get("outcome_reason") or "",
        "capabilities_used": _json_str(snapshot.get("capabilities_used") or []),
        "escalated": snapshot.get("escalated") or False,
        "commitment_date": str(snapshot.get("commitment_date") or ""),
        "commitment_amount": snapshot.get("commitment_amount"),
        "selected_credit_id": snapshot.get("selected_credit_id") or "",
        "schema_version": snapshot.get("schema_version") or 1,
    }


def _to_doris_event(event: dict) -> dict:
    """Map a gestion_events PG row to a Doris bot_gestion_events row.

    payload is JSON-encoded string. datetime_utc is derived from ts.
    """
    return {
        "datetime_utc": _fmt_dt(event.get("ts")),
        "event_id": event.get("event_id"),
        "conversation_id": event.get("conversation_id") or "",
        "event_type": event.get("event_type") or "",
        "intent": event.get("intent") or "",
        "capability": event.get("capability") or "",
        "payload": _json_str(event.get("payload") or {}),
    }


async def record_gestion(*, snapshot: dict) -> None:
    """Fire-and-forget: write one gestiones snapshot row to Doris.

    Never raises. On any failure, logs a warning and returns.
    """
    try:
        row = _to_doris_gestion(snapshot)
        await analytics_sink._async_write(_GESTIONES_TABLE, [row])
    except Exception:  # noqa: BLE001 — gestion sink must never break chat
        logger.warning("gestion_sink.record_gestion failed (ignored)")


async def record_gestion_event(*, event: dict) -> None:
    """Fire-and-forget: write one gestion_events journal row to Doris.

    Never raises. On any failure, logs a warning and returns.
    """
    try:
        row = _to_doris_event(event)
        await analytics_sink._async_write(_EVENTS_TABLE, [row])
    except Exception:  # noqa: BLE001 — gestion sink must never break chat
        logger.warning("gestion_sink.record_gestion_event failed (ignored)")
