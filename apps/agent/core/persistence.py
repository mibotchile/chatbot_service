"""PostgreSQL persistence for conversations and leads.

Tables (auto-created on startup):
  - {schema}.sorelia_conversations  — full conversation state (history, lead, context)
  - {schema}.sorelia_leads          — denormalized lead rows for easy querying/export
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

import asyncpg
from loguru import logger

_SAFE_IDENTIFIER = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _q(schema: str, table: str) -> str:
    """Return schema-qualified table name. Schema is validated at init time."""
    return f"{schema}.{table}"


async def ensure_tables(pool: asyncpg.Pool, schema: str) -> None:
    """Create sorelia_conversations and sorelia_leads tables if they don't exist."""
    if not _SAFE_IDENTIFIER.match(schema):
        raise ValueError(f"Unsafe schema name: {schema!r}")

    conv_table = _q(schema, "sorelia_conversations")
    lead_table = _q(schema, "sorelia_leads")

    async with pool.acquire() as conn:
        await conn.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
        await conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {conv_table} (
                conversation_id TEXT PRIMARY KEY,
                visitor_id TEXT,
                history JSONB DEFAULT '[]',
                lead_data JSONB DEFAULT '{{}}'::jsonb,
                lead_level TEXT DEFAULT 'VISITOR',
                page_context JSONB DEFAULT '{{}}'::jsonb,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        await conn.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_conv_visitor
            ON {conv_table}(visitor_id)
        """)
        await conn.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_conv_updated
            ON {conv_table}(updated_at)
        """)
        await conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {lead_table} (
                id SERIAL PRIMARY KEY,
                conversation_id TEXT,
                visitor_id TEXT,
                name TEXT,
                email TEXT,
                phone TEXT,
                district_interest TEXT,
                project_interest TEXT,
                purpose TEXT,
                budget TEXT,
                lead_level TEXT DEFAULT 'VISITOR',
                source TEXT DEFAULT 'web',
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

    logger.info("Persistence tables ensured (schema={})", schema)


# -- Conversation state --

async def save_conversation(
    pool: asyncpg.Pool,
    schema: str,
    conversation_id: str,
    *,
    visitor_id: str | None = None,
    history: list[dict] | None = None,
    lead_data: dict | None = None,
    lead_level: str = "VISITOR",
    page_context: dict | None = None,
) -> None:
    """Upsert full conversation state."""
    table = _q(schema, "sorelia_conversations")
    now = datetime.now(timezone.utc)
    await pool.execute(
        f"""
        INSERT INTO {table}
            (conversation_id, visitor_id, history, lead_data, lead_level, page_context, created_at, updated_at)
        VALUES ($1, $2, $3::jsonb, $4::jsonb, $5, $6::jsonb, $7, $7)
        ON CONFLICT (conversation_id) DO UPDATE SET
            visitor_id = COALESCE($2, {table}.visitor_id),
            history = $3::jsonb,
            lead_data = $4::jsonb,
            lead_level = $5,
            page_context = $6::jsonb,
            updated_at = $7
        """,
        conversation_id,
        visitor_id,
        json.dumps(history or []),
        json.dumps(lead_data or {}),
        lead_level,
        json.dumps(page_context or {}),
        now,
    )


async def load_conversation(
    pool: asyncpg.Pool,
    schema: str,
    conversation_id: str,
) -> dict | None:
    """Load conversation state. Returns None if not found."""
    table = _q(schema, "sorelia_conversations")
    row = await pool.fetchrow(
        f"SELECT * FROM {table} WHERE conversation_id = $1",
        conversation_id,
    )
    if row is None:
        return None

    d = dict(row)
    for key in ("history", "lead_data", "page_context"):
        if key in d and isinstance(d[key], str):
            d[key] = json.loads(d[key])
    return d


# -- Denormalized leads --

async def upsert_lead(
    pool: asyncpg.Pool,
    schema: str,
    conversation_id: str,
    visitor_id: str | None,
    lead_data: dict,
    lead_level: str,
) -> None:
    """Upsert a denormalized lead row for easy querying/export."""
    table = _q(schema, "sorelia_leads")
    now = datetime.now(timezone.utc)

    # Sanitize: cast all lead values to str (LLM may send int for phone/budget)
    def _s(val):
        return str(val) if val is not None else None
    lead_data = {k: _s(v) for k, v in lead_data.items()}

    # Check if a lead for this conversation already exists
    existing = await pool.fetchval(
        f"SELECT id FROM {table} WHERE conversation_id = $1",
        conversation_id,
    )

    if existing:
        await pool.execute(
            f"""
            UPDATE {table} SET
                visitor_id = COALESCE($2, visitor_id),
                name = COALESCE($3, name),
                email = COALESCE($4, email),
                phone = COALESCE($5, phone),
                district_interest = COALESCE($6, district_interest),
                project_interest = COALESCE($7, project_interest),
                purpose = COALESCE($8, purpose),
                budget = COALESCE($9, budget),
                lead_level = $10,
                updated_at = $11
            WHERE conversation_id = $1
            """,
            conversation_id,
            visitor_id,
            lead_data.get("name"),
            lead_data.get("email"),
            lead_data.get("phone"),
            lead_data.get("district"),
            lead_data.get("project_interest"),
            lead_data.get("purpose"),
            lead_data.get("budget"),
            lead_level,
            now,
        )
    else:
        await pool.execute(
            f"""
            INSERT INTO {table}
                (conversation_id, visitor_id, name, email, phone,
                 district_interest, project_interest, purpose, budget,
                 lead_level, source, created_at, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $12)
            """,
            conversation_id,
            visitor_id,
            lead_data.get("name"),
            lead_data.get("email"),
            lead_data.get("phone"),
            lead_data.get("district"),
            lead_data.get("project_interest"),
            lead_data.get("purpose"),
            lead_data.get("budget"),
            lead_level,
            "web",
            now,
        )


# -- Legacy helpers (kept for backwards compatibility with existing async API) --

async def save_message(
    pool: asyncpg.Pool,
    conversation_id: str,
    role: str,
    content: str,
) -> None:
    """No-op kept for backwards compatibility. Saving is now done via save_conversation."""
    pass


async def get_history(
    pool: asyncpg.Pool,
    conversation_id: str,
    limit: int = 20,
) -> list[dict]:
    """No-op kept for backwards compatibility. Loading is now done via load_conversation."""
    return []


async def save_lead_data(
    pool: asyncpg.Pool,
    conversation_id: str,
    data: dict,
) -> None:
    """No-op kept for backwards compatibility. Saving is now done via save_conversation."""
    pass


async def get_lead_data(
    pool: asyncpg.Pool,
    conversation_id: str,
) -> dict:
    """No-op kept for backwards compatibility. Loading is now done via load_conversation."""
    return {}
