"""Tests for gestion_sink.py (Task 4.1 RED → 4.2 GREEN).

Verifies:
- record_gestion calls analytics_sink._async_write with table "bot_gestiones"
  and a row that has datetime_utc and capabilities_used as a JSON string.
- record_gestion_event calls analytics_sink._async_write with table
  "bot_gestion_events" and a row that has datetime_utc.
- Neither function raises when _async_write raises Exception.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_snapshot(**overrides) -> dict:
    """Minimal gestiones PG row."""
    base = {
        "conversation_id": "conv-abc-123",
        "tenant_id": "tenant-x",
        "project_uid": "proj-001",
        "channel": "web",
        "document": "12345678",
        "account_id": "ACC-001",
        "credit_state": None,
        "outcome": "payment_commitment_registered",
        "outcome_reason": None,
        "capabilities_used": ["identificacion", "compromiso"],
        "escalated": False,
        "commitment_date": None,
        "commitment_amount": None,
        "selected_credit_id": None,
        "schema_version": 1,
        "created_at": datetime(2026, 6, 10, 12, 0, 0, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 6, 10, 12, 1, 0, tzinfo=timezone.utc),
        "closed_at": datetime(2026, 6, 10, 12, 1, 0, tzinfo=timezone.utc),
    }
    base.update(overrides)
    return base


def _make_event(**overrides) -> dict:
    """Minimal gestion_events PG row."""
    base = {
        "event_id": 42,
        "conversation_id": "conv-abc-123",
        "ts": datetime(2026, 6, 10, 12, 0, 30, tzinfo=timezone.utc),
        "event_type": "terminal",
        "intent": "payment_commitment",
        "capability": "compromiso",
        "payload": {"outcome": "payment_commitment_registered"},
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# 4.1.a — record_gestion calls _async_write with correct table + row shape
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_record_gestion_calls_async_write_with_correct_table():
    from features.analytics import gestion_sink

    mock_write = AsyncMock()
    with patch("features.analytics.analytics_sink._async_write", mock_write):
        await gestion_sink.record_gestion(snapshot=_make_snapshot())

    mock_write.assert_called_once()
    call_args = mock_write.call_args
    table = call_args[0][0]
    assert table == "bot_gestiones", f"Expected 'bot_gestiones', got {table!r}"


@pytest.mark.asyncio
async def test_record_gestion_row_has_datetime_utc():
    from features.analytics import gestion_sink

    mock_write = AsyncMock()
    with patch("features.analytics.analytics_sink._async_write", mock_write):
        await gestion_sink.record_gestion(snapshot=_make_snapshot())

    rows = mock_write.call_args[0][1]
    assert len(rows) == 1
    row = rows[0]
    assert "datetime_utc" in row, "Row missing datetime_utc"
    # datetime_utc must be a formatted string (not a datetime object)
    assert isinstance(row["datetime_utc"], str), "datetime_utc must be a string"


@pytest.mark.asyncio
async def test_record_gestion_capabilities_used_is_json_string():
    from features.analytics import gestion_sink

    mock_write = AsyncMock()
    snapshot = _make_snapshot(capabilities_used=["identificacion", "compromiso"])
    with patch("features.analytics.analytics_sink._async_write", mock_write):
        await gestion_sink.record_gestion(snapshot=snapshot)

    rows = mock_write.call_args[0][1]
    row = rows[0]
    assert "capabilities_used" in row
    caps = row["capabilities_used"]
    assert isinstance(caps, str), "capabilities_used must be JSON-encoded string"
    decoded = json.loads(caps)
    assert decoded == ["identificacion", "compromiso"]


@pytest.mark.asyncio
async def test_record_gestion_capabilities_used_list_object_also_works():
    """capabilities_used may arrive as a list (from asyncpg) or already a str."""
    from features.analytics import gestion_sink

    mock_write = AsyncMock()
    snapshot = _make_snapshot(capabilities_used=["pago"])
    with patch("features.analytics.analytics_sink._async_write", mock_write):
        await gestion_sink.record_gestion(snapshot=snapshot)

    rows = mock_write.call_args[0][1]
    caps = rows[0]["capabilities_used"]
    assert isinstance(caps, str)
    assert json.loads(caps) == ["pago"]


# ---------------------------------------------------------------------------
# 4.1.b — record_gestion_event calls _async_write with correct table + row shape
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_record_gestion_event_calls_async_write_with_correct_table():
    from features.analytics import gestion_sink

    mock_write = AsyncMock()
    with patch("features.analytics.analytics_sink._async_write", mock_write):
        await gestion_sink.record_gestion_event(event=_make_event())

    mock_write.assert_called_once()
    table = mock_write.call_args[0][0]
    assert table == "bot_gestion_events", f"Expected 'bot_gestion_events', got {table!r}"


@pytest.mark.asyncio
async def test_record_gestion_event_row_has_datetime_utc():
    from features.analytics import gestion_sink

    mock_write = AsyncMock()
    with patch("features.analytics.analytics_sink._async_write", mock_write):
        await gestion_sink.record_gestion_event(event=_make_event())

    rows = mock_write.call_args[0][1]
    assert len(rows) == 1
    row = rows[0]
    assert "datetime_utc" in row
    assert isinstance(row["datetime_utc"], str)


@pytest.mark.asyncio
async def test_record_gestion_event_payload_is_json_string():
    from features.analytics import gestion_sink

    mock_write = AsyncMock()
    event = _make_event(payload={"outcome": "payment_commitment_registered"})
    with patch("features.analytics.analytics_sink._async_write", mock_write):
        await gestion_sink.record_gestion_event(event=event)

    rows = mock_write.call_args[0][1]
    row = rows[0]
    assert "payload" in row
    payload_val = row["payload"]
    assert isinstance(payload_val, str), "payload must be JSON-encoded string"
    assert json.loads(payload_val) == {"outcome": "payment_commitment_registered"}


# ---------------------------------------------------------------------------
# 4.1.c — fire-and-forget contract: neither raises when _async_write raises
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_record_gestion_does_not_raise_when_async_write_raises():
    from features.analytics import gestion_sink

    mock_write = AsyncMock(side_effect=Exception("Doris is down"))
    with patch("features.analytics.analytics_sink._async_write", mock_write):
        # Must NOT raise
        await gestion_sink.record_gestion(snapshot=_make_snapshot())


@pytest.mark.asyncio
async def test_record_gestion_event_does_not_raise_when_async_write_raises():
    from features.analytics import gestion_sink

    mock_write = AsyncMock(side_effect=Exception("network error"))
    with patch("features.analytics.analytics_sink._async_write", mock_write):
        # Must NOT raise
        await gestion_sink.record_gestion_event(event=_make_event())


# ---------------------------------------------------------------------------
# 4.1.d — datetime_utc is derived from ts/closed_at fields when present
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_record_gestion_datetime_utc_from_closed_at():
    """When closed_at is a datetime, datetime_utc should reflect it (UTC format)."""
    from features.analytics import gestion_sink

    ts = datetime(2026, 6, 10, 15, 30, 0, tzinfo=timezone.utc)
    mock_write = AsyncMock()
    snapshot = _make_snapshot(closed_at=ts)
    with patch("features.analytics.analytics_sink._async_write", mock_write):
        await gestion_sink.record_gestion(snapshot=snapshot)

    row = mock_write.call_args[0][1][0]
    assert row["datetime_utc"] == "2026-06-10 15:30:00"


@pytest.mark.asyncio
async def test_record_gestion_event_datetime_utc_from_ts():
    """When ts is a datetime, datetime_utc should reflect it (UTC format)."""
    from features.analytics import gestion_sink

    ts = datetime(2026, 6, 10, 9, 45, 0, tzinfo=timezone.utc)
    mock_write = AsyncMock()
    event = _make_event(ts=ts)
    with patch("features.analytics.analytics_sink._async_write", mock_write):
        await gestion_sink.record_gestion_event(event=event)

    row = mock_write.call_args[0][1][0]
    assert row["datetime_utc"] == "2026-06-10 09:45:00"
