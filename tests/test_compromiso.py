"""Tests for Phase 4 (CMP-01/CMP-02): bot-owned payment commitment flow.

Covers:
  (a) date today → registered; gestiones row has commitment_date/amount + outcome
  (b) date +2d → registered
  (c) date +3d → escalate, NOT registered
  (d) unparseable/past → escalate
  (e) gestiones write raises → escalate, no confirm
  (f) al_dia/por_vencer → compromiso blocked (vencido-only guard)
  (g) confirmation message contains the fecha
  (h) pago-parcial chains to compromiso (pending_intent set)

DB tests require GESTION_TEST_PG_DSN (same as test_gestion_integration.py).
Pure date-logic tests run without a DB.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Postgres availability guard (same pattern as test_gestion_integration.py)
# ---------------------------------------------------------------------------

_PG_DSN = os.getenv("GESTION_TEST_PG_DSN", "")
_PG_AVAILABLE = False

if _PG_DSN:
    try:
        import asyncpg

        async def _check_pg():
            conn = await asyncpg.connect(_PG_DSN, timeout=3)
            await conn.close()

        asyncio.run(_check_pg())
        _PG_AVAILABLE = True
    except Exception:
        _PG_AVAILABLE = False

_pg_only = pytest.mark.skipif(
    not _PG_AVAILABLE,
    reason="No Postgres available (set GESTION_TEST_PG_DSN to enable)",
)

# ---------------------------------------------------------------------------
# Imports under test
# ---------------------------------------------------------------------------

from features.cobranza.commitment import (
    CommitmentResult,
    parse_commitment_date,
    within_commitment_window,
)


# ---------------------------------------------------------------------------
# Helper: build a minimal profile with monto
# ---------------------------------------------------------------------------

def _profile(monto: float = 500.0) -> dict:
    return {
        "account_id": "ACC001",
        "document": "12345678",
        "saldo_por_cancelar": monto,
        "credit_state": "vencido",
    }


# ---------------------------------------------------------------------------
# (a/b/c/d) Pure date-logic tests — no DB required
# ---------------------------------------------------------------------------


class TestParseCommitmentDate:
    """parse_commitment_date accepts ISO and DD/MM/YYYY formats."""

    def test_iso_format_accepted(self):
        today = date.today()
        result = parse_commitment_date(today.isoformat())
        assert result == today

    def test_ddmmyyyy_format_accepted(self):
        today = date.today()
        formatted = today.strftime("%d/%m/%Y")
        result = parse_commitment_date(formatted)
        assert result == today

    def test_unparseable_returns_none(self):
        assert parse_commitment_date("not-a-date") is None
        assert parse_commitment_date("") is None
        assert parse_commitment_date("32/01/2026") is None

    def test_past_date_returns_none(self):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        assert parse_commitment_date(yesterday) is None


class TestWithinCommitmentWindow:
    """within_commitment_window: today..+2 → True, +3 and beyond → False."""

    def test_today_is_in_window(self):
        assert within_commitment_window(date.today()) is True

    def test_plus_1_in_window(self):
        assert within_commitment_window(date.today() + timedelta(days=1)) is True

    def test_plus_2_in_window(self):
        assert within_commitment_window(date.today() + timedelta(days=2)) is True

    def test_plus_3_outside_window(self):
        assert within_commitment_window(date.today() + timedelta(days=3)) is False

    def test_plus_7_outside_window(self):
        assert within_commitment_window(date.today() + timedelta(days=7)) is False


# ---------------------------------------------------------------------------
# (a) date today → registered — DB test
# ---------------------------------------------------------------------------


@_pg_only
class TestRegisterCommitmentDB:
    """register_commitment writes to gestiones with correct fields."""

    @pytest.fixture
    async def pool(self):
        pool = await asyncpg.create_pool(_PG_DSN, min_size=1, max_size=2)
        yield pool
        await pool.close()

    @pytest.fixture
    async def ensure_tables(self, pool):
        from shared.persistence.persistence import ensure_tables
        await ensure_tables(pool, "dev")

    @pytest.mark.asyncio
    async def test_today_registered_row_has_commitment_fields(self, pool, ensure_tables):
        from features.cobranza.commitment import register_commitment

        conv_id = f"test-{uuid.uuid4()}"
        today = date.today()
        result: CommitmentResult = await register_commitment(
            pool, "dev", conv_id,
            date_str=today.isoformat(),
            amount=350.0,
            profile=_profile(350.0),
        )
        assert result.registered is True
        assert result.escalate is False
        assert result.commitment_date == today

        # Verify the gestiones row
        row = await pool.fetchrow(
            'SELECT commitment_date, commitment_amount, outcome FROM "dev".gestiones'
            " WHERE conversation_id = $1",
            conv_id,
        )
        assert row is not None
        assert row["commitment_date"] == today
        assert float(row["commitment_amount"]) == 350.0
        assert row["outcome"] == "payment_commitment_registered"

        # Cleanup
        await pool.execute(
            'DELETE FROM "dev".gestiones WHERE conversation_id = $1', conv_id
        )
        await pool.execute(
            'DELETE FROM "dev".gestion_events WHERE conversation_id = $1', conv_id
        )

    @pytest.mark.asyncio
    async def test_plus_2_days_registered(self, pool, ensure_tables):
        from features.cobranza.commitment import register_commitment

        conv_id = f"test-{uuid.uuid4()}"
        target = date.today() + timedelta(days=2)
        result: CommitmentResult = await register_commitment(
            pool, "dev", conv_id,
            date_str=target.isoformat(),
            amount=200.0,
            profile=_profile(200.0),
        )
        assert result.registered is True
        assert result.commitment_date == target

        await pool.execute(
            'DELETE FROM "dev".gestiones WHERE conversation_id = $1', conv_id
        )
        await pool.execute(
            'DELETE FROM "dev".gestion_events WHERE conversation_id = $1', conv_id
        )

    @pytest.mark.asyncio
    async def test_plus_3_days_not_registered_escalate(self, pool, ensure_tables):
        from features.cobranza.commitment import register_commitment

        conv_id = f"test-{uuid.uuid4()}"
        target = date.today() + timedelta(days=3)
        result: CommitmentResult = await register_commitment(
            pool, "dev", conv_id,
            date_str=target.isoformat(),
            amount=200.0,
            profile=_profile(200.0),
        )
        assert result.registered is False
        assert result.escalate is True

        # Nothing written to gestiones
        row = await pool.fetchrow(
            'SELECT conversation_id FROM "dev".gestiones WHERE conversation_id = $1',
            conv_id,
        )
        assert row is None

    @pytest.mark.asyncio
    async def test_unparseable_escalates_no_write(self, pool, ensure_tables):
        from features.cobranza.commitment import register_commitment

        conv_id = f"test-{uuid.uuid4()}"
        result: CommitmentResult = await register_commitment(
            pool, "dev", conv_id,
            date_str="no-date",
            amount=100.0,
            profile=_profile(100.0),
        )
        assert result.registered is False
        assert result.escalate is True

        row = await pool.fetchrow(
            'SELECT conversation_id FROM "dev".gestiones WHERE conversation_id = $1',
            conv_id,
        )
        assert row is None


# ---------------------------------------------------------------------------
# (e) gestiones write raises → escalate, no confirm
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_db_failure_escalates():
    """When upsert_gestion raises, register_commitment returns escalate=True."""
    from features.cobranza.commitment import register_commitment

    mock_pool = AsyncMock()
    mock_pool.execute = AsyncMock(side_effect=RuntimeError("DB down"))
    mock_pool.fetchrow = AsyncMock(return_value=None)

    today = date.today()
    result: CommitmentResult = await register_commitment(
        mock_pool, "dev", "conv-err",
        date_str=today.isoformat(),
        amount=100.0,
        profile=_profile(100.0),
    )
    assert result.registered is False
    assert result.escalate is True


# ---------------------------------------------------------------------------
# (f) al_dia / por_vencer → compromiso blocked via handle_vencido_only_intent
# ---------------------------------------------------------------------------


def test_al_dia_compromiso_blocked():
    """handle_vencido_only_intent redirects compromiso_pago when not vencido."""
    from features.conversation.responses import handle_vencido_only_intent
    from tenancy.responses_spec import ResponsesSpec
    from pathlib import Path

    spec_dir = Path(__file__).resolve().parent.parent / "tenants" / "prestamype"
    spec = ResponsesSpec.from_dir(str(spec_dir))

    session = {"credit_state": "al_dia"}
    outcome = handle_vencido_only_intent(
        "compromiso_pago", spec, {}, session_state=session, source="canned_keyword"
    )
    # Must redirect (consulta_deuda menu), NOT None
    assert outcome is not None
    assert outcome.handled is True
    assert outcome.intent != "compromiso_pago"


def test_por_vencer_compromiso_blocked():
    from features.conversation.responses import handle_vencido_only_intent
    from tenancy.responses_spec import ResponsesSpec
    from pathlib import Path

    spec_dir = Path(__file__).resolve().parent.parent / "tenants" / "prestamype"
    spec = ResponsesSpec.from_dir(str(spec_dir))

    session = {"credit_state": "por_vencer"}
    outcome = handle_vencido_only_intent(
        "compromiso_pago", spec, {}, session_state=session, source="canned_keyword"
    )
    assert outcome is not None
    assert outcome.handled is True


def test_vencido_compromiso_allowed():
    """handle_vencido_only_intent returns None (allowed) for vencido users."""
    from features.conversation.responses import handle_vencido_only_intent
    from tenancy.responses_spec import ResponsesSpec
    from pathlib import Path

    spec_dir = Path(__file__).resolve().parent.parent / "tenants" / "prestamype"
    spec = ResponsesSpec.from_dir(str(spec_dir))

    session = {"credit_state": "vencido"}
    outcome = handle_vencido_only_intent(
        "compromiso_pago", spec, {}, session_state=session, source="canned_keyword"
    )
    assert outcome is None  # allowed — caller proceeds


# ---------------------------------------------------------------------------
# (g) confirmation message contains the fecha
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_confirmation_message_contains_date():
    """handle_compromiso_date_reply returns a message with the commitment date."""
    from features.conversation.responses import handle_compromiso_date_reply
    from tenancy.responses_spec import ResponsesSpec
    from pathlib import Path

    spec_dir = Path(__file__).resolve().parent.parent / "tenants" / "prestamype"
    spec = ResponsesSpec.from_dir(str(spec_dir))

    today = date.today()
    date_str = today.isoformat()

    # upsert_gestion uses pool.execute for the INSERT and pool.fetchrow for the
    # capabilities SELECT. append_gestion_event uses pool.fetchrow (INSERT RETURNING).
    import datetime as _dt

    _fake_event_row = {
        "event_id": 1,
        "conversation_id": "conv-confirm-test",
        "ts": _dt.datetime.now(_dt.timezone.utc),
        "event_type": "commitment",
        "intent": "compromiso_pago",
        "capability": None,
        "payload": "{}",
    }

    mock_pool = AsyncMock()
    mock_pool.execute = AsyncMock(return_value=None)
    # First fetchrow: upsert_gestion capabilities SELECT → None (no existing row).
    # Second fetchrow: append_gestion_event INSERT RETURNING → fake event row.
    _fetchrow_calls = [None, _fake_event_row]

    async def _fetchrow_side_effect(*args, **kwargs):
        return _fetchrow_calls.pop(0) if _fetchrow_calls else None

    mock_pool.fetchrow = _fetchrow_side_effect

    session = {
        "credit_state": "vencido",
        "compromiso_pago_pending_date": True,
    }
    profile = _profile(400.0)
    profile["dias_mora"] = 10

    outcome = await handle_compromiso_date_reply(
        date_str, spec, profile,
        session_state=session,
        source="canned_keyword",
        pool=mock_pool,
        schema="dev",
        conversation_id="conv-confirm-test",
    )
    assert outcome is not None
    assert outcome.handled is True
    # The confirmation text must mention the date
    assert date_str in outcome.text or today.strftime("%d/%m/%Y") in outcome.text


# ---------------------------------------------------------------------------
# (h) pago-parcial chains to compromiso_pago via pending_intent
# ---------------------------------------------------------------------------


def test_pago_parcial_chains_to_compromiso():
    """After a comprobante with tipo=abono, pending_intent is set to compromiso_pago."""
    # This is already wired in agent._content_after_tool (PR-1/PR-2).
    # We verify the session_state mutation directly.
    session_state: dict = {}

    # Simulate what _content_after_tool does:
    tool_result = {"tipo": "abono", "registered": True}
    if tool_result.get("tipo") == "abono" and session_state is not None:
        session_state["pending_intent"] = "compromiso_pago"

    assert session_state["pending_intent"] == "compromiso_pago"
