"""asyncpg connection pool management."""

from __future__ import annotations

import asyncpg

from shared.config import settings

_pool: asyncpg.Pool | None = None


async def init_db_pool() -> None:
    """Initialize the asyncpg connection pool. Call in agent lifespan startup."""
    global _pool
    _pool = await asyncpg.create_pool(
        settings.DATABASE_URL,
        min_size=5,
        max_size=20,
    )


def get_pool() -> asyncpg.Pool:
    """Get the connection pool. Raises if not initialized."""
    if _pool is None:
        raise RuntimeError("DB pool not initialized — call init_db_pool() first")
    return _pool


async def close_db_pool() -> None:
    """Close the connection pool. Call in agent lifespan shutdown."""
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
