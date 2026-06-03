"""PostgreSQL persistence for conversations and debtors.

Tables (auto-created on startup):
  - {schema}.sorelia_conversations  — full conversation state (history, debtor, context)
  - {schema}.sorelia_debtors        — denormalized debtor rows for easy querying/export

NOTE: sorelia_conversations retains the lead_data column until the atomic migration
runs at deploy time.  Code writes debtor_data and reads debtor_data with fallback to
lead_data (dual-read pattern).  sorelia_debtors is the post-migration name for what
was sorelia_leads; the migration script handles the RENAME atomically.
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
    """Create sorelia_conversations and sorelia_debtors tables if they don't exist."""
    if not _SAFE_IDENTIFIER.match(schema):
        raise ValueError(f"Unsafe schema name: {schema!r}")

    conv_table = _q(schema, "sorelia_conversations")
    debtor_table = _q(schema, "sorelia_debtors")

    async with pool.acquire() as conn:
        await conn.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
        await conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {conv_table} (
                conversation_id TEXT PRIMARY KEY,
                visitor_id TEXT,
                history JSONB DEFAULT '[]',
                debtor_data JSONB DEFAULT '{{}}'::jsonb,
                debtor_level TEXT DEFAULT 'VISITOR',
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
            CREATE TABLE IF NOT EXISTS {debtor_table} (
                id SERIAL PRIMARY KEY,
                conversation_id TEXT,
                visitor_id TEXT,
                name TEXT,
                email TEXT,
                phone TEXT,
                project_interest TEXT,
                debtor_level TEXT DEFAULT 'VISITOR',
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
    debtor_data: dict | None = None,
    debtor_level: str = "VISITOR",
    page_context: dict | None = None,
) -> None:
    """Upsert full conversation state."""
    table = _q(schema, "sorelia_conversations")
    now = datetime.now(timezone.utc)
    await pool.execute(
        f"""
        INSERT INTO {table}
            (conversation_id, visitor_id, history, debtor_data, debtor_level, page_context, created_at, updated_at)
        VALUES ($1, $2, $3::jsonb, $4::jsonb, $5, $6::jsonb, $7, $7)
        ON CONFLICT (conversation_id) DO UPDATE SET
            visitor_id = COALESCE($2, {table}.visitor_id),
            history = $3::jsonb,
            debtor_data = $4::jsonb,
            debtor_level = $5,
            page_context = $6::jsonb,
            updated_at = $7
        """,
        conversation_id,
        visitor_id,
        json.dumps(history or []),
        json.dumps(debtor_data or {}),
        debtor_level,
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
    for key in ("history", "debtor_data", "lead_data", "page_context"):
        if key in d and isinstance(d[key], str):
            d[key] = json.loads(d[key])
    return d


# -- Denormalized debtors --

async def upsert_debtor(
    pool: asyncpg.Pool,
    schema: str,
    conversation_id: str,
    visitor_id: str | None,
    debtor_data: dict,
    debtor_level: str,
) -> None:
    """Upsert a denormalized debtor row for easy querying/export."""
    table = _q(schema, "sorelia_debtors")
    now = datetime.now(timezone.utc)

    # Sanitize: cast all values to str (LLM may send int for phone)
    def _s(val):
        return str(val) if val is not None else None
    debtor_data = {k: _s(v) for k, v in debtor_data.items()}

    # Check if a debtor for this conversation already exists
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
                debtor_level = $7,
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
                 project_interest, debtor_level, source, created_at, updated_at)
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
