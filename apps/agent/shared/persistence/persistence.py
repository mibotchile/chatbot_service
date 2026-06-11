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

        # -- Layer 3: gestiones snapshot (1 row per conversation) --
        gestiones_table = _q(schema, "gestiones")
        await conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {gestiones_table} (
                conversation_id    TEXT PRIMARY KEY,
                tenant_id          TEXT,
                project_uid        TEXT,
                channel            TEXT,
                document           TEXT,
                account_id         TEXT,
                credit_state       TEXT,
                outcome            TEXT,
                outcome_reason     TEXT,
                capabilities_used  JSONB DEFAULT '[]'::jsonb,
                escalated          BOOLEAN DEFAULT FALSE,
                commitment_date    DATE,
                commitment_amount  NUMERIC,
                selected_credit_id TEXT,
                schema_version     SMALLINT DEFAULT 1,
                created_at         TIMESTAMPTZ DEFAULT NOW(),
                updated_at         TIMESTAMPTZ DEFAULT NOW(),
                closed_at          TIMESTAMPTZ
            )
        """)
        await conn.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_gestiones_open
            ON {gestiones_table}(updated_at) WHERE closed_at IS NULL
        """)
        await conn.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_gestiones_tenant
            ON {gestiones_table}(tenant_id)
        """)

        # -- Layer 3: gestion_events append-only journal --
        events_table = _q(schema, "gestion_events")
        await conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {events_table} (
                event_id        BIGSERIAL PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                ts              TIMESTAMPTZ DEFAULT NOW(),
                event_type      TEXT NOT NULL,
                intent          TEXT,
                capability      TEXT,
                payload         JSONB DEFAULT '{{}}'::jsonb
            )
        """)
        await conn.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_gestion_events_conv
            ON {events_table}(conversation_id, ts)
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


# -- Layer 3: Gestion journal + snapshot write functions --

async def append_gestion_event(
    pool: asyncpg.Pool,
    schema: str,
    conversation_id: str,
    *,
    event_type: str,
    intent: str | None = None,
    capability: str | None = None,
    payload: dict | None = None,
) -> dict:
    """Append one row to the gestion_events journal.

    Returns a dict with at least ``event_id`` (int) and ``ts`` (datetime),
    ready to be forwarded to the Doris sink.
    """
    table = _q(schema, "gestion_events")
    row = await pool.fetchrow(
        f"""
        INSERT INTO {table}
            (conversation_id, event_type, intent, capability, payload)
        VALUES ($1, $2, $3, $4, $5::jsonb)
        RETURNING event_id, conversation_id, ts, event_type, intent, capability, payload
        """,
        conversation_id,
        event_type,
        intent,
        capability,
        json.dumps(payload or {}),
    )
    return dict(row)


async def upsert_gestion(
    pool: asyncpg.Pool,
    schema: str,
    conversation_id: str,
    *,
    fields: dict,
) -> None:
    """Upsert the gestiones snapshot for a conversation.

    Merge rules:
    - ``capabilities_used``: accumulated (union, no duplicates).  Pass the
      full desired list; the function computes the union with the stored value.
    - ``closed_at`` / ``outcome`` / ``outcome_reason``: only set when provided
      and NOT already set (COALESCE keeps the first terminal value — first
      close wins).
    - All other fields: overwrite with the provided value when not None.
    """
    table = _q(schema, "gestiones")

    # Capabilities: merge provided list with existing stored list (de-duplicate).
    new_caps = fields.get("capabilities_used")
    if new_caps is not None:
        # Fetch existing and union
        existing_row = await pool.fetchrow(
            f"SELECT capabilities_used FROM {table} WHERE conversation_id = $1",
            conversation_id,
        )
        if existing_row is not None:
            stored = existing_row["capabilities_used"]
            if isinstance(stored, str):
                stored = json.loads(stored)
            merged = list(dict.fromkeys(list(stored) + list(new_caps)))
        else:
            merged = list(dict.fromkeys(new_caps))
        caps_json = json.dumps(merged)
    else:
        caps_json = None

    await pool.execute(
        f"""
        INSERT INTO {table} (
            conversation_id, tenant_id, project_uid, channel,
            document, account_id, credit_state,
            outcome, outcome_reason,
            capabilities_used,
            escalated, commitment_date, commitment_amount, selected_credit_id,
            schema_version, created_at, updated_at, closed_at
        )
        VALUES (
            $1,
            $2, $3, $4,
            $5, $6, $7,
            $8, $9,
            COALESCE($10::jsonb, '[]'::jsonb),
            COALESCE($11, FALSE), $12, $13, $14,
            COALESCE($15, 1), NOW(), NOW(), $16
        )
        ON CONFLICT (conversation_id) DO UPDATE SET
            tenant_id          = COALESCE($2, {table}.tenant_id),
            project_uid        = COALESCE($3, {table}.project_uid),
            channel            = COALESCE($4, {table}.channel),
            document           = COALESCE($5, {table}.document),
            account_id         = COALESCE($6, {table}.account_id),
            credit_state       = COALESCE($7, {table}.credit_state),
            outcome            = COALESCE({table}.outcome, $8),
            outcome_reason     = COALESCE({table}.outcome_reason, $9),
            capabilities_used  = CASE
                                     WHEN $10 IS NOT NULL THEN $10::jsonb
                                     ELSE {table}.capabilities_used
                                 END,
            escalated          = COALESCE($11, {table}.escalated),
            commitment_date    = COALESCE($12, {table}.commitment_date),
            commitment_amount  = COALESCE($13, {table}.commitment_amount),
            selected_credit_id = COALESCE($14, {table}.selected_credit_id),
            schema_version     = COALESCE($15, {table}.schema_version),
            updated_at         = NOW(),
            closed_at          = COALESCE({table}.closed_at, $16)
        """,
        conversation_id,
        fields.get("tenant_id"),
        fields.get("project_uid"),
        fields.get("channel"),
        fields.get("document"),
        fields.get("account_id"),
        fields.get("credit_state"),
        fields.get("outcome"),
        fields.get("outcome_reason"),
        caps_json,
        fields.get("escalated"),
        fields.get("commitment_date"),
        fields.get("commitment_amount"),
        fields.get("selected_credit_id"),
        fields.get("schema_version"),
        fields.get("closed_at"),
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
