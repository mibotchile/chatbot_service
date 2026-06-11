"""Integration tests for Layer-3 gestion tracking (Phase 6).

Drives _emit_gestion with REAL prestamype intents and a ResponsesSpec loaded
from the real tenants/prestamype/ directory (no mocking of the spec).

Requires a live Postgres: set GESTION_TEST_PG_DSN=postgresql://user:pass@host/db
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

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

_TENANT_ID = "prestamype"
_REPO_ROOT = Path(__file__).resolve().parent.parent


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


def _make_conv(conversation_id: str, tenant_id: str = _TENANT_ID) -> SimpleNamespace:
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


def _patched_tenant_dir(tenant_id: str):
    """Resolve real tenants/ dir from repo root (works in dev and CI)."""
    return _REPO_ROOT / "tenants" / tenant_id


# ---------------------------------------------------------------------------
# B7-1: consulta_deuda → info_provided
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_consulta_deuda_end_to_end_info_provided(pg_pool, pg_schema):
    """B7-1: consulta_deuda → outcome=info_provided, capability=consulta_deuda."""
    import api.wiring as wiring_module
    from features.analytics.gestion_catalog import Outcome

    conversation_id = f"integ-cd-{uuid.uuid4().hex[:8]}"
    conv = _make_conv(conversation_id)

    mock_store = MagicMock()
    mock_store.db_pool = pg_pool
    mock_store.db_schema = pg_schema

    with (
        patch.object(wiring_module, "store", mock_store),
        patch.object(wiring_module, "_tenant_dir", _patched_tenant_dir),
        patch("features.analytics.analytics_sink._async_write", new_callable=AsyncMock),
    ):
        await wiring_module._emit_gestion(conv, _make_result("consulta_deuda"), [])

    async with pg_pool.acquire() as conn:
        row = await conn.fetchrow(
            f'SELECT outcome, closed_at, capabilities_used FROM "{pg_schema}".gestiones'
            " WHERE conversation_id = $1",
            conversation_id,
        )
    assert row is not None
    assert row["outcome"] == Outcome.info_provided.value, (
        f"Expected info_provided, got: {row['outcome']}"
    )
    assert row["closed_at"] is not None
    caps = json.loads(row["capabilities_used"] or "[]")
    assert "consulta_deuda" in caps


# ---------------------------------------------------------------------------
# B7-2: comprobante_resultado → payment_proof_submitted
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_comprobante_resultado_end_to_end(pg_pool, pg_schema):
    """B7-2: comprobante_resultado → outcome=payment_proof_submitted, capability=comprobante."""
    import api.wiring as wiring_module
    from features.analytics.gestion_catalog import Outcome

    conversation_id = f"integ-cr-{uuid.uuid4().hex[:8]}"
    conv = _make_conv(conversation_id)

    mock_store = MagicMock()
    mock_store.db_pool = pg_pool
    mock_store.db_schema = pg_schema

    with (
        patch.object(wiring_module, "store", mock_store),
        patch.object(wiring_module, "_tenant_dir", _patched_tenant_dir),
        patch("features.analytics.analytics_sink._async_write", new_callable=AsyncMock),
    ):
        await wiring_module._emit_gestion(conv, _make_result("comprobante_resultado"), [])

    async with pg_pool.acquire() as conn:
        row = await conn.fetchrow(
            f'SELECT outcome, closed_at, capabilities_used FROM "{pg_schema}".gestiones'
            " WHERE conversation_id = $1",
            conversation_id,
        )
    assert row["outcome"] == Outcome.payment_proof_submitted.value
    assert row["closed_at"] is not None
    caps = json.loads(row["capabilities_used"] or "[]")
    assert "comprobante" in caps


# ---------------------------------------------------------------------------
# B7-3: derivar_asesor → escalated_to_agent + explicit_agent_request
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_derivar_asesor_end_to_end(pg_pool, pg_schema):
    """B7-3: derivar_asesor → outcome=escalated_to_agent, reason=explicit_agent_request."""
    import api.wiring as wiring_module
    from features.analytics.gestion_catalog import Outcome

    conversation_id = f"integ-da-{uuid.uuid4().hex[:8]}"
    conv = _make_conv(conversation_id)

    mock_store = MagicMock()
    mock_store.db_pool = pg_pool
    mock_store.db_schema = pg_schema

    with (
        patch.object(wiring_module, "store", mock_store),
        patch.object(wiring_module, "_tenant_dir", _patched_tenant_dir),
        patch("features.analytics.analytics_sink._async_write", new_callable=AsyncMock),
    ):
        await wiring_module._emit_gestion(conv, _make_result("derivar_asesor"), [])

    async with pg_pool.acquire() as conn:
        row = await conn.fetchrow(
            f'SELECT outcome, outcome_reason, closed_at FROM "{pg_schema}".gestiones'
            " WHERE conversation_id = $1",
            conversation_id,
        )
    assert row["outcome"] == Outcome.escalated_to_agent.value
    assert row["outcome_reason"] == "explicit_agent_request"
    assert row["closed_at"] is not None


# ---------------------------------------------------------------------------
# B7-4: no_entendido → not_understood
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_entendido_end_to_end(pg_pool, pg_schema):
    """B7-4: no_entendido → outcome=not_understood."""
    import api.wiring as wiring_module
    from features.analytics.gestion_catalog import Outcome

    conversation_id = f"integ-ne-{uuid.uuid4().hex[:8]}"
    conv = _make_conv(conversation_id)

    mock_store = MagicMock()
    mock_store.db_pool = pg_pool
    mock_store.db_schema = pg_schema

    with (
        patch.object(wiring_module, "store", mock_store),
        patch.object(wiring_module, "_tenant_dir", _patched_tenant_dir),
        patch("features.analytics.analytics_sink._async_write", new_callable=AsyncMock),
    ):
        await wiring_module._emit_gestion(conv, _make_result("no_entendido"), [])

    async with pg_pool.acquire() as conn:
        row = await conn.fetchrow(
            f'SELECT outcome, closed_at FROM "{pg_schema}".gestiones'
            " WHERE conversation_id = $1",
            conversation_id,
        )
    assert row["outcome"] == Outcome.not_understood.value
    assert row["closed_at"] is not None


# ---------------------------------------------------------------------------
# Full multi-turn: open (consulta_deuda) → terminal (comprobante_resultado)
# Journal replay, idempotency
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_full_conversation_gestion_tracking(pg_pool, pg_schema):
    """Multi-turn: non-terminal open → terminal close. Journal + idempotency."""
    import api.wiring as wiring_module
    from features.analytics.gestion_catalog import Outcome

    conversation_id = f"integ-full-{uuid.uuid4().hex[:8]}"
    conv = _make_conv(conversation_id)

    mock_store = MagicMock()
    mock_store.db_pool = pg_pool
    mock_store.db_schema = pg_schema

    doris_calls: list[tuple[str, list]] = []

    async def _fake_async_write(table: str, rows: list) -> None:
        doris_calls.append((table, rows))

    with (
        patch.object(wiring_module, "store", mock_store),
        patch.object(wiring_module, "_tenant_dir", _patched_tenant_dir),
        patch("features.analytics.analytics_sink._async_write", side_effect=_fake_async_write),
    ):
        # Turn 1: non-terminal (saludo — unannotated)
        await wiring_module._emit_gestion(conv, _make_result("saludo"), [])

        async with pg_pool.acquire() as conn:
            row = await conn.fetchrow(
                f'SELECT closed_at, outcome, schema_version, tenant_id, channel'
                f' FROM "{pg_schema}".gestiones WHERE conversation_id = $1',
                conversation_id,
            )
        assert row is not None
        assert row["closed_at"] is None
        assert row["outcome"] is None
        assert row["schema_version"] == 1
        assert row["tenant_id"] == _TENANT_ID
        assert row["channel"] == "web"

        # Turn 2: non-terminal info (consulta_deuda — capability only, no terminal close)
        # consulta_deuda HAS terminal_signal=info_provided so it DOES close — use
        # elegir_credito instead (capability=multicredito, no terminal_signal)
        await wiring_module._emit_gestion(conv, _make_result("elegir_credito"), [])

        async with pg_pool.acquire() as conn:
            row2 = await conn.fetchrow(
                f'SELECT closed_at, capabilities_used FROM "{pg_schema}".gestiones'
                " WHERE conversation_id = $1",
                conversation_id,
            )
        assert row2["closed_at"] is None, "elegir_credito has no terminal_signal — must stay open"
        caps2 = json.loads(row2["capabilities_used"] or "[]")
        assert "multicredito" in caps2

        # Turn 3: terminal (comprobante_resultado → proof)
        doris_calls.clear()
        await wiring_module._emit_gestion(conv, _make_result("comprobante_resultado"), [])

        async with pg_pool.acquire() as conn:
            row3 = await conn.fetchrow(
                f'SELECT closed_at, outcome, outcome_reason FROM "{pg_schema}".gestiones'
                " WHERE conversation_id = $1",
                conversation_id,
            )
        assert row3["closed_at"] is not None
        assert row3["outcome"] == Outcome.payment_proof_submitted.value

        # Journal: check events
        async with pg_pool.acquire() as conn:
            events = await conn.fetch(
                f'SELECT event_type, capability FROM "{pg_schema}".gestion_events'
                " WHERE conversation_id = $1 ORDER BY event_id",
                conversation_id,
            )
        event_types = [e["event_type"] for e in events]
        assert "capability_used" in event_types
        assert "terminal" in event_types

        # Idempotency: second call must not overwrite closed_at
        first_closed = row3["closed_at"]
        await asyncio.sleep(0.05)
        await wiring_module._emit_gestion(conv, _make_result("comprobante_resultado"), [])

        async with pg_pool.acquire() as conn:
            row4 = await conn.fetchrow(
                f'SELECT closed_at, outcome FROM "{pg_schema}".gestiones'
                " WHERE conversation_id = $1",
                conversation_id,
            )
        assert abs((row4["closed_at"] - first_closed).total_seconds()) < 1
        assert row4["outcome"] == Outcome.payment_proof_submitted.value

        # Doris shape
        gestiones_rows = [rows[0] for tbl, rows in doris_calls if tbl == "bot_gestiones" and rows]
        if gestiones_rows:
            g = gestiones_rows[-1]
            assert "conversation_id" in g
            assert "datetime_utc" in g


# ---------------------------------------------------------------------------
# schema_version = 1 on every written row
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_schema_version_is_one(pg_pool, pg_schema):
    import api.wiring as wiring_module

    conversation_id = f"sv-{uuid.uuid4().hex[:8]}"
    conv = _make_conv(conversation_id)

    mock_store = MagicMock()
    mock_store.db_pool = pg_pool
    mock_store.db_schema = pg_schema

    with (
        patch.object(wiring_module, "store", mock_store),
        patch.object(wiring_module, "_tenant_dir", _patched_tenant_dir),
        patch("features.analytics.analytics_sink._async_write", new_callable=AsyncMock),
    ):
        await wiring_module._emit_gestion(conv, _make_result("elegir_credito"), [])

    async with pg_pool.acquire() as conn:
        row = await conn.fetchrow(
            f'SELECT schema_version FROM "{pg_schema}".gestiones WHERE conversation_id = $1',
            conversation_id,
        )
    assert row["schema_version"] == 1


# ---------------------------------------------------------------------------
# Rows joinable by conversation_id across tables
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rows_joinable_by_conversation_id(pg_pool, pg_schema):
    import api.wiring as wiring_module

    conversation_id = f"join-{uuid.uuid4().hex[:8]}"
    conv = _make_conv(conversation_id)

    mock_store = MagicMock()
    mock_store.db_pool = pg_pool
    mock_store.db_schema = pg_schema

    with (
        patch.object(wiring_module, "store", mock_store),
        patch.object(wiring_module, "_tenant_dir", _patched_tenant_dir),
        patch("features.analytics.analytics_sink._async_write", new_callable=AsyncMock),
    ):
        await wiring_module._emit_gestion(conv, _make_result("elegir_credito"), [])
        await wiring_module._emit_gestion(conv, _make_result("comprobante_resultado"), [])

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

    assert len(result) >= 2
    conv_ids = {r["conversation_id"] for r in result}
    assert conv_ids == {conversation_id}
    outcomes = {r["outcome"] for r in result}
    assert "payment_proof_submitted" in outcomes


# ---------------------------------------------------------------------------
# No mibotair_results reference in new modules
# ---------------------------------------------------------------------------

def test_no_mibotair_results_reference():
    new_modules = [
        "apps/agent/features/analytics/gestion_catalog.py",
        "apps/agent/features/analytics/gestion_derivation.py",
        "apps/agent/features/analytics/gestion_sink.py",
        "apps/agent/features/analytics/gestion_sweep.py",
    ]
    root = Path(__file__).resolve().parent.parent
    for rel_path in new_modules:
        source = (root / rel_path).read_text(encoding="utf-8")
        assert "mibotair_results" not in source, (
            f"{rel_path} must not reference mibotair_results"
        )
