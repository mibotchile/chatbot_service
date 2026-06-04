"""PostgreSQL persistence for conversations and per-type projection tables.

Tables (auto-created on startup):
  - {schema}.conversations   — full conversation state (history, record, context)
  - {schema}.visitors        — visitor profiles (created by VisitorMemory)
  - {schema}.{projection}    — per-type projection table (e.g. 'debtors' for cobranza)
                               created only when ensure_tables receives projection_table != None

Naming is neutral: no sorelia_ prefix. DB is empty on first deploy so no migration needed.
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


async def ensure_tables(
    pool: asyncpg.Pool,
    schema: str,
    projection_table: str | None = None,
) -> None:
    """Create conversations table and optional per-type projection table.

    Args:
        pool: asyncpg connection pool.
        schema: DB schema name (validated against safe-identifier pattern).
        projection_table: Per-agent-type table name (e.g. 'debtors' for cobranza).
            When None, only the common conversations table is created.
    """
    if not _SAFE_IDENTIFIER.match(schema):
        raise ValueError(f"Unsafe schema name: {schema!r}")

    conv_table = _q(schema, "conversations")

    async with pool.acquire() as conn:
        await conn.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
        await conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {conv_table} (
                conversation_id TEXT PRIMARY KEY,
                visitor_id TEXT,
                history JSONB DEFAULT '[]',
                record_data JSONB DEFAULT '{{}}'::jsonb,
                record_level TEXT DEFAULT 'VISITOR',
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

        if projection_table is not None:
            if not _SAFE_IDENTIFIER.match(projection_table):
                raise ValueError(f"Unsafe projection_table name: {projection_table!r}")
            proj_table = _q(schema, projection_table)
            await conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {proj_table} (
                    id SERIAL PRIMARY KEY,
                    conversation_id TEXT,
                    visitor_id TEXT,
                    name TEXT,
                    email TEXT,
                    phone TEXT,
                    project_interest TEXT,
                    record_level TEXT DEFAULT 'VISITOR',
                    source TEXT DEFAULT 'web',
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)

    logger.info("Persistence tables ensured (schema={}, projection_table={})", schema, projection_table)


# -- Conversation state --

async def save_conversation(
    pool: asyncpg.Pool,
    schema: str,
    conversation_id: str,
    *,
    visitor_id: str | None = None,
    history: list[dict] | None = None,
    record_data: dict | None = None,
    record_level: str = "VISITOR",
    page_context: dict | None = None,
) -> None:
    """Upsert full conversation state."""
    table = _q(schema, "conversations")
    now = datetime.now(timezone.utc)
    await pool.execute(
        f"""
        INSERT INTO {table}
            (conversation_id, visitor_id, history, record_data, record_level, page_context, created_at, updated_at)
        VALUES ($1, $2, $3::jsonb, $4::jsonb, $5, $6::jsonb, $7, $7)
        ON CONFLICT (conversation_id) DO UPDATE SET
            visitor_id = COALESCE($2, {table}.visitor_id),
            history = $3::jsonb,
            record_data = $4::jsonb,
            record_level = $5,
            page_context = $6::jsonb,
            updated_at = $7
        """,
        conversation_id,
        visitor_id,
        json.dumps(history or []),
        json.dumps(record_data or {}),
        record_level,
        json.dumps(page_context or {}),
        now,
    )


async def load_conversation(
    pool: asyncpg.Pool,
    schema: str,
    conversation_id: str,
) -> dict | None:
    """Load conversation state. Returns None if not found."""
    table = _q(schema, "conversations")
    row = await pool.fetchrow(
        f"SELECT * FROM {table} WHERE conversation_id = $1",
        conversation_id,
    )
    if row is None:
        return None

    d = dict(row)
    for key in ("history", "record_data", "page_context"):
        if key in d and isinstance(d[key], str):
            d[key] = json.loads(d[key])
    return d


# -- Denormalized projection rows (per-type) --

async def upsert_debtor(
    pool: asyncpg.Pool,
    schema: str,
    conversation_id: str,
    visitor_id: str | None,
    debtor_data: dict,
    debtor_level: str,
    projection_table: str = "debtors",
) -> None:
    """Upsert a denormalized row in the per-type projection table."""
    table = _q(schema, projection_table)
    now = datetime.now(timezone.utc)

    # Sanitize: cast all values to str (LLM may send int for phone)
    def _s(val):
        return str(val) if val is not None else None
    debtor_data = {k: _s(v) for k, v in debtor_data.items()}

    # Check if a row for this conversation already exists
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
                project_interest = COALESCE($6, project_interest),
                record_level = $7,
                updated_at = $8
            WHERE conversation_id = $1
            """,
            conversation_id,
            visitor_id,
            debtor_data.get("name"),
            debtor_data.get("email"),
            debtor_data.get("phone"),
            debtor_data.get("project_interest"),
            debtor_level,
            now,
        )
    else:
        await pool.execute(
            f"""
            INSERT INTO {table}
                (conversation_id, visitor_id, name, email, phone,
                 project_interest, record_level, source, created_at, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $9)
            """,
            conversation_id,
            visitor_id,
            debtor_data.get("name"),
            debtor_data.get("email"),
            debtor_data.get("phone"),
            debtor_data.get("project_interest"),
            debtor_level,
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
