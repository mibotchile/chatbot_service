"""Tests for _emit_gestion / _spawn_gestion in api.wiring (Task 4.3 RED → 4.4 GREEN).

All external side-effects are mocked:
  - upsert_gestion, append_gestion_event  (persistence)
  - record_gestion, record_gestion_event  (Doris sink)
  - was_escalated                         (chathub_adapter)
  - api.wiring.store                      (module-level singleton)

Scenarios:
  A. Terminal intent (payment_commitment) → non-unresolved outcome →
       closed_at set on snapshot + terminal event appended + both Doris fns called.
  B. Non-terminal intent (consulta_deuda) → closed_at null + no terminal event
       + capability_used event appended.
  C. Already-closed snapshot (closed_at IS NOT NULL) → no second close.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _make_pool(closed_row=None):
    """Minimal mock pool that satisfies 'pool is not None' guard."""
    pool = MagicMock()
    # asyncpg pool methods are coroutines — mock them as AsyncMock
    pool.fetchrow = AsyncMock(return_value=closed_row)
    pool.execute = AsyncMock(return_value=None)
    return pool


def _make_store(pool=None, schema="public"):
    store = MagicMock()
    store.db_pool = pool if pool is not None else _make_pool()
    store.db_schema = schema
    return store


def _make_conv(
    conversation_id="conv-test-001",
    tenant_id="tenant-a",
    channel="web",
    session_state=None,
    identity_verified=False,
    debt_context=None,
):
    conv = SimpleNamespace()
    conv.conversation_id = conversation_id
    conv.tenant_id = tenant_id
    conv.channel = channel
    conv.session_state = session_state or {}
    conv.identity_verified = identity_verified
    conv.debt_context = debt_context or {}
    return conv


def _make_result(intent=None):
    """Minimal result dict as returned by agent.process_message."""
    return {
        "content": "reply text",
        "response_id": "resp-001",
        "metadata": {"intent": intent},
        "tool_pairs": [],
    }


def _tool_pairs_with(*tool_names):
    """Build a tool_pairs list as used in conversations.py."""
    return [(name, {"ok": True}) for name in tool_names]


# ---------------------------------------------------------------------------
# Scenario A — terminal intent → close path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_terminal_intent_sets_closed_at_and_calls_terminal_event():
    """payment_commitment tool in tool_pairs → outcome != unresolved → closed_at set."""
    from api import wiring

    tool_pairs = _tool_pairs_with("register_payment_commitment")
    conv = _make_conv()
    result = _make_result(intent="payment_commitment")
    mock_store = _make_store()

    with (
        patch("api.wiring.store", mock_store),
        patch("api.wiring.upsert_gestion", new_callable=AsyncMock) as mock_upsert,
        patch("api.wiring.append_gestion_event", new_callable=AsyncMock) as mock_append,
        patch("api.wiring.record_gestion", new_callable=AsyncMock) as mock_rec_g,
        patch("api.wiring.record_gestion_event", new_callable=AsyncMock) as mock_rec_e,
        patch("api.wiring.was_escalated", return_value=False),
    ):
        await wiring._emit_gestion(conv, result, tool_pairs)

    # upsert_gestion must have been called
    mock_upsert.assert_called()

    # A terminal event must be among the append_gestion_event calls
    event_types = [call.kwargs.get("event_type") for call in mock_append.call_args_list]
    assert "terminal" in event_types, f"Expected 'terminal' event, got: {event_types}"

    # Both Doris sink functions must have been called
    mock_rec_g.assert_called()
    mock_rec_e.assert_called()

    # The upsert_gestion fields must include closed_at
    upsert_fields = mock_upsert.call_args.kwargs.get("fields", {})
    assert "closed_at" in upsert_fields, "upsert_gestion fields must contain closed_at"
    assert upsert_fields["closed_at"] is not None


@pytest.mark.asyncio
async def test_terminal_intent_outcome_is_not_unresolved():
    """Terminal tool → derived outcome must not be 'unresolved'."""
    from api import wiring

    tool_pairs = _tool_pairs_with("register_payment_commitment")
    conv = _make_conv()
    result = _make_result(intent="payment_commitment")
    mock_store = _make_store()

    with (
        patch("api.wiring.store", mock_store),
        patch("api.wiring.upsert_gestion", new_callable=AsyncMock) as mock_upsert,
        patch("api.wiring.append_gestion_event", new_callable=AsyncMock),
        patch("api.wiring.record_gestion", new_callable=AsyncMock),
        patch("api.wiring.record_gestion_event", new_callable=AsyncMock),
        patch("api.wiring.was_escalated", return_value=False),
    ):
        await wiring._emit_gestion(conv, result, tool_pairs)

    upsert_fields = mock_upsert.call_args.kwargs.get("fields", {})
    outcome = upsert_fields.get("outcome")
    assert outcome is not None
    assert outcome != "unresolved", f"Expected non-unresolved outcome, got: {outcome!r}"


# ---------------------------------------------------------------------------
# Scenario B — non-terminal intent → open path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_non_terminal_intent_closed_at_is_none():
    """consulta_deuda (info intent) → no terminal action → closed_at not set."""
    from api import wiring

    tool_pairs = []
    conv = _make_conv()
    result = _make_result(intent="consulta_deuda")
    mock_store = _make_store()

    with (
        patch("api.wiring.store", mock_store),
        patch("api.wiring.upsert_gestion", new_callable=AsyncMock) as mock_upsert,
        patch("api.wiring.append_gestion_event", new_callable=AsyncMock) as mock_append,
        patch("api.wiring.record_gestion", new_callable=AsyncMock),
        patch("api.wiring.record_gestion_event", new_callable=AsyncMock),
        patch("api.wiring.was_escalated", return_value=False),
    ):
        await wiring._emit_gestion(conv, result, tool_pairs)

    # No terminal event
    event_types = [call.kwargs.get("event_type") for call in mock_append.call_args_list]
    assert "terminal" not in event_types, f"Did not expect 'terminal' event, got: {event_types}"

    # closed_at must not be set (None or absent)
    upsert_fields = mock_upsert.call_args.kwargs.get("fields", {})
    assert upsert_fields.get("closed_at") is None, (
        f"closed_at should be None for non-terminal, got: {upsert_fields.get('closed_at')}"
    )


@pytest.mark.asyncio
async def test_non_terminal_intent_capability_used_event_appended():
    """consulta_deuda → a capability_used event must be appended."""
    from api import wiring

    tool_pairs = []
    conv = _make_conv()
    result = _make_result(intent="consulta_deuda")
    mock_store = _make_store()

    with (
        patch("api.wiring.store", mock_store),
        patch("api.wiring.upsert_gestion", new_callable=AsyncMock),
        patch("api.wiring.append_gestion_event", new_callable=AsyncMock) as mock_append,
        patch("api.wiring.record_gestion", new_callable=AsyncMock),
        patch("api.wiring.record_gestion_event", new_callable=AsyncMock),
        patch("api.wiring.was_escalated", return_value=False),
    ):
        await wiring._emit_gestion(conv, result, tool_pairs)

    event_types = [call.kwargs.get("event_type") for call in mock_append.call_args_list]
    assert "capability_used" in event_types, (
        f"Expected 'capability_used' event for info intent, got: {event_types}"
    )


# ---------------------------------------------------------------------------
# Scenario C — already-closed snapshot → no second close
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_already_closed_snapshot_no_second_close():
    """If gestiones row already has closed_at set, no terminal event / no close fields."""
    from api import wiring

    tool_pairs = _tool_pairs_with("register_payment_commitment")
    conv = _make_conv()
    result = _make_result(intent="payment_commitment")

    already_closed = datetime(2026, 6, 10, 10, 0, 0, tzinfo=timezone.utc)
    closed_row = {
        "closed_at": already_closed,
        "capabilities_used": "[]",
    }
    mock_store = _make_store(pool=_make_pool(closed_row=closed_row))

    with (
        patch("api.wiring.store", mock_store),
        patch("api.wiring.upsert_gestion", new_callable=AsyncMock) as mock_upsert,
        patch("api.wiring.append_gestion_event", new_callable=AsyncMock) as mock_append,
        patch("api.wiring.record_gestion", new_callable=AsyncMock),
        patch("api.wiring.record_gestion_event", new_callable=AsyncMock),
        patch("api.wiring.was_escalated", return_value=False),
    ):
        await wiring._emit_gestion(conv, result, tool_pairs)

    # No terminal event must be appended
    event_types = [call.kwargs.get("event_type") for call in mock_append.call_args_list]
    assert "terminal" not in event_types, (
        f"Already-closed snapshot must not get second terminal event, got: {event_types}"
    )

    # closed_at in upsert_gestion fields must be None (not overwriting)
    if mock_upsert.called:
        upsert_fields = mock_upsert.call_args.kwargs.get("fields", {})
        assert upsert_fields.get("closed_at") is None, (
            "Already-closed: upsert_gestion must not set closed_at again"
        )


# ---------------------------------------------------------------------------
# Scenario D — no db_pool → graceful no-op
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_db_pool_is_silent_noop():
    """When store.db_pool is None, _emit_gestion must return without calling any sink."""
    from api import wiring

    tool_pairs = _tool_pairs_with("register_payment_commitment")
    conv = _make_conv()
    result = _make_result(intent="payment_commitment")
    mock_store = _make_store(pool=None)
    mock_store.db_pool = None  # explicit None — no pool

    with (
        patch("api.wiring.store", mock_store),
        patch("api.wiring.upsert_gestion", new_callable=AsyncMock) as mock_upsert,
        patch("api.wiring.append_gestion_event", new_callable=AsyncMock) as mock_append,
        patch("api.wiring.record_gestion", new_callable=AsyncMock) as mock_rec_g,
        patch("api.wiring.record_gestion_event", new_callable=AsyncMock) as mock_rec_e,
    ):
        # Must not raise
        await wiring._emit_gestion(conv, result, tool_pairs)

    mock_upsert.assert_not_called()
    mock_append.assert_not_called()
    mock_rec_g.assert_not_called()
    mock_rec_e.assert_not_called()


# ---------------------------------------------------------------------------
# Scenario E — _emit_gestion never raises (fire-and-forget contract)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_emit_gestion_never_raises_on_persistence_error():
    """Even if upsert_gestion raises, _emit_gestion must not propagate."""
    from api import wiring

    tool_pairs = _tool_pairs_with("register_payment_commitment")
    conv = _make_conv()
    result = _make_result(intent="payment_commitment")
    mock_store = _make_store()

    with (
        patch("api.wiring.store", mock_store),
        patch("api.wiring.upsert_gestion", AsyncMock(side_effect=Exception("DB down"))),
        patch("api.wiring.append_gestion_event", new_callable=AsyncMock),
        patch("api.wiring.record_gestion", new_callable=AsyncMock),
        patch("api.wiring.record_gestion_event", new_callable=AsyncMock),
        patch("api.wiring.was_escalated", return_value=False),
    ):
        # Must NOT raise
        await wiring._emit_gestion(conv, result, tool_pairs)
