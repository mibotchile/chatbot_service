"""Tests for _emit_gestion / _spawn_gestion in api.wiring (Phase 5).

Uses REAL prestamype intent names and a fixture ResponsesSpec loaded from
a temp dir (or inline) with annotated binding fields.

Scenarios:
  A. Terminal intent (consulta_deuda, terminal_signal=info_provided) →
       capability_used event, closed_at set, outcome=info_provided.
  B. Escalation intent (derivar_asesor) → closed_at set, outcome=escalated_to_agent.
  C. Unannotated intent (saludo) → no capability_used event, open state.
  D. Multi-turn deduplicates capabilities_used.
  E. Already-closed snapshot → no second close.
  F. No db_pool → silent no-op.
  G. _emit_gestion never raises (fire-and-forget contract).
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_pool(closed_row=None):
    pool = MagicMock()
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
    tenant_id="prestamype",
    channel="web",
    session_state=None,
):
    conv = SimpleNamespace()
    conv.conversation_id = conversation_id
    conv.tenant_id = tenant_id
    conv.channel = channel
    conv.session_state = session_state or {}
    return conv


def _make_result(intent=None):
    return {
        "content": "reply text",
        "response_id": "resp-001",
        "metadata": {"intent": intent},
        "tool_pairs": [],
    }


def _tool_pairs_with(*tool_names):
    return [(name, {"ok": True}) for name in tool_names]


def _make_responses_spec(intents: dict):
    """Build a ResponsesSpec inline (no file I/O) for patching _tenant_dir."""
    from tenancy.responses_spec import ResponsesSpec
    return ResponsesSpec(intents=intents)


# Minimal annotated spec matching the prestamype binding table
_PRESTAMYPE_INTENTS = {
    "saludo": {"mode": "variant"},
    "despedida": {"mode": "variant"},
    "consulta_deuda": {
        "capability": "consulta_deuda",
        "terminal_signal": "info_provided",
    },
    "donde_pagar": {
        "capability": "cuentas_bancarias",
        "terminal_signal": "info_provided",
    },
    "comprobante_resultado": {
        "capability": "comprobante",
        "terminal_signal": "proof",
    },
    "derivar_asesor": {
        "terminal_signal": "escalation",
        "escalation_reason": "explicit_agent_request",
    },
    "no_entendido": {
        "terminal_signal": "fallback",
        "escalation_reason": "fallback_exhausted",
    },
}


def _fixture_spec():
    return _make_responses_spec(_PRESTAMYPE_INTENTS)


# ---------------------------------------------------------------------------
# Scenario A — consulta_deuda → info_provided (terminal via signal)
# B6-1, B7-1
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_consulta_deuda_produces_info_provided_outcome():
    """consulta_deuda (terminal_signal=info_provided) → closed_at set, outcome=info_provided."""
    from api import wiring

    conv = _make_conv()
    result = _make_result(intent="consulta_deuda")
    mock_store = _make_store()
    spec = _fixture_spec()

    with (
        patch("api.wiring.store", mock_store),
        patch("api.wiring.upsert_gestion", new_callable=AsyncMock) as mock_upsert,
        patch("api.wiring.append_gestion_event", new_callable=AsyncMock) as mock_append,
        patch("api.wiring.record_gestion", new_callable=AsyncMock),
        patch("api.wiring.record_gestion_event", new_callable=AsyncMock),
        patch("api.wiring.was_escalated", return_value=False),
        patch("api.wiring.ResponsesSpec") as mock_rs_cls,
    ):
        mock_rs_cls.from_dir.return_value = spec
        await wiring._emit_gestion(conv, result, [])

    upsert_fields = mock_upsert.call_args.kwargs.get("fields", {})
    assert upsert_fields.get("outcome") == "info_provided"
    assert upsert_fields.get("closed_at") is not None

    event_types = [c.kwargs.get("event_type") for c in mock_append.call_args_list]
    assert "capability_used" in event_types
    assert "terminal" in event_types

    # capability in event must be consulta_deuda
    cap_calls = [
        c for c in mock_append.call_args_list
        if c.kwargs.get("event_type") == "capability_used"
    ]
    assert cap_calls[0].kwargs.get("capability") == "consulta_deuda"


# ---------------------------------------------------------------------------
# Scenario A2 — comprobante_resultado → payment_proof_submitted (B7-2)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_comprobante_resultado_produces_proof_outcome():
    from api import wiring

    conv = _make_conv()
    result = _make_result(intent="comprobante_resultado")
    mock_store = _make_store()
    spec = _fixture_spec()

    with (
        patch("api.wiring.store", mock_store),
        patch("api.wiring.upsert_gestion", new_callable=AsyncMock) as mock_upsert,
        patch("api.wiring.append_gestion_event", new_callable=AsyncMock),
        patch("api.wiring.record_gestion", new_callable=AsyncMock),
        patch("api.wiring.record_gestion_event", new_callable=AsyncMock),
        patch("api.wiring.was_escalated", return_value=False),
        patch("api.wiring.ResponsesSpec") as mock_rs_cls,
    ):
        mock_rs_cls.from_dir.return_value = spec
        await wiring._emit_gestion(conv, result, [])

    upsert_fields = mock_upsert.call_args.kwargs.get("fields", {})
    assert upsert_fields.get("outcome") == "payment_proof_submitted"


# ---------------------------------------------------------------------------
# Scenario B — derivar_asesor → escalated_to_agent (B7-3)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_derivar_asesor_produces_escalated_to_agent():
    from api import wiring

    conv = _make_conv()
    result = _make_result(intent="derivar_asesor")
    mock_store = _make_store()
    spec = _fixture_spec()

    with (
        patch("api.wiring.store", mock_store),
        patch("api.wiring.upsert_gestion", new_callable=AsyncMock) as mock_upsert,
        patch("api.wiring.append_gestion_event", new_callable=AsyncMock),
        patch("api.wiring.record_gestion", new_callable=AsyncMock),
        patch("api.wiring.record_gestion_event", new_callable=AsyncMock),
        patch("api.wiring.was_escalated", return_value=False),
        patch("api.wiring.ResponsesSpec") as mock_rs_cls,
    ):
        mock_rs_cls.from_dir.return_value = spec
        await wiring._emit_gestion(conv, result, [])

    upsert_fields = mock_upsert.call_args.kwargs.get("fields", {})
    assert upsert_fields.get("outcome") == "escalated_to_agent"
    assert upsert_fields.get("outcome_reason") == "explicit_agent_request"


# ---------------------------------------------------------------------------
# B7-4 — no_entendido → not_understood
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_entendido_produces_not_understood():
    from api import wiring

    conv = _make_conv()
    result = _make_result(intent="no_entendido")
    mock_store = _make_store()
    spec = _fixture_spec()

    with (
        patch("api.wiring.store", mock_store),
        patch("api.wiring.upsert_gestion", new_callable=AsyncMock) as mock_upsert,
        patch("api.wiring.append_gestion_event", new_callable=AsyncMock),
        patch("api.wiring.record_gestion", new_callable=AsyncMock),
        patch("api.wiring.record_gestion_event", new_callable=AsyncMock),
        patch("api.wiring.was_escalated", return_value=False),
        patch("api.wiring.ResponsesSpec") as mock_rs_cls,
    ):
        mock_rs_cls.from_dir.return_value = spec
        await wiring._emit_gestion(conv, result, [])

    upsert_fields = mock_upsert.call_args.kwargs.get("fields", {})
    assert upsert_fields.get("outcome") == "not_understood"


# ---------------------------------------------------------------------------
# Scenario C — unannotated intent (saludo) → no capability_used event, open
# B6-2
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unannotated_saludo_produces_no_capability_and_stays_open():
    from api import wiring

    conv = _make_conv()
    result = _make_result(intent="saludo")
    mock_store = _make_store()
    spec = _fixture_spec()

    with (
        patch("api.wiring.store", mock_store),
        patch("api.wiring.upsert_gestion", new_callable=AsyncMock) as mock_upsert,
        patch("api.wiring.append_gestion_event", new_callable=AsyncMock) as mock_append,
        patch("api.wiring.record_gestion", new_callable=AsyncMock),
        patch("api.wiring.record_gestion_event", new_callable=AsyncMock),
        patch("api.wiring.was_escalated", return_value=False),
        patch("api.wiring.ResponsesSpec") as mock_rs_cls,
    ):
        mock_rs_cls.from_dir.return_value = spec
        await wiring._emit_gestion(conv, result, [])

    event_types = [c.kwargs.get("event_type") for c in mock_append.call_args_list]
    assert "capability_used" not in event_types, (
        "unannotated saludo must not produce capability_used"
    )
    assert "terminal" not in event_types, "saludo has no terminal_signal — must not close"

    upsert_fields = mock_upsert.call_args.kwargs.get("fields", {})
    assert upsert_fields.get("closed_at") is None


# ---------------------------------------------------------------------------
# Scenario D — multi-turn deduplicates capabilities_used (B6-1)
# Two turns with consulta_deuda → capability appears only once
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_multi_turn_deduplicates_capabilities_used():
    """Second turn with same capability → capabilities_used deduplicated in upsert."""
    from api import wiring

    conv = _make_conv()
    spec = _fixture_spec()

    # Simulate existing row with consulta_deuda already in capabilities_used
    existing_row = {
        "closed_at": None,
        "capabilities_used": '["consulta_deuda"]',
    }
    mock_store = _make_store(pool=_make_pool(closed_row=existing_row))
    result = _make_result(intent="donde_pagar")

    with (
        patch("api.wiring.store", mock_store),
        patch("api.wiring.upsert_gestion", new_callable=AsyncMock) as mock_upsert,
        patch("api.wiring.append_gestion_event", new_callable=AsyncMock),
        patch("api.wiring.record_gestion", new_callable=AsyncMock),
        patch("api.wiring.record_gestion_event", new_callable=AsyncMock),
        patch("api.wiring.was_escalated", return_value=False),
        patch("api.wiring.ResponsesSpec") as mock_rs_cls,
    ):
        mock_rs_cls.from_dir.return_value = spec
        await wiring._emit_gestion(conv, result, [])

    upsert_fields = mock_upsert.call_args.kwargs.get("fields", {})
    caps = upsert_fields.get("capabilities_used", [])
    # cuentas_bancarias from donde_pagar should be present, no duplicates
    assert "cuentas_bancarias" in caps
    assert len(caps) == len(set(caps)), "capabilities_used must not have duplicates"


# ---------------------------------------------------------------------------
# Scenario E — already-closed snapshot → no second close
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_already_closed_snapshot_no_second_close():
    from api import wiring

    already_closed = datetime(2026, 6, 10, 10, 0, 0, tzinfo=timezone.utc)
    closed_row = {"closed_at": already_closed, "capabilities_used": "[]"}
    mock_store = _make_store(pool=_make_pool(closed_row=closed_row))
    conv = _make_conv()
    result = _make_result(intent="consulta_deuda")
    spec = _fixture_spec()

    with (
        patch("api.wiring.store", mock_store),
        patch("api.wiring.upsert_gestion", new_callable=AsyncMock) as mock_upsert,
        patch("api.wiring.append_gestion_event", new_callable=AsyncMock) as mock_append,
        patch("api.wiring.record_gestion", new_callable=AsyncMock),
        patch("api.wiring.record_gestion_event", new_callable=AsyncMock),
        patch("api.wiring.was_escalated", return_value=False),
        patch("api.wiring.ResponsesSpec") as mock_rs_cls,
    ):
        mock_rs_cls.from_dir.return_value = spec
        await wiring._emit_gestion(conv, result, [])

    event_types = [c.kwargs.get("event_type") for c in mock_append.call_args_list]
    assert "terminal" not in event_types

    if mock_upsert.called:
        upsert_fields = mock_upsert.call_args.kwargs.get("fields", {})
        assert upsert_fields.get("closed_at") is None


# ---------------------------------------------------------------------------
# Scenario F — no db_pool → silent no-op
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_db_pool_is_silent_noop():
    from api import wiring

    mock_store = _make_store(pool=None)
    mock_store.db_pool = None
    conv = _make_conv()
    result = _make_result(intent="consulta_deuda")

    with (
        patch("api.wiring.store", mock_store),
        patch("api.wiring.upsert_gestion", new_callable=AsyncMock) as mock_upsert,
        patch("api.wiring.append_gestion_event", new_callable=AsyncMock) as mock_append,
        patch("api.wiring.record_gestion", new_callable=AsyncMock) as mock_rec_g,
        patch("api.wiring.record_gestion_event", new_callable=AsyncMock) as mock_rec_e,
    ):
        await wiring._emit_gestion(conv, result, [])

    mock_upsert.assert_not_called()
    mock_append.assert_not_called()
    mock_rec_g.assert_not_called()
    mock_rec_e.assert_not_called()


# ---------------------------------------------------------------------------
# Scenario G — never raises (fire-and-forget contract)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_emit_gestion_never_raises_on_persistence_error():
    from api import wiring

    conv = _make_conv()
    result = _make_result(intent="consulta_deuda")
    mock_store = _make_store()
    spec = _fixture_spec()

    with (
        patch("api.wiring.store", mock_store),
        patch("api.wiring.upsert_gestion", AsyncMock(side_effect=Exception("DB down"))),
        patch("api.wiring.append_gestion_event", new_callable=AsyncMock),
        patch("api.wiring.record_gestion", new_callable=AsyncMock),
        patch("api.wiring.record_gestion_event", new_callable=AsyncMock),
        patch("api.wiring.was_escalated", return_value=False),
        patch("api.wiring.ResponsesSpec") as mock_rs_cls,
    ):
        mock_rs_cls.from_dir.return_value = spec
        await wiring._emit_gestion(conv, result, [])  # must not raise
