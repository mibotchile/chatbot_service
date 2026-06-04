"""Visitor memory backed by PostgreSQL.

Tracks returning visitors across conversations: name, contact info,
preferences, projects viewed, and conversation summaries.  Uses asyncpg
directly -- no ORM.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from typing import Any

import asyncpg
from loguru import logger

# Only allow simple alphanumeric + underscore schema names (SQL identifier safe)
_SAFE_IDENTIFIER = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


class VisitorMemory:
    """Persistent visitor profiles stored in PostgreSQL."""

    def __init__(self, database_url: str, schema: str = "prod"):
        if not _SAFE_IDENTIFIER.match(schema):
            raise ValueError(f"Unsafe schema name: {schema!r}")
        self.database_url = database_url
        self.schema = schema
        self._pool: asyncpg.Pool | None = None

    # -- lifecycle --

    async def init(self) -> None:
        """Create connection pool and ensure table exists."""
        try:
            self._pool = await asyncpg.create_pool(
                self.database_url,
                min_size=1,
                max_size=5,
            )
            await self._ensure_table()
            logger.info("VisitorMemory initialised (schema={})", self.schema)
        except Exception:
            logger.warning("VisitorMemory DB unavailable -- running without visitor persistence")
            self._pool = None

    async def _ensure_table(self) -> None:
        assert self._pool is not None
        async with self._pool.acquire() as conn:
            await conn.execute(f"CREATE SCHEMA IF NOT EXISTS {self.schema}")
            await conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {self.schema}.visitors (
                    visitor_id   TEXT PRIMARY KEY,
                    name         TEXT,
                    email        TEXT,
                    phone        TEXT,
                    preferences  JSONB DEFAULT '{{}}'::jsonb,
                    projects_viewed TEXT[] DEFAULT '{{}}',
                    conversation_summaries JSONB DEFAULT '[]'::jsonb,
                    record_data  JSONB DEFAULT '{{}}'::jsonb,
                    first_visit  TIMESTAMPTZ DEFAULT NOW(),
                    last_visit   TIMESTAMPTZ DEFAULT NOW(),
                    visit_count  INTEGER DEFAULT 1,
                    messages_today    INTEGER DEFAULT 0,
                    last_message_date DATE DEFAULT CURRENT_DATE
                )
            """)
            # Add columns to existing tables (idempotent)
            await conn.execute(f"""
                ALTER TABLE {self.schema}.visitors
                ADD COLUMN IF NOT EXISTS messages_today INTEGER DEFAULT 0
            """)
            await conn.execute(f"""
                ALTER TABLE {self.schema}.visitors
                ADD COLUMN IF NOT EXISTS last_message_date DATE DEFAULT CURRENT_DATE
            """)
            await conn.execute(f"""
                ALTER TABLE {self.schema}.visitors
                ADD COLUMN IF NOT EXISTS searches JSONB DEFAULT '[]'::jsonb
            """)
            await conn.execute(f"""
                ALTER TABLE {self.schema}.visitors
                ADD COLUMN IF NOT EXISTS entry_source TEXT DEFAULT 'direct'
            """)

    async def close(self) -> None:
        """Close connection pool."""
        if self._pool:
            await self._pool.close()
            self._pool = None

    # -- queries --

    async def get_visitor(self, visitor_id: str) -> dict[str, Any] | None:
        """Get visitor profile.  Returns None if unknown or DB unavailable."""
        if not self._pool:
            return None
        try:
            row = await self._pool.fetchrow(
                f"SELECT * FROM {self.schema}.visitors WHERE visitor_id = $1",
                visitor_id,
            )
            if row is None:
                return None
            return _row_to_dict(row)
        except Exception:
            logger.opt(exception=True).warning("get_visitor failed")
            return None

    async def upsert_visitor(self, visitor_id: str, data: dict[str, Any]) -> None:
        """Create or update visitor.  Merges data, increments visit_count."""
        if not self._pool:
            return
        try:
            existing = await self.get_visitor(visitor_id)
            if existing is None:
                # Insert new visitor
                await self._pool.execute(
                    f"""
                    INSERT INTO {self.schema}.visitors
                        (visitor_id, name, email, phone, preferences, record_data, entry_source)
                    VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb, $7)
                    """,
                    visitor_id,
                    data.get("name"),
                    data.get("email"),
                    data.get("phone"),
                    json.dumps(data.get("preferences", {})),
                    json.dumps(data.get("record_data", {})),
                    data.get("entry_source", "direct"),
                )
            else:
                # Merge update -- only overwrite non-null fields
                sets: list[str] = [
                    "last_visit = NOW()",
                    "visit_count = visit_count + 1",
                ]
                args: list[Any] = [visitor_id]
                idx = 2

                for col in ("name", "email", "phone"):
                    if data.get(col):
                        sets.append(f"{col} = ${idx}")
                        args.append(data[col])
                        idx += 1

                if data.get("preferences"):
                    sets.append(f"preferences = preferences || ${idx}::jsonb")
                    args.append(json.dumps(data["preferences"]))
                    idx += 1

                if data.get("record_data"):
                    sets.append(f"record_data = record_data || ${idx}::jsonb")
                    args.append(json.dumps(data["record_data"]))
                    idx += 1

                set_clause = ", ".join(sets)
                await self._pool.execute(
                    f"UPDATE {self.schema}.visitors SET {set_clause} WHERE visitor_id = $1",
                    *args,
                )
        except Exception:
            logger.opt(exception=True).warning("upsert_visitor failed")

    async def add_project_viewed(self, visitor_id: str, project_slug: str) -> None:
        """Add project to viewed list (deduped via array_append + array_remove)."""
        if not self._pool:
            return
        try:
            # Ensure visitor exists first
            exists = await self._pool.fetchval(
                f"SELECT 1 FROM {self.schema}.visitors WHERE visitor_id = $1",
                visitor_id,
            )
            if not exists:
                await self._pool.execute(
                    f"""
                    INSERT INTO {self.schema}.visitors (visitor_id, projects_viewed)
                    VALUES ($1, ARRAY[$2]::text[])
                    """,
                    visitor_id,
                    project_slug,
                )
            else:
                await self._pool.execute(
                    f"""
                    UPDATE {self.schema}.visitors
                    SET projects_viewed = array_append(
                            array_remove(projects_viewed, $2), $2
                        ),
                        last_visit = NOW()
                    WHERE visitor_id = $1
                    """,
                    visitor_id,
                    project_slug,
                )
        except Exception:
            logger.opt(exception=True).warning("add_project_viewed failed")

    async def add_search(self, visitor_id: str, search_data: dict) -> None:
        """Append a search query to visitor's search history (last 20)."""
        if not self._pool:
            return
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(f"""
                    UPDATE {self.schema}.visitors
                    SET searches = (
                        SELECT jsonb_agg(elem)
                        FROM (
                            SELECT elem FROM jsonb_array_elements(
                                COALESCE(searches, '[]'::jsonb) || $2::jsonb
                            ) elem
                            ORDER BY elem->>'ts' DESC
                            LIMIT 20
                        ) sub
                    )
                    WHERE visitor_id = $1
                """, visitor_id, json.dumps(search_data))
        except Exception:
            logger.opt(exception=True).debug("Failed to add search")

    async def check_daily_limit(self, visitor_id: str, limit: int = 50) -> tuple[bool, int]:
        """Check if visitor exceeded daily message limit.

        Returns (allowed, remaining).  Resets count when date changes.
        Falls back to (True, limit) if DB is unavailable.
        """
        if not self._pool:
            return True, limit
        try:
            row = await self._pool.fetchrow(
                f"""
                SELECT messages_today, last_message_date
                FROM {self.schema}.visitors
                WHERE visitor_id = $1
                """,
                visitor_id,
            )
            if row is None:
                return True, limit

            last_date = row["last_message_date"]
            count = row["messages_today"] or 0

            # Reset counter if day changed
            if last_date is None or last_date < date.today():
                count = 0

            remaining = max(limit - count, 0)
            return remaining > 0, remaining
        except Exception:
            logger.opt(exception=True).warning("check_daily_limit failed")
            return True, limit

    async def increment_daily_count(self, visitor_id: str) -> None:
        """Increment today's message counter for a visitor.

        Resets counter to 1 if the date rolled over since last message.
        """
        if not self._pool:
            return
        try:
            await self._pool.execute(
                f"""
                UPDATE {self.schema}.visitors
                SET messages_today = CASE
                        WHEN last_message_date < CURRENT_DATE THEN 1
                        ELSE COALESCE(messages_today, 0) + 1
                    END,
                    last_message_date = CURRENT_DATE
                WHERE visitor_id = $1
                """,
                visitor_id,
            )
        except Exception:
            logger.opt(exception=True).warning("increment_daily_count failed")

    async def save_conversation_summary(self, visitor_id: str, summary: str) -> None:
        """Append conversation summary to history."""
        if not self._pool:
            return
        try:
            entry = json.dumps({
                "summary": summary,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            await self._pool.execute(
                f"""
                UPDATE {self.schema}.visitors
                SET conversation_summaries = conversation_summaries || $2::jsonb,
                    last_visit = NOW()
                WHERE visitor_id = $1
                """,
                visitor_id,
                f"[{entry}]",
            )
        except Exception:
            logger.opt(exception=True).warning("save_conversation_summary failed")


def _row_to_dict(row: asyncpg.Record) -> dict[str, Any]:
    """Convert asyncpg Record to a plain dict with JSON-safe values."""
    d = dict(row)
    for key in ("preferences", "conversation_summaries", "record_data", "searches"):
        if key in d and isinstance(d[key], str):
            d[key] = json.loads(d[key])
    for key in ("first_visit", "last_visit"):
        if key in d and isinstance(d[key], datetime):
            d[key] = d[key].isoformat()
    if d.get("projects_viewed") is None:
        d["projects_viewed"] = []
    else:
        d["projects_viewed"] = list(d["projects_viewed"])
    return d
