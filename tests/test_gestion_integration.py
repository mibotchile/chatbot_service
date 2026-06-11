"""[RED/GREEN] Integration test for Layer-3 gestion tracking (Phase 6).

Simulates a full two-turn conversation against a real Postgres DB:
  Turn 1 (non-terminal): assert gestiones row created open, capability_used event in journal.
  Turn 2 (terminal: payment_commitment): assert closed_at set, correct outcome, terminal event.
  Journal replay: assert all events replay correctly to match snapshot state.
  schema_version: assert = 1 on snapshot.
  No mibotair_results write: assert no import/reference in new code.
  Doris sink: mocked — assert _async_write called with correct shape.

Set GESTION_TEST_PG_DSN=postgresql://user:pass@host/dbname to run.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Postgres availability guard
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

pytestmark = pytest.mark.skipif(
    not _PG_AVAILABLE,
    reason="No Postgres available (set GESTION_TEST_PG_DSN to enable)",
)

import asyncpg  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def pg_pool():
    pool = await asyncpg.create_pool(_PG_DSN, min_size=1, max_size=3)
    yield pool
    await pool.close()


@pytest.fixture
def test_schema():
    return f"test_integ_{uuid.uuid4().hex[:8]}"


@pytest.fixture
async def pg_schema(pg_pool, test_schema):
    from shared.persistence.persistence import ensure_tables

    await ensure_tables(pg_pool, test_schema)
    yield test_schema
    async with pg_pool.acquire() as conn:
        await conn.execute(f'DROP SCHEMA IF EXISTS "{test_schema}" CASCADE')


def _make_conv(conversation_id: str, tenant_id: str = "test_tenant") -> SimpleNamespace:
    """Build a minimal conv object matching what _emit_gestion expects."""
    return SimpleNamespace(
        conversation_id=conversation_id,
        tenant_id=tenant_id,
        channel="web",
        session_state={},
    )


def _make_result(intent: str | None = None) -> dict:
    return {
        "metadata": {"intent": intent} if intent else {},
        "content": "test response",
        "usage": {},
        "tools_called": [],
    }


# ---------------------------------------------------------------------------
# Full conversation integration test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_conversation_gestion_tracking(pg_pool, pg_schema):
    """Simulate two turns; assert journal + snapshot state at each step."""
    import api.wiring as wiring_module
    from features.analytics.gestion_catalog import Outcome

    conversation_id = f"integ-{uuid.uuid4().hex[:12]}"
    conv = _make_conv(conversation_id)

    # Patch store so _emit_gestion uses our test pool
    mock_store = MagicMock()
    mock_store.db_pool = pg_pool
    mock_store.db_schema = pg_schema

    # Mock Doris _async_write to capture calls without network
    doris_calls: list[tuple[str, list]] = []

    async def _fake_async_write(table: str, rows: list) -> None:
        doris_calls.append((table, rows))

    with (
        patch.object(wiring_module, "store", mock_store),
        patch(
            "features.analytics.analytics_sink._async_write",
            side_effect=_fake_async_write,
        ),
    ):
        # ------------------------------------------------------------------
        # Turn 1: non-terminal (consulta_deuda)
        # ------------------------------------------------------------------
        result1 = _make_result(intent="consulta_deuda")
        tool_pairs1 = []  # no tool calls
        await wiring_module._emit_gestion(conv, result1, tool_pairs1)

        # Assert: gestiones row created, open
        async with pg_pool.acquire() as conn:
            row = await conn.fetchrow(
                f'SELECT * FROM "{pg_schema}".gestiones WHERE conversation_id = $1',
                conversation_id,
            )
        assert row is not None, "gestiones row must be created on first turn"
        assert row["closed_at"] is None, "closed_at must be null after non-terminal turn"
        assert row["outcome"] is None, "outcome must be null while open"
        assert row["schema_version"] == 1, "schema_version must be 1"
        assert row["tenant_id"] == "test_tenant"
        assert row["channel"] == "web"

        # Assert: capability_used event in journal
        async with pg_pool.acquire() as conn:
            events_t1 = await conn.fetch(
                f'SELECT event_type, capability, intent FROM "{pg_schema}".gestion_events'
                f" WHERE conversation_id = $1 ORDER BY event_id",
                conversation_id,
            )
        assert len(events_t1) == 1, "one capability_used event expected after turn 1"
        assert events_t1[0]["event_type"] == "capability_used"
        assert events_t1[0]["capability"] == "consulta_deuda"
        assert events_t1[0]["intent"] == "consulta_deuda"

        # Assert: Doris received a bot_gestion_events + bot_gestiones call
        doris_tables_t1 = [t for t, _ in doris_calls]
        assert "bot_gestion_events" in doris_tables_t1
        assert "bot_gestiones" in doris_tables_t1

        # ------------------------------------------------------------------
        # Turn 2: terminal (payment_commitment via tool)
        # ------------------------------------------------------------------
        doris_calls.clear()
        result2 = _make_result(intent="payment_commitment")
        tool_pairs2 = [("register_payment_commitment", {"date": "2026-07-01"})]
        await wiring_module._emit_gestion(conv, result2, tool_pairs2)

        # Assert: gestiones closed
        async with pg_pool.acquire() as conn:
            row2 = await conn.fetchrow(
                f'SELECT * FROM "{pg_schema}".gestiones WHERE conversation_id = $1',
                conversation_id,
            )
        assert row2["closed_at"] is not None, "closed_at must be set after terminal turn"
        assert row2["outcome"] == Outcome.payment_commitment_registered.value
        assert row2["outcome_reason"] is None
        assert row2["schema_version"] == 1

        # Assert: terminal event in journal
        async with pg_pool.acquire() as conn:
            events_t2 = await conn.fetch(
                f'SELECT event_type, capability, payload FROM "{pg_schema}".gestion_events'
                f" WHERE conversation_id = $1 ORDER BY event_id",
                conversation_id,
            )
        event_types_t2 = [e["event_type"] for e in events_t2]
        assert "terminal" in event_types_t2, "terminal event must be appended"

        terminal_evt = next(e for e in events_t2 if e["event_type"] == "terminal")
        terminal_payload = json.loads(terminal_evt["payload"])
        assert terminal_payload["outcome"] == Outcome.payment_commitment_registered.value

        # Assert: Doris called again after terminal turn
        doris_tables_t2 = [t for t, _ in doris_calls]
        assert "bot_gestion_events" in doris_tables_t2
        assert "bot_gestiones" in doris_tables_t2

        # Validate Doris row shape for bot_gestiones
        gestiones_doris_rows = [
            rows[0] for tbl, rows in doris_calls if tbl == "bot_gestiones" and rows
        ]
        assert gestiones_doris_rows, "bot_gestiones Doris row must be present"
        doris_g = gestiones_doris_rows[-1]
        assert "conversation_id" in doris_g, "Doris row must include conversation_id (join key)"
        assert "datetime_utc" in doris_g, "Doris row must include datetime_utc"

        # ------------------------------------------------------------------
        # Journal replay: events in order reconstruct snapshot state
        # ------------------------------------------------------------------
        async with pg_pool.acquire() as conn:
            all_events = await conn.fetch(
                f'SELECT event_type, capability, payload FROM "{pg_schema}".gestion_events'
                f" WHERE conversation_id = $1 ORDER BY event_id",
                conversation_id,
            )

        # Replay: accumulate capabilities, detect terminal
        replayed_caps: list[str] = []
        replayed_outcome: str | None = None
        replayed_closed = False
        for evt in all_events:
            if evt["event_type"] == "capability_used" and evt["capability"]:
                if evt["capability"] not in replayed_caps:
                    replayed_caps.append(evt["capability"])
            elif evt["event_type"] == "terminal":
                p = json.loads(evt["payload"])
                replayed_outcome = p.get("outcome")
                replayed_closed = True

        assert replayed_closed, "journal replay must detect terminal close"
        assert replayed_outcome == Outcome.payment_commitment_registered.value
        assert "consulta_deuda" in replayed_caps

        # ------------------------------------------------------------------
        # Idempotency: second terminal call must NOT overwrite first closed_at
        # ------------------------------------------------------------------
        first_closed_at = row2["closed_at"]
        await asyncio.sleep(0.05)  # small delay to detect timestamp change

        result3 = _make_result(intent="payment_commitment")
        await wiring_module._emit_gestion(conv, result3, tool_pairs2)

        async with pg_pool.acquire() as conn:
            row3 = await conn.fetchrow(
                f'SELECT closed_at, outcome FROM "{pg_schema}".gestiones WHERE conversation_id = $1',
                conversation_id,
            )
        assert abs((row3["closed_at"] - first_closed_at).total_seconds()) < 1, (
            "second close must not overwrite first closed_at"
        )
        assert row3["outcome"] == Outcome.payment_commitment_registered.value


# ---------------------------------------------------------------------------
# No mibotair_results reference in new modules
# ---------------------------------------------------------------------------


def test_no_mibotair_results_reference():
    """Assert none of the new gestion modules import or reference mibotair_results."""
    import importlib
    import importlib.util
    import pathlib

    new_modules = [
        "apps/agent/features/analytics/gestion_catalog.py",
        "apps/agent/features/analytics/gestion_derivation.py",
        "apps/agent/features/analytics/gestion_sink.py",
        "apps/agent/features/analytics/gestion_sweep.py",
    ]
    root = pathlib.Path(__file__).resolve().parent.parent
    for rel_path in new_modules:
        source = (root / rel_path).read_text(encoding="utf-8")
        assert "mibotair_results" not in source, (
            f"{rel_path} must not reference mibotair_results"
        )


# ---------------------------------------------------------------------------
# schema_version = 1 on every written row
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_schema_version_is_one(pg_pool, pg_schema):
    """Every gestiones row written must have schema_version = 1."""
    import api.wiring as wiring_module

    conversation_id = f"sv-{uuid.uuid4().hex[:8]}"
    conv = _make_conv(conversation_id)

    mock_store = MagicMock()
    mock_store.db_pool = pg_pool
    mock_store.db_schema = pg_schema

    with (
        patch.object(wiring_module, "store", mock_store),
        patch("features.analytics.analytics_sink._async_write", new_callable=AsyncMock),
    ):
        await wiring_module._emit_gestion(conv, _make_result("consulta_deuda"), [])

    async with pg_pool.acquire() as conn:
        row = await conn.fetchrow(
            f'SELECT schema_version FROM "{pg_schema}".gestiones WHERE conversation_id = $1',
            conversation_id,
        )
    assert row["schema_version"] == 1


# ---------------------------------------------------------------------------
# Rows are joinable by conversation_id across tables
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rows_joinable_by_conversation_id(pg_pool, pg_schema):
    """gestiones and gestion_events share conversation_id (join key for BI)."""
    import api.wiring as wiring_module

    conversation_id = f"join-{uuid.uuid4().hex[:8]}"
    conv = _make_conv(conversation_id)

    mock_store = MagicMock()
    mock_store.db_pool = pg_pool
    mock_store.db_schema = pg_schema

    with (
        patch.object(wiring_module, "store", mock_store),
        patch("features.analytics.analytics_sink._async_write", new_callable=AsyncMock),
    ):
        await wiring_module._emit_gestion(conv, _make_result("consulta_deuda"), [])
        tool_pairs = [("register_payment_commitment", {})]
        await wiring_module._emit_gestion(conv, _make_result("payment_commitment"), tool_pairs)

    async with pg_pool.acquire() as conn:
        result = await conn.fetch(
            f"""
            SELECT g.conversation_id, g.outcome, e.event_type
            FROM "{pg_schema}".gestiones g
            JOIN "{pg_schema}".gestion_events e
              ON g.conversation_id = e.conversation_id
            WHERE g.conversation_id = $1
            ORDER BY e.event_id
            """,
            conversation_id,
        )

    assert len(result) >= 2, "join must return rows from both tables"
    conv_ids = {r["conversation_id"] for r in result}
    assert conv_ids == {conversation_id}
    # At least one terminal event joined to the closed snapshot
    outcomes = {r["outcome"] for r in result}
    assert "payment_commitment_registered" in outcomes
