"""[RED/GREEN] Tests for gestion_sweep — inactivity sweep worker (Phase 5).

All tests require a live Postgres DB. Set GESTION_TEST_PG_DSN to enable.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

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
    """asyncpg pool pointing at the test DB."""
    pool = await asyncpg.create_pool(_PG_DSN, min_size=1, max_size=3)
    yield pool
    await pool.close()


@pytest.fixture
def test_schema():
    """Unique schema per test run to avoid collisions."""
    return f"test_sweep_{uuid.uuid4().hex[:8]}"


@pytest.fixture
async def pg_schema(pg_pool, test_schema):
    """Create schema + gestion tables; drop on teardown."""
    from shared.persistence.persistence import ensure_tables

    await ensure_tables(pg_pool, test_schema)
    yield test_schema
    async with pg_pool.acquire() as conn:
        await conn.execute(f'DROP SCHEMA IF EXISTS "{test_schema}" CASCADE')


async def _insert_gestiones_row(
    pool,
    schema: str,
    conversation_id: str,
    tenant_id: str = "tenant_a",
    closed_at=None,
    updated_at=None,
):
    """Helper: insert a gestiones row with controlled updated_at."""
    now = datetime.now(timezone.utc)
    upd = updated_at or now
    async with pool.acquire() as conn:
        await conn.execute(
            f"""
            INSERT INTO "{schema}".gestiones
                (conversation_id, tenant_id, schema_version, created_at, updated_at, closed_at)
            VALUES ($1, $2, 1, $3, $4, $5)
            ON CONFLICT (conversation_id) DO UPDATE
                SET tenant_id = EXCLUDED.tenant_id,
                    updated_at = EXCLUDED.updated_at,
                    closed_at = EXCLUDED.closed_at
            """,
            conversation_id,
            tenant_id,
            now,
            upd,
            closed_at,
        )


# ---------------------------------------------------------------------------
# Test 5.1-A: stale open conversation is closed as unresolved
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sweep_closes_stale_open_row(pg_pool, pg_schema):
    """Stale open row (updated_at > TTL ago) → closed as unresolved."""
    from features.analytics.gestion_sweep import _sweep_once

    conv_id = f"conv-stale-{uuid.uuid4().hex[:8]}"
    stale_ts = datetime.now(timezone.utc) - timedelta(minutes=40)
    await _insert_gestiones_row(
        pg_pool, pg_schema, conv_id, tenant_id="tenant_a", updated_at=stale_ts
    )

    # Patch Doris sinks to avoid real network calls
    with (
        patch(
            "features.analytics.gestion_sweep.record_gestion",
            new_callable=AsyncMock,
        ) as mock_rec_g,
        patch(
            "features.analytics.gestion_sweep.record_gestion_event",
            new_callable=AsyncMock,
        ) as mock_rec_e,
    ):
        tenant_ttl_map = {"tenant_a": 30}
        await _sweep_once(pg_pool, pg_schema, tenant_ttl_map)

    # Verify row is closed
    async with pg_pool.acquire() as conn:
        row = await conn.fetchrow(
            f'SELECT outcome, closed_at FROM "{pg_schema}".gestiones WHERE conversation_id = $1',
            conv_id,
        )

    assert row is not None
    assert row["outcome"] == "unresolved"
    assert row["closed_at"] is not None

    # Terminal journal event must have been appended
    async with pg_pool.acquire() as conn:
        events = await conn.fetch(
            f'SELECT event_type, payload FROM "{pg_schema}".gestion_events WHERE conversation_id = $1',
            conv_id,
        )
    assert len(events) == 1
    assert events[0]["event_type"] == "terminal"
    payload = json.loads(events[0]["payload"])
    assert payload.get("outcome") == "unresolved"

    # Doris fns called once each
    mock_rec_g.assert_awaited_once()
    mock_rec_e.assert_awaited_once()


# ---------------------------------------------------------------------------
# Test 5.1-B: sweep skips already-closed row
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sweep_skips_already_closed_row(pg_pool, pg_schema):
    """Sweep must NOT touch rows where closed_at is already set."""
    from features.analytics.gestion_sweep import _sweep_once

    conv_id = f"conv-closed-{uuid.uuid4().hex[:8]}"
    stale_ts = datetime.now(timezone.utc) - timedelta(minutes=60)
    closed_ts = datetime.now(timezone.utc) - timedelta(minutes=50)
    await _insert_gestiones_row(
        pg_pool,
        pg_schema,
        conv_id,
        tenant_id="tenant_a",
        closed_at=closed_ts,
        updated_at=stale_ts,
    )

    # Write a sentinel outcome to verify it is not overwritten
    async with pg_pool.acquire() as conn:
        await conn.execute(
            f'UPDATE "{pg_schema}".gestiones SET outcome = $1 WHERE conversation_id = $2',
            "payment_commitment_registered",
            conv_id,
        )

    with (
        patch(
            "features.analytics.gestion_sweep.record_gestion", new_callable=AsyncMock
        ),
        patch(
            "features.analytics.gestion_sweep.record_gestion_event",
            new_callable=AsyncMock,
        ),
    ):
        await _sweep_once(pg_pool, pg_schema, {"tenant_a": 30})

    async with pg_pool.acquire() as conn:
        row = await conn.fetchrow(
            f'SELECT outcome, closed_at FROM "{pg_schema}".gestiones WHERE conversation_id = $1',
            conv_id,
        )

    # Outcome and closed_at must be unchanged
    assert row["outcome"] == "payment_commitment_registered"
    assert row["closed_at"] is not None
    # closed_at was set by us, not by sweep (sweep would have set it to NOW())
    assert abs((row["closed_at"] - closed_ts).total_seconds()) < 2


# ---------------------------------------------------------------------------
# Test 5.1-C: sweep skips recently active row (within TTL)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sweep_skips_recent_row(pg_pool, pg_schema):
    """Row updated 10 min ago with TTL=30 must NOT be closed by sweep."""
    from features.analytics.gestion_sweep import _sweep_once

    conv_id = f"conv-recent-{uuid.uuid4().hex[:8]}"
    recent_ts = datetime.now(timezone.utc) - timedelta(minutes=10)
    await _insert_gestiones_row(
        pg_pool, pg_schema, conv_id, tenant_id="tenant_a", updated_at=recent_ts
    )

    with (
        patch(
            "features.analytics.gestion_sweep.record_gestion", new_callable=AsyncMock
        ),
        patch(
            "features.analytics.gestion_sweep.record_gestion_event",
            new_callable=AsyncMock,
        ),
    ):
        await _sweep_once(pg_pool, pg_schema, {"tenant_a": 30})

    async with pg_pool.acquire() as conn:
        row = await conn.fetchrow(
            f'SELECT outcome, closed_at FROM "{pg_schema}".gestiones WHERE conversation_id = $1',
            conv_id,
        )

    assert row["closed_at"] is None
    assert row["outcome"] is None


# ---------------------------------------------------------------------------
# Test 5.1-D: per-tenant TTL — only tenant with expired TTL is closed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sweep_per_tenant_ttl(pg_pool, pg_schema):
    """Two tenants with different TTLs; only the stale-enough one is closed."""
    from features.analytics.gestion_sweep import _sweep_once

    # tenant_a: TTL=30 min, row is 40 min old → should close
    conv_a = f"conv-ta-{uuid.uuid4().hex[:8]}"
    stale_a = datetime.now(timezone.utc) - timedelta(minutes=40)
    await _insert_gestiones_row(
        pg_pool, pg_schema, conv_a, tenant_id="tenant_a", updated_at=stale_a
    )

    # tenant_b: TTL=60 min, row is 40 min old → should NOT close
    conv_b = f"conv-tb-{uuid.uuid4().hex[:8]}"
    stale_b = datetime.now(timezone.utc) - timedelta(minutes=40)
    await _insert_gestiones_row(
        pg_pool, pg_schema, conv_b, tenant_id="tenant_b", updated_at=stale_b
    )

    with (
        patch(
            "features.analytics.gestion_sweep.record_gestion", new_callable=AsyncMock
        ),
        patch(
            "features.analytics.gestion_sweep.record_gestion_event",
            new_callable=AsyncMock,
        ),
    ):
        tenant_ttl_map = {"tenant_a": 30, "tenant_b": 60}
        await _sweep_once(pg_pool, pg_schema, tenant_ttl_map)

    async with pg_pool.acquire() as conn:
        row_a = await conn.fetchrow(
            f'SELECT outcome, closed_at FROM "{pg_schema}".gestiones WHERE conversation_id = $1',
            conv_a,
        )
        row_b = await conn.fetchrow(
            f'SELECT outcome, closed_at FROM "{pg_schema}".gestiones WHERE conversation_id = $1',
            conv_b,
        )

    # tenant_a (TTL=30, age=40) → closed
    assert row_a["outcome"] == "unresolved"
    assert row_a["closed_at"] is not None

    # tenant_b (TTL=60, age=40) → still open
    assert row_b["closed_at"] is None
    assert row_b["outcome"] is None


# ---------------------------------------------------------------------------
# Test 5.1-E: race safety — terminal close wins (sweep skips closed_at IS NOT NULL)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sweep_race_safety_terminal_wins(pg_pool, pg_schema):
    """If terminal hook already closed the row, sweep must not overwrite."""
    from features.analytics.gestion_sweep import _sweep_once

    conv_id = f"conv-race-{uuid.uuid4().hex[:8]}"
    stale_ts = datetime.now(timezone.utc) - timedelta(minutes=45)
    terminal_close_ts = datetime.now(timezone.utc) - timedelta(seconds=5)

    # Row has closed_at set (terminal hook already ran)
    await _insert_gestiones_row(
        pg_pool,
        pg_schema,
        conv_id,
        tenant_id="tenant_a",
        updated_at=stale_ts,
        closed_at=terminal_close_ts,
    )
    async with pg_pool.acquire() as conn:
        await conn.execute(
            f'UPDATE "{pg_schema}".gestiones SET outcome = $1 WHERE conversation_id = $2',
            "payment_proof_submitted",
            conv_id,
        )

    with (
        patch(
            "features.analytics.gestion_sweep.record_gestion", new_callable=AsyncMock
        ),
        patch(
            "features.analytics.gestion_sweep.record_gestion_event",
            new_callable=AsyncMock,
        ),
    ):
        await _sweep_once(pg_pool, pg_schema, {"tenant_a": 30})

    async with pg_pool.acquire() as conn:
        row = await conn.fetchrow(
            f'SELECT outcome, closed_at FROM "{pg_schema}".gestiones WHERE conversation_id = $1',
            conv_id,
        )

    # Terminal hook's outcome must be preserved
    assert row["outcome"] == "payment_proof_submitted"
    assert abs((row["closed_at"] - terminal_close_ts).total_seconds()) < 2
