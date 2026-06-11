"""[RED/GREEN] Tests for gestion persistence — DDL (3.1) and write functions (3.3).

Requires a live Postgres instance. All tests in this module are skipped when
no Postgres is reachable (env var GESTION_TEST_PG_DSN not set or connection fails).

Set GESTION_TEST_PG_DSN=postgresql://user:pass@host/dbname to run against a real DB.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid

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

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

import asyncpg  # noqa: E402 — only imported when PG is available


@pytest.fixture
async def pg_pool():
    """Module-scoped asyncpg pool pointing at the test DB."""
    pool = await asyncpg.create_pool(_PG_DSN, min_size=1, max_size=3)
    yield pool
    await pool.close()


@pytest.fixture
def test_schema():
    """Unique schema name for this test run to avoid collisions."""
    return f"gestion_test_{uuid.uuid4().hex[:8]}"


@pytest.fixture
async def ensured_tables(pg_pool, test_schema):
    """Run ensure_tables once for the module; drop schema on teardown."""
    from shared.persistence.persistence import ensure_tables

    await ensure_tables(pg_pool, test_schema)
    yield test_schema
    async with pg_pool.acquire() as conn:
        await conn.execute(f"DROP SCHEMA IF EXISTS {test_schema} CASCADE")


# ---------------------------------------------------------------------------
# Task 3.1 — table existence tests  (verify: pytest -v -k table)
# ---------------------------------------------------------------------------

async def test_gestiones_table_exists(pg_pool, ensured_tables):
    schema = ensured_tables
    row = await pg_pool.fetchrow(
        """
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = $1 AND table_name = 'gestiones'
        """,
        schema,
    )
    assert row is not None, "gestiones table not found"


async def test_gestiones_has_required_columns(pg_pool, ensured_tables):
    schema = ensured_tables
    rows = await pg_pool.fetch(
        """
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = $1 AND table_name = 'gestiones'
        """,
        schema,
    )
    cols = {r["column_name"] for r in rows}
    required = {
        "conversation_id", "tenant_id", "project_uid", "channel",
        "document", "account_id", "credit_state",
        "outcome", "outcome_reason", "capabilities_used",
        "escalated", "commitment_date", "commitment_amount",
        "selected_credit_id", "schema_version",
        "created_at", "closed_at", "updated_at",
    }
    missing = required - cols
    assert not missing, f"gestiones missing columns: {missing}"


async def test_gestiones_updated_at_column_exists(pg_pool, ensured_tables):
    schema = ensured_tables
    row = await pg_pool.fetchrow(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = $1 AND table_name = 'gestiones' AND column_name = 'updated_at'
        """,
        schema,
    )
    assert row is not None, "gestiones.updated_at column not found"


async def test_gestion_events_table_exists(pg_pool, ensured_tables):
    schema = ensured_tables
    row = await pg_pool.fetchrow(
        """
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = $1 AND table_name = 'gestion_events'
        """,
        schema,
    )
    assert row is not None, "gestion_events table not found"


