"""
E-Commerce Agents -- change the embedding dimension.

``product_embeddings.embedding`` and ``agent_memories.embedding`` are ``vector(N)`` columns, created
at N = 1536 (text-embedding-3-small) by the frozen baseline. To use an embedding model of another
size -- for example ``nomic-embed-text`` through Ollama (768) -- both columns have to change, and
changing a vector column's dimension makes the stored vectors meaningless. That is why this is a
script an operator runs on purpose and not a migration that would run on every ``dev.sh``.

    uv run --project agents/python python scripts/reset_embeddings.py --dim 768
    # --if-needed makes it a no-op when the columns already have that width (dev.sh --profile uses it)
    # then set EMBEDDING_DIM=768 (and EMBEDDING_MODEL / EMBEDDING_PROVIDER) and re-embed:
    uv run python -m scripts.seed --seed-only   # or: python -m scripts.generate_embeddings

What it does, in one transaction (all or nothing):

* drops the ivfflat index(es) on the embedding columns, remembering their definitions;
* ``TRUNCATE product_embeddings`` -- every stored product vector is deleted;
* sets ``agent_memories.embedding`` to NULL. The memory *rows* are kept: they hold what users told
  the agents, and nothing in the app writes that column today;
* ``ALTER COLUMN ... TYPE vector(N)`` on both tables;
* recreates the indexes from the remembered definitions (on empty data -- the seeder and
  ``generate_embeddings`` REINDEX after loading, which is what makes ivfflat centroids useful).

It refuses a dimension pgvector cannot index. ivfflat and hnsw indexes stop at 2000 dimensions, and
without one a semantic search is a sequential scan. For a larger model (text-embedding-3-large is
3072) ask the API for fewer dimensions with ``dimensions=`` -- the app does that for text-embedding-3
models when EMBEDDING_DIM is smaller -- or store ``halfvec`` instead (indexable to 4000; not done here).

Needs only ``asyncpg`` (any Python that has it; ``uv run --project agents/python`` supplies one). Reads ``DATABASE_URL`` (same default as the rest of the repo).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

import asyncpg

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("reset_embeddings")

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://ecommerce:ecommerce_secret@localhost:5432/ecommerce_agents")

MAX_INDEXABLE_DIM = 2000
TABLES = ("product_embeddings", "agent_memories")
# Used when no index definition was found to carry over (the baseline's own).
BASELINE_INDEX = (
    "CREATE INDEX idx_product_embedding ON public.product_embeddings "
    "USING ivfflat (embedding vector_cosine_ops) WITH (lists = 10)"
)


class ResetRefused(ValueError):
    """The requested dimension cannot be applied."""


def validate_dim(dim: int) -> int:
    """Return ``dim`` if pgvector can store and index it, else raise :class:`ResetRefused`."""
    if isinstance(dim, bool) or not isinstance(dim, int) or dim < 1:
        raise ResetRefused(f"--dim must be a positive integer, got {dim!r}")
    if dim > MAX_INDEXABLE_DIM:
        raise ResetRefused(
            f"{dim} dimensions is over pgvector's {MAX_INDEXABLE_DIM}-dimension limit for ivfflat/hnsw indexes, so "
            "semantic search would be a sequential scan. Ask the embedding API for fewer dimensions with "
            "`dimensions=` (text-embedding-3 models accept it) and use that smaller --dim, or store `halfvec` "
            "(indexable to 4000) -- this script does not do that."
        )
    return dim


async def reset(conn: asyncpg.Connection, dim: int) -> None:
    """Truncate product vectors, clear memory vectors, retype both columns, rebuild the indexes."""
    validate_dim(dim)
    async with conn.transaction():
        indexes = await conn.fetch(
            "SELECT indexname, indexdef FROM pg_indexes "
            "WHERE schemaname = 'public' AND tablename = ANY($1::text[]) AND indexdef ILIKE '%(embedding %'",
            list(TABLES),
        )
        index_defs = [row["indexdef"] for row in indexes]
        for row in indexes:
            await conn.execute(f'DROP INDEX IF EXISTS public."{row["indexname"]}"')

        await conn.execute("TRUNCATE product_embeddings")
        await conn.execute("UPDATE agent_memories SET embedding = NULL WHERE embedding IS NOT NULL")
        # ``dim`` was validated as an int above, so formatting it into the DDL is safe.
        for table in TABLES:
            await conn.execute(f"ALTER TABLE {table} ALTER COLUMN embedding TYPE vector({dim})")

        for definition in index_defs or [BASELINE_INDEX]:
            await conn.execute(definition)


async def current_dims(conn: asyncpg.Connection) -> dict[str, int]:
    rows = await conn.fetch(
        "SELECT c.relname AS t, a.atttypmod AS d FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relname = ANY($1::text[]) AND a.attname = 'embedding' "
        "AND NOT a.attisdropped",
        list(TABLES),
    )
    return {r["t"]: r["d"] for r in rows}


async def main(dim: int, database_url: str, *, if_needed: bool = False) -> int:
    try:
        validate_dim(dim)
    except ResetRefused as exc:
        logger.error("%s", exc)
        return 2
    conn = await asyncpg.connect(database_url)
    try:
        before = await current_dims(conn)
        if if_needed and before and all(width == dim for width in before.values()):
            logger.info("embedding columns are already vector(%d); nothing to do", dim)
            return 0
        await reset(conn, dim)
        after = await current_dims(conn)
    finally:
        await conn.close()
    logger.info("embedding columns: %s -> %s", before, after)
    logger.info(
        "Done. Stored product embeddings were deleted. Set EMBEDDING_DIM=%d (and EMBEDDING_PROVIDER / "
        "EMBEDDING_MODEL), then re-embed: re-run the seeder or `python -m scripts.generate_embeddings`.",
        dim,
    )
    return 0


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Change the pgvector dimension of the embedding columns. DESTROYS stored product embeddings."
    )
    parser.add_argument("--dim", type=int, required=True, help="new vector dimension (1-2000)")
    parser.add_argument("--database-url", default=DATABASE_URL, help="defaults to $DATABASE_URL")
    parser.add_argument(
        "--if-needed",
        action="store_true",
        help="do nothing when the columns already have this dimension (what dev.sh --profile runs)",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args(sys.argv[1:])
    sys.exit(asyncio.run(main(args.dim, args.database_url, if_needed=args.if_needed)))
