"""Async connection pool for PostgreSQL via asyncpg."""

from __future__ import annotations

import asyncpg
from config.settings import settings

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    """Return the shared connection pool, creating it on first call."""
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            dsn=settings.database_url,
            min_size=2,
            max_size=10,
            server_settings={"search_path": settings.database_schema},
        )
    return _pool


async def close_pool() -> None:
    """Close the connection pool. Call on app shutdown."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