async def test_gestion_events_has_required_columns(pg_pool, ensured_tables):
    schema = ensured_tables
    rows = await pg_pool.fetch(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = $1 AND table_name = 'gestion_events'
        """,
        schema,
    )
    cols = {r["column_name"] for r in rows}
    required = {
        "event_id", "conversation_id", "ts",
        "event_type", "intent", "capability", "payload",
    }
    missing = required - cols
    assert not missing, f"gestion_events missing columns: {missing}"


async def test_gestiones_indexes_exist(pg_pool, ensured_tables):
    schema = ensured_tables
    rows = await pg_pool.fetch(
        """
        SELECT indexname FROM pg_indexes
        WHERE schemaname = $1 AND tablename = 'gestiones'
        """,
        schema,
    )
    index_names = {r["indexname"] for r in rows}
    assert "idx_gestiones_open" in index_names, f"idx_gestiones_open not found in {index_names}"
    assert "idx_gestiones_tenant" in index_names, f"idx_gestiones_tenant not found in {index_names}"


async def test_gestion_events_index_exists(pg_pool, ensured_tables):
    schema = ensured_tables
    rows = await pg_pool.fetch(
        """
        SELECT indexname FROM pg_indexes
        WHERE schemaname = $1 AND tablename = 'gestion_events'
        """,
        schema,
    )
    index_names = {r["indexname"] for r in rows}
    assert "idx_gestion_events_conv" in index_names, f"idx_gestion_events_conv not found in {index_names}"


# ---------------------------------------------------------------------------
# Task 3.3 — write function tests  (verify: pytest -v — full module)
# ---------------------------------------------------------------------------

@pytest.fixture
async def conv_id():
    """Fresh conversation_id for each test."""
    return f"test-conv-{uuid.uuid4().hex[:12]}"


async def test_append_gestion_event_returns_event_id_and_ts(pg_pool, ensured_tables, conv_id):
    from shared.persistence.persistence import append_gestion_event

    result = await append_gestion_event(
        pg_pool,
        ensured_tables,
        conv_id,
        event_type="capability_used",
        capability="consulta_deuda",
    )
    assert "event_id" in result, f"event_id missing from result: {result}"
    assert "ts" in result, f"ts missing from result: {result}"
    assert isinstance(result["event_id"], int)


async def test_append_gestion_event_with_payload(pg_pool, ensured_tables, conv_id):
    from shared.persistence.persistence import append_gestion_event

    payload = {"outcome": "payment_commitment_registered", "closed_at": "2026-06-10T00:00:00Z"}
    result = await append_gestion_event(
        pg_pool,
        ensured_tables,
        conv_id,
        event_type="terminal",
        intent="payment_commitment",
        payload=payload,
    )
    assert result["event_id"] is not None


async def test_upsert_gestion_insert_on_first_call(pg_pool, ensured_tables, conv_id):
    from shared.persistence.persistence import upsert_gestion

    await upsert_gestion(
        pg_pool,
        ensured_tables,
        conv_id,
        fields={"tenant_id": "tenant_a", "schema_version": 1},
    )
    row = await pg_pool.fetchrow(
        f"SELECT * FROM {ensured_tables}.gestiones WHERE conversation_id = $1",
        conv_id,
    )
    assert row is not None
    assert row["closed_at"] is None
    assert row["outcome"] is None
    caps = json.loads(row["capabilities_used"]) if isinstance(row["capabilities_used"], str) else row["capabilities_used"]
    assert caps == []


async def test_upsert_gestion_capabilities_accumulate_no_duplicate(pg_pool, ensured_tables, conv_id):
    from shared.persistence.persistence import upsert_gestion

    # First upsert — add consulta_deuda
    await upsert_gestion(
        pg_pool,
        ensured_tables,
        conv_id,
        fields={"capabilities_used": ["consulta_deuda"]},
    )
    # Second upsert — add comprobante (new) and consulta_deuda (duplicate)
    await upsert_gestion(
        pg_pool,
        ensured_tables,
        conv_id,
        fields={"capabilities_used": ["consulta_deuda", "comprobante"]},
    )
    row = await pg_pool.fetchrow(
        f"SELECT capabilities_used FROM {ensured_tables}.gestiones WHERE conversation_id = $1",
        conv_id,
    )
    caps = row["capabilities_used"]
    if isinstance(caps, str):
        caps = json.loads(caps)
    assert sorted(caps) == ["comprobante", "consulta_deuda"], f"Unexpected caps: {caps}"


async def test_upsert_gestion_closing_sets_outcome_and_closed_at(pg_pool, ensured_tables, conv_id):
    from datetime import datetime, timezone

    from shared.persistence.persistence import upsert_gestion

    # Open the row first
    await upsert_gestion(
        pg_pool,
        ensured_tables,
        conv_id,
        fields={"tenant_id": "tenant_a"},
    )
    # Close it
    closed_at = datetime.now(timezone.utc)
    await upsert_gestion(
        pg_pool,
        ensured_tables,
        conv_id,
        fields={
            "outcome": "payment_commitment_registered",
            "outcome_reason": None,
            "closed_at": closed_at,
        },
    )
    row = await pg_pool.fetchrow(
        f"SELECT outcome, outcome_reason, closed_at FROM {ensured_tables}.gestiones WHERE conversation_id = $1",
        conv_id,
    )
    assert row["outcome"] == "payment_commitment_registered"
    assert row["closed_at"] is not None


async def test_upsert_gestion_second_close_does_not_overwrite_first_closed_at(
    pg_pool, ensured_tables, conv_id
):
    from datetime import datetime, timedelta, timezone

    from shared.persistence.persistence import upsert_gestion

    first_closed = datetime.now(timezone.utc)
    second_closed = first_closed + timedelta(minutes=5)

    # First close
    await upsert_gestion(
        pg_pool,
        ensured_tables,
        conv_id,
        fields={"outcome": "unresolved", "closed_at": first_closed},
    )
    # Second close attempt — must NOT overwrite first closed_at
    await upsert_gestion(
        pg_pool,
        ensured_tables,
        conv_id,
        fields={"outcome": "info_provided", "closed_at": second_closed},
    )
    row = await pg_pool.fetchrow(
        f"SELECT closed_at, outcome FROM {ensured_tables}.gestiones WHERE conversation_id = $1",
        conv_id,
    )
    # closed_at must still be the FIRST value (COALESCE keeps existing non-null)
    delta = abs((row["closed_at"] - first_closed).total_seconds())
    assert delta < 1, f"closed_at was overwritten: {row['closed_at']} != {first_closed}"
