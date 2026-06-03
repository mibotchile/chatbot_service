"""Dashboard API for the sales team -- debtor & conversation visibility."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from loguru import logger

from shared.config.settings import settings

dashboard_router = APIRouter(prefix="/api/v1/dashboard", tags=["dashboard"])


# ---------------------------------------------------------------------------
# Auth dependency
# ---------------------------------------------------------------------------

async def verify_dashboard_key(request: Request) -> None:
    """Validate X-Dashboard-Key header against configured secret."""
    if not settings.dashboard_key:
        raise HTTPException(503, "Dashboard not configured")
    key = request.headers.get("X-Dashboard-Key", "")
    if key != settings.dashboard_key:
        raise HTTPException(403, "Invalid dashboard key")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _row_to_dict(row) -> dict[str, Any]:
    """Convert asyncpg Record to JSON-safe dict."""
    d = dict(row)
    for k, v in d.items():
        if isinstance(v, datetime):
            d[k] = v.isoformat()
        elif isinstance(v, date):
            d[k] = v.isoformat()
    return d


async def _safe_fetch(pool, query: str, *args) -> list[dict[str, Any]]:
    """Run a query and return rows as dicts.  Returns [] if table missing."""
    try:
        rows = await pool.fetch(query, *args)
        return [_row_to_dict(r) for r in rows]
    except Exception as exc:
        # UndefinedTableError code = 42P01
        if getattr(exc, "sqlstate", None) == "42P01":
            logger.debug("Table does not exist yet: {}", exc)
            return []
        raise


async def _safe_fetchrow(pool, query: str, *args) -> dict[str, Any] | None:
    """Run a query and return a single row as dict.  None if missing."""
    try:
        row = await pool.fetchrow(query, *args)
        return _row_to_dict(row) if row else None
    except Exception as exc:
        if getattr(exc, "sqlstate", None) == "42P01":
            logger.debug("Table does not exist yet: {}", exc)
            return None
        raise


async def _safe_fetchval(pool, query: str, *args) -> Any:
    """Run a query returning a scalar.  Returns 0 on missing table."""
    try:
        val = await pool.fetchval(query, *args)
        return val if val is not None else 0
    except Exception as exc:
        if getattr(exc, "sqlstate", None) == "42P01":
            return 0
        raise


def _get_pool(request: Request):
    """Get the asyncpg pool from VisitorMemory stored on app.state by the lifespan."""
    vm = getattr(request.app.state, "visitor_memory", None)
    if vm is None or vm._pool is None:
        raise HTTPException(503, "Database not available")
    return vm._pool


def _schema() -> str:
    return settings.database_schema


# ---------------------------------------------------------------------------
# GET /api/v1/dashboard/leads
# ---------------------------------------------------------------------------

@dashboard_router.get("/leads", dependencies=[Depends(verify_dashboard_key)])
async def list_leads(
    request: Request,
    status: str | None = Query(None, description="Filter by debtor_level"),
    from_date: date | None = Query(None, alias="from", description="Created after (YYYY-MM-DD)"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    pool = _get_pool(request)
    schema = _schema()

    conditions: list[str] = []
    args: list[Any] = []
    idx = 1

    if status:
        conditions.append(f"debtor_level = ${idx}")
        args.append(status)
        idx += 1

    if from_date:
        conditions.append(f"created_at >= ${idx}")
        args.append(datetime(from_date.year, from_date.month, from_date.day, tzinfo=timezone.utc))
        idx += 1

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    args.append(limit)
    limit_idx = idx
    idx += 1
    args.append(offset)
    offset_idx = idx

    query = f"""
        SELECT conversation_id, visitor_id, name, email, phone,
               project_interest, debtor_level, created_at
        FROM {schema}.sorelia_debtors
        {where}
        ORDER BY created_at DESC
        LIMIT ${limit_idx} OFFSET ${offset_idx}
    """

    rows = await _safe_fetch(pool, query, *args)

    # Total count for pagination
    count_query = f"SELECT COUNT(*) FROM {schema}.sorelia_debtors {where}"
    count_args = [a for a in args[:len(conditions)]]  # only filter args
    total = await _safe_fetchval(pool, count_query, *count_args) if conditions else await _safe_fetchval(pool, count_query)

    return {"leads": rows, "total": total, "limit": limit, "offset": offset}


# ---------------------------------------------------------------------------
# GET /api/v1/dashboard/leads/{conversation_id}
# ---------------------------------------------------------------------------

@dashboard_router.get("/leads/{conversation_id}", dependencies=[Depends(verify_dashboard_key)])
async def get_lead_detail(request: Request, conversation_id: str):
    pool = _get_pool(request)
    schema = _schema()

    lead = await _safe_fetchrow(
        pool,
        f"""
        SELECT * FROM {schema}.sorelia_debtors
        WHERE conversation_id = $1
        """,
        conversation_id,
    )
    if lead is None:
        raise HTTPException(404, "Lead not found")

    # Fetch conversation history
    messages = await _safe_fetch(
        pool,
        f"""
        SELECT role, content, created_at
        FROM {schema}.sorelia_conversations
        WHERE conversation_id = $1
        ORDER BY created_at ASC
        """,
        conversation_id,
    )

    return {"lead": lead, "messages": messages}


# ---------------------------------------------------------------------------
# GET /api/v1/dashboard/stats
# ---------------------------------------------------------------------------

@dashboard_router.get("/stats", dependencies=[Depends(verify_dashboard_key)])
async def get_stats(request: Request):
    pool = _get_pool(request)
    schema = _schema()

    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    total_conversations = await _safe_fetchval(
        pool,
        f"SELECT COUNT(DISTINCT conversation_id) FROM {schema}.sorelia_conversations",
    )

    total_debtors = await _safe_fetchval(
        pool,
        f"SELECT COUNT(*) FROM {schema}.sorelia_debtors WHERE debtor_level IN ('DEBTOR', 'DEBTOR_VERIFIED')",
    )

    total_verified = await _safe_fetchval(
        pool,
        f"SELECT COUNT(*) FROM {schema}.sorelia_debtors WHERE debtor_level = 'DEBTOR_VERIFIED'",
    )

    debtors_today = await _safe_fetchval(
        pool,
        f"SELECT COUNT(*) FROM {schema}.sorelia_debtors WHERE created_at >= $1",
        today_start,
    )

    top_projects = await _safe_fetch(
        pool,
        f"""
        SELECT project_interest AS name, COUNT(*) AS count
        FROM {schema}.sorelia_debtors
        WHERE project_interest IS NOT NULL AND project_interest != ''
        GROUP BY project_interest
        ORDER BY count DESC
        LIMIT 10
        """,
    )

    total_visitors = await _safe_fetchval(
        pool,
        f"SELECT COUNT(*) FROM {schema}.sorelia_visitors",
    )

    return {
        "total_conversations": total_conversations,
        "total_debtors": total_debtors,
        "total_verified": total_verified,
        "debtors_today": debtors_today,
        "top_projects": top_projects,
        "conversion_funnel": {
            "visitors": total_visitors,
            "debtors": total_debtors,
            "verified": total_verified,
        },
    }


# ---------------------------------------------------------------------------
# GET /api/v1/dashboard/conversations
# ---------------------------------------------------------------------------

@dashboard_router.get("/conversations", dependencies=[Depends(verify_dashboard_key)])
async def list_conversations(
    request: Request,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    pool = _get_pool(request)
    schema = _schema()

    # Get recent conversations with last message and debtor info
    query = f"""
        WITH last_msg AS (
            SELECT DISTINCT ON (conversation_id)
                conversation_id,
                content AS last_message,
                created_at AS last_message_at
            FROM {schema}.sorelia_conversations
            ORDER BY conversation_id, created_at DESC
        )
        SELECT
            lm.conversation_id,
            lm.last_message,
            lm.last_message_at,
            d.debtor_level,
            d.name AS visitor_name,
            d.project_interest
        FROM last_msg lm
        LEFT JOIN {schema}.sorelia_debtors d ON lm.conversation_id = d.conversation_id
        ORDER BY lm.last_message_at DESC
        LIMIT $1 OFFSET $2
    """

    rows = await _safe_fetch(pool, query, limit, offset)

    total = await _safe_fetchval(
        pool,
        f"SELECT COUNT(DISTINCT conversation_id) FROM {schema}.sorelia_conversations",
    )

    return {"conversations": rows, "total": total, "limit": limit, "offset": offset}


# ---------------------------------------------------------------------------
# GET /api/v1/dashboard/visits
# ---------------------------------------------------------------------------

@dashboard_router.get("/visits", dependencies=[Depends(verify_dashboard_key)])
async def list_visits(
    request: Request,
    status: str | None = Query(None, description="Filter by visit status (pending, confirmed, cancelled, completed)"),
    from_date: date | None = Query(None, alias="from", description="Created after (YYYY-MM-DD)"),
    project_slug: str | None = Query(None, description="Filter by project slug"),
    limit: int = Query(50, ge=1, le=200),
):
    pool = _get_pool(request)
    schema = _schema()

    conditions: list[str] = []
    args: list[Any] = []
    idx = 1

    if status:
        conditions.append(f"status = ${idx}")
        args.append(status)
        idx += 1

    if from_date:
        conditions.append(f"created_at >= ${idx}")
        args.append(datetime(from_date.year, from_date.month, from_date.day, tzinfo=timezone.utc))
        idx += 1

    if project_slug:
        conditions.append(f"project_slug = ${idx}")
        args.append(project_slug)
        idx += 1

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    args.append(limit)
    limit_idx = idx

    query = f"""
        SELECT id, conversation_id, visitor_name, visitor_phone, visitor_email,
               project_slug, project_name, visit_date, visit_time,
               sales_agent_name, sales_agent_phone, status, notes,
               google_event_id, created_at
        FROM {schema}.sorelia_visits
        {where}
        ORDER BY created_at DESC
        LIMIT ${limit_idx}
    """

    rows = await _safe_fetch(pool, query, *args)

    count_query = f"SELECT COUNT(*) FROM {schema}.sorelia_visits {where}"
    count_args = [a for a in args[:len(conditions)]]
    total = await _safe_fetchval(pool, count_query, *count_args) if conditions else await _safe_fetchval(pool, count_query)

    return {"visits": rows, "total": total, "limit": limit}
