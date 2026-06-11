"""Inactivity sweep worker for Layer-3 gestion tracking.

Closes abandoned conversations (closed_at IS NULL, updated_at older than
the per-tenant TTL) as ``unresolved``. Runs as a periodic asyncio background
task started in the app lifespan (wiring.py startup path).

Race safety: the UPDATE filters ``WHERE closed_at IS NULL``, so a row already
closed by the terminal hook is never touched (first-close-wins).

Cancel safety: the loop uses ``asyncio.sleep`` so cancellation propagates
cleanly. DB errors are caught and logged — the loop keeps running.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import asyncpg
from loguru import logger

from features.analytics.gestion_catalog import EventType, Outcome
from shared.persistence.persistence import append_gestion_event
from features.analytics.gestion_sink import record_gestion, record_gestion_event


# ---------------------------------------------------------------------------
# Core sweep logic
# ---------------------------------------------------------------------------


async def _sweep_once(
    pool: asyncpg.Pool,
    schema: str,
    tenant_ttl_map: dict[str, int],
) -> None:
    """Close all stale-open gestiones rows for each tenant TTL bucket.

    For each tenant in ``tenant_ttl_map``:
      - UPDATE gestiones SET outcome='unresolved', closed_at=NOW(), updated_at=NOW()
        WHERE closed_at IS NULL AND tenant_id = $tenant AND updated_at < NOW() - TTL
        RETURNING ...
      - For each closed row: append terminal journal event + replicate to Doris.

    Rows that already have closed_at set are never touched (WHERE closed_at IS NULL).
    """
    if not pool or not tenant_ttl_map:
        return

    for tenant_id, ttl_minutes in tenant_ttl_map.items():
        try:
            await _sweep_tenant(pool, schema, tenant_id, ttl_minutes)
        except Exception:
            logger.opt(exception=True).warning(
                "gestion_sweep: error sweeping tenant={} (ignored)", tenant_id
            )


async def _sweep_tenant(
    pool: asyncpg.Pool,
    schema: str,
    tenant_id: str,
    ttl_minutes: int,
) -> None:
    """Sweep one tenant: close stale rows and emit journal + Doris events."""
    outcome_val = Outcome.unresolved.value

    # UPDATE ... WHERE closed_at IS NULL guarantees race safety vs terminal hook.
    # Uses the idx_gestiones_open partial index on updated_at WHERE closed_at IS NULL.
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            UPDATE "{schema}".gestiones
               SET outcome     = $1,
                   closed_at   = NOW(),
                   updated_at  = NOW()
             WHERE closed_at IS NULL
               AND tenant_id  = $2
               AND updated_at < NOW() - ($3 * INTERVAL '1 minute')
            RETURNING conversation_id, tenant_id, project_uid, channel,
                      capabilities_used, schema_version, outcome, closed_at,
                      updated_at, created_at
            """,
            outcome_val,
            tenant_id,
            ttl_minutes,
        )

    if not rows:
        return

    logger.info(
        "gestion_sweep: closed {} stale rows for tenant={} ttl={}min",
        len(rows),
        tenant_id,
        ttl_minutes,
    )

    for row in rows:
        conv_id = row["conversation_id"]
        try:
            # Append terminal journal event
            event_row = await append_gestion_event(
                pool,
                schema,
                conv_id,
                event_type=EventType.terminal,
                intent=None,
                capability=None,
                payload={"outcome": outcome_val, "outcome_reason": None},
            )
            await record_gestion_event(event=event_row)

            # Build Doris snapshot dict from the returned row
            caps_raw = row["capabilities_used"]
            if isinstance(caps_raw, str):
                caps_list = json.loads(caps_raw)
            elif caps_raw is None:
                caps_list = []
            else:
                # asyncpg returns JSONB as a Python list already
                caps_list = list(caps_raw)

            closed_at = row["closed_at"]
            now_utc = datetime.now(timezone.utc)

            doris_snapshot = {
                "conversation_id": conv_id,
                "tenant_id": row["tenant_id"] or "",
                "project_uid": row["project_uid"] or "",
                "channel": row["channel"] or "",
                "capabilities_used": caps_list,
                "outcome": outcome_val,
                "outcome_reason": None,
                "escalated": False,
                "schema_version": row["schema_version"] or 1,
                "closed_at": closed_at,
                "updated_at": now_utc,
            }
            await record_gestion(snapshot=doris_snapshot)

        except Exception:
            logger.opt(exception=True).warning(
                "gestion_sweep: error processing row conv_id={} (ignored)", conv_id
            )


# ---------------------------------------------------------------------------
# Periodic loop
# ---------------------------------------------------------------------------


async def start_sweep_loop(
    pool: asyncpg.Pool,
    schema: str,
    tenant_ttl_map: dict[str, int],
    interval_seconds: int = 300,
) -> None:
    """Run the sweep loop forever until cancelled.

    Cancel-safe: asyncio.sleep propagates CancelledError, which exits the
    loop cleanly. DB errors inside _sweep_once are swallowed per sweep cycle.
    """
    logger.info(
        "gestion_sweep: loop started — interval={}s tenants={}",
        interval_seconds,
        list(tenant_ttl_map.keys()),
    )
    while True:
        try:
            await asyncio.sleep(interval_seconds)
            await _sweep_once(pool, schema, tenant_ttl_map)
        except asyncio.CancelledError:
            logger.info("gestion_sweep: loop cancelled — shutting down cleanly")
            raise
        except Exception:
            logger.opt(exception=True).warning(
                "gestion_sweep: unexpected error in loop (ignored, will retry)"
            )
