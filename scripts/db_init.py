"""E-Commerce Agents -- one-shot database bootstrap (Azure Container Apps job).

Runs, in order, and stops at the first failure (exit 1):

1. **migrate**  -- ``scripts.migrate.migrate``: baseline + numbered migrations, advisory-locked, idempotent.
2. **seed**     -- ``scripts.seed.main(if_empty=True)``: only when ``users`` has no rows. Never TRUNCATEs.
3. **embeddings** -- only when ``product_embeddings`` is still empty after the seed (which loads a precomputed
   file when one matches) and a real embedding provider is configured. Under ``LLM_PROVIDER=replay`` or no
   provider the step is skipped with a warning. ``scripts.generate_embeddings`` ends with the REINDEX of
   ``idx_product_embedding``.
4. **analyze**  -- ``ANALYZE``.

Every step is idempotent, so the job can run on every deployment. Logs are one JSON object per line.

    DATABASE_URL=postgresql://... python -m scripts.db_init
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

import asyncpg

logger = logging.getLogger("db_init")

DEFAULT_DSN = "postgresql://ecommerce:ecommerce_secret@localhost:5432/ecommerce_agents"


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        extra = getattr(record, "fields", None)
        if isinstance(extra, dict):
            payload.update(extra)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)


def _log(level: int, message: str, **fields: object) -> None:
    logger.log(level, message, extra={"fields": fields})


async def _count(dsn: str, table: str) -> int:
    conn = await asyncpg.connect(dsn)
    try:
        return int(await conn.fetchval(f"SELECT COUNT(*) FROM {table}"))  # fixed table names only
    finally:
        await conn.close()


async def step_migrate(dsn: str) -> None:
    from scripts.migrate import migrate

    applied = await migrate(dsn)
    _log(logging.INFO, "migrate done", step="migrate", applied=applied)


async def step_seed(dsn: str) -> None:
    from scripts import seed

    users = await _count(dsn, "users")
    if users > 0:
        _log(logging.INFO, "seed skipped: users table is not empty", step="seed", skipped=True, users=users)
        return
    seed.DATABASE_URL = dsn
    await seed.main(if_empty=True)
    _log(logging.INFO, "seed done", step="seed", skipped=False)


async def step_embeddings(dsn: str) -> None:
    from shared.embedding_config import NO_EMBEDDINGS, embedding_provider

    existing = await _count(dsn, "product_embeddings")
    if existing > 0:
        _log(logging.INFO, "embeddings skipped: already present", step="embeddings", skipped=True, rows=existing)
        return
    provider = embedding_provider()
    if provider in ("replay", NO_EMBEDDINGS):
        _log(
            logging.WARNING,
            "embeddings skipped: no real embedding provider configured; semantic search uses its full-text arm",
            step="embeddings",
            skipped=True,
            provider=provider,
        )
        return
    from scripts import generate_embeddings

    generate_embeddings.DATABASE_URL = dsn
    await generate_embeddings.main()
    _log(logging.INFO, "embeddings done", step="embeddings", skipped=False, provider=provider)


async def step_analyze(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("ANALYZE")
    finally:
        await conn.close()
    _log(logging.INFO, "analyze done", step="analyze")


STEPS: tuple[tuple[str, Callable[[str], Awaitable[None]]], ...] = (
    ("migrate", step_migrate),
    ("seed", step_seed),
    ("embeddings", step_embeddings),
    ("analyze", step_analyze),
)


async def run(dsn: str) -> None:
    """Run every step in order; the first exception propagates."""
    for name, step in STEPS:
        _log(logging.INFO, "step starting", step=name)
        await step(dsn)


def main() -> int:
    configure_logging()
    dsn = os.environ.get("DATABASE_URL", DEFAULT_DSN)
    try:
        asyncio.run(run(dsn))
    except (Exception, SystemExit) as exc:  # the job's exit code is the contract
        logger.error("db_init failed: %s", exc, exc_info=not isinstance(exc, SystemExit))
        return 1
    _log(logging.INFO, "db_init complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
