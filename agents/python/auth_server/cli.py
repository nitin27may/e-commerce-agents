"""Rotate the auth-server's RS256 signing key (AUTH-KR).

Inserts a new active key and demotes the current one, which stays published in the JWKS for
``AUTH_KEY_RETIRE_AFTER_SECONDS`` (default: access-token TTL + 300 s skew) so tokens it already signed keep
validating. A running auth-server switches to the new key within ``KEY_MAINTENANCE_INTERVAL_SECONDS`` (30 s);
validators fetch it on their first unknown ``kid`` (rate-limited), so nothing restarts.

Only meaningful under AUTH_MODE=oauth (the auth-server is not part of local mode). Runbook:
docs/security-guide.md.

Usage::

    uv run --project agents/python python -m scripts.rotate_signing_key             # from the repo root
    docker compose exec auth-server uv run --no-project python -m auth_server.cli   # inside the image
    ... --retire-after 7200      # seconds the old key stays published, this rotation only
    ... --sweep-only             # retire keys past their deadline, rotate nothing

Needs ``DATABASE_URL`` and, when the key is encrypted at rest, ``AUTH_SIGNING_KEY_ENCRYPTION_KEY`` — the same
values the auth-server runs with. ``scripts/rotate_signing_key.py`` is a thin entry point to this module (the
auth-server image carries ``auth_server/`` but not the repo's ``scripts/``).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from collections.abc import Sequence

import asyncpg

from auth_server import keys
from shared.config import settings


async def run(database_url: str, *, retire_after: int | None = None, sweep_only: bool = False) -> int:
    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=2)
    try:
        for kid in await keys.retire_expired(pool):
            print(f"retired kid={kid}")
        if sweep_only:
            return 0

        # Bootstrap first so a rotation on an empty table still leaves exactly one active key.
        await keys.ensure_active_key(pool)
        result = await keys.rotate(pool, retire_after=retire_after)
        print(f"new active kid={result.new_kid}")
        if result.old_kid:
            print(f"previous kid={result.old_kid} stays in the JWKS until {result.old_key_not_after}")
        return 0
    finally:
        await pool.close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--retire-after", type=int, default=None, help="seconds the old key stays published")
    parser.add_argument("--sweep-only", action="store_true", help="only retire keys past their deadline")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    try:
        return asyncio.run(run(settings.DATABASE_URL, retire_after=args.retire_after, sweep_only=args.sweep_only))
    except (asyncpg.PostgresError, OSError, ValueError) as exc:
        print(f"rotation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
