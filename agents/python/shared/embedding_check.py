"""Startup check: the configured ``EMBEDDING_DIM`` must equal the stored vector columns' width.

``product_embeddings.embedding`` and ``agent_memories.embedding`` are ``vector(N)`` columns created
by the frozen baseline at ``N = 1536``. Pointing ``EMBEDDING_DIM`` (or a new embedding model) at a
different size without changing the columns used to surface only on the first insert or query, as an
opaque pgvector error. This fails at startup instead, with a message that names the fix.

Changing a column's dimension destroys the stored vectors, so the fix is an explicit operator action
(``scripts/reset_embeddings.py --dim N``), deliberately not a migration.
"""

from __future__ import annotations

import logging
from typing import Any

from shared.embedding_config import embedding_dim

logger = logging.getLogger(__name__)

EMBEDDING_TABLES = ("product_embeddings", "agent_memories")

_COLUMN_DIMS_SQL = """
SELECT c.relname AS table_name, a.atttypmod AS dim
FROM pg_attribute a
JOIN pg_class c ON c.oid = a.attrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relname = ANY($1::text[]) AND a.attname = 'embedding' AND NOT a.attisdropped
"""


class EmbeddingDimensionError(RuntimeError):
    """``EMBEDDING_DIM`` disagrees with a stored vector column. The message names the fix."""


async def column_dimensions(conn: Any) -> dict[str, int | None]:
    """Declared ``vector(N)`` width per embedding table (``None`` for an unconstrained ``vector``).

    A table that does not exist is simply absent from the result.
    """
    rows = await conn.fetch(_COLUMN_DIMS_SQL, list(EMBEDDING_TABLES))
    # pgvector stores the declared dimension directly in atttypmod; -1 means "no dimension".
    return {r["table_name"]: (r["dim"] if r["dim"] > 0 else None) for r in rows}


def dimension_problems(configured: int, actual: dict[str, int | None]) -> list[str]:
    """One line per table whose declared width differs from ``configured``."""
    return [
        f"{table}.embedding is vector({dim})"
        for table, dim in sorted(actual.items())
        if dim is not None and dim != configured
    ]


async def verify_embedding_dimensions(conn: Any) -> None:
    """Raise :class:`EmbeddingDimensionError` if any embedding column is not ``EMBEDDING_DIM`` wide."""
    configured = embedding_dim()
    problems = dimension_problems(configured, await column_dimensions(conn))
    if not problems:
        return
    raise EmbeddingDimensionError(
        f"EMBEDDING_DIM={configured} but {'; '.join(problems)}. Either set EMBEDDING_DIM to the column's size, "
        "or run `uv run --project agents/python python scripts/reset_embeddings.py "
        f"--dim {configured}` to change the columns "
        "(this deletes every stored embedding; re-run the seeder or scripts/generate_embeddings.py afterwards)."
    )


async def verify_embedding_dimensions_on_pool(pool: Any) -> None:
    """Startup hook: check on a pooled connection."""
    async with pool.acquire() as conn:
        await verify_embedding_dimensions(conn)
    logger.info("embeddings.dimension_ok dim=%d", embedding_dim())
