"""
E-Commerce Agents — schema migrations.

``docker/postgres/init.sql`` is frozen as the *baseline* (version ``0001_baseline``).
Every later schema change is a numbered file in ``docker/postgres/migrations/``
(``NNNN_description.sql``) and is applied here, in order, exactly once.

Behaviour:

* **Empty database** (no ``products`` table): apply the baseline, then every
  migration.
* **Existing volume** (``products`` exists but ``schema_migrations`` has no
  baseline row — the volume was created by Postgres' ``initdb.d`` mount of
  ``init.sql``): first check that it really holds the *current* baseline (see
  ``BASELINE_MARKERS``); if so, record the baseline as applied *without
  re-running it* (``CREATE TABLE products`` has no ``IF NOT EXISTS``) and apply
  migrations. A volume from before the current ``init.sql`` is refused, with
  nothing recorded: claiming it is at baseline would leave it with a different
  schema from a fresh database. Recreate it with ``./scripts/dev.sh --clean``.
* **Idempotent**: a second run applies nothing.
* **One transaction per migration**, so a failing file leaves no half-applied
  change and no ``schema_migrations`` row.
* **Advisory lock** around the whole run, so two services starting together
  cannot both apply the same migration.
* **Checksum guard**: an already-applied file whose content changed is an
  error, not a silent divergence. Fix forward with a new migration.

Usage::

    uv run python -m scripts.migrate           # from the repo root / seeder
    DATABASE_URL=postgresql://... python scripts/migrate.py

Only ``asyncpg`` is required, so it runs anywhere the seeder does.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import asyncpg

logger = logging.getLogger("migrate")

REPO_ROOT = Path(__file__).resolve().parents[1]
BASELINE_VERSION = "0001_baseline"
# A fixed 64-bit key; any constant works as long as every caller uses the same one.
ADVISORY_LOCK_KEY = 0x45434D4947524154  # "ECMIGRAT"
_MIGRATION_NAME = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")

# Objects that exist only in the current baseline (``docker/postgres/init.sql``), the newest
# additions to it. An existing volume is recorded as "at baseline" only if every one is present;
# a volume created from an older init.sql lacks some and would otherwise be certified wrongly.
# (table, column) pairs name a column; (table, None) a whole table. Keep in step with init.sql:
# tests/test_migrate.py checks each marker is still declared there.
BASELINE_MARKERS: tuple[tuple[str, str | None], ...] = (
    ("products", "search_vector"),
    ("idempotency_keys", None),
    ("agent_memories", None),
    ("workflow_checkpoints", None),
    ("hitl_requests", None),
    ("oauth_clients", None),
    ("oauth_signing_keys", None),
    ("oauth_tokens", None),
)


class MigrationError(RuntimeError):
    """A migration could not be applied or the recorded history disagrees with the files."""


def checksum_of(raw: bytes) -> str:
    """SHA-256 of a migration file with CRLF folded to LF.

    The same volume must verify from a Windows checkout (CRLF, if ``core.autocrlf`` rewrote it)
    and from a Linux / WSL one (LF), so line endings are not part of a file's identity. Every
    checksum recorded so far was computed over LF files, so existing rows stay valid.
    ``.gitattributes`` also pins ``*.sql`` to LF.
    """
    return hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()


@dataclass(frozen=True)
class Migration:
    version: str
    path: Path

    @property
    def sql(self) -> str:
        return self.path.read_text(encoding="utf-8")

    @property
    def checksum(self) -> str:
        return checksum_of(self.path.read_bytes())


async def missing_baseline_markers(conn: asyncpg.Connection) -> list[str]:
    """Names of ``BASELINE_MARKERS`` the connected database lacks (empty = it matches the baseline)."""
    missing: list[str] = []
    for table, column in BASELINE_MARKERS:
        if column is None:
            present = await conn.fetchval("SELECT to_regclass($1)", f"public.{table}")
        else:
            present = await conn.fetchval(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = $1 AND column_name = $2",
                table,
                column,
            )
        if not present:
            missing.append(table if column is None else f"{table}.{column}")
    return missing


def default_postgres_dir() -> Path:
    """``docker/postgres`` — overridable because the seeder image mounts it elsewhere."""
    return Path(os.environ.get("POSTGRES_SCHEMA_DIR", REPO_ROOT / "docker" / "postgres"))


def discover(postgres_dir: Path) -> list[Migration]:
    """The baseline followed by ``migrations/NNNN_*.sql`` in version order."""
    baseline = postgres_dir / "init.sql"
    if not baseline.is_file():
        raise MigrationError(f"baseline not found: {baseline}")
    found = [Migration(BASELINE_VERSION, baseline)]
    migrations_dir = postgres_dir / "migrations"
    files = sorted(migrations_dir.glob("*.sql")) if migrations_dir.is_dir() else []
    seen: set[str] = set()
    for path in files:
        match = _MIGRATION_NAME.match(path.name)
        if not match:
            raise MigrationError(f"bad migration filename {path.name!r}: expected NNNN_lower_snake.sql")
        if match.group(1) in seen:
            raise MigrationError(f"two migrations share the number {match.group(1)}")
        seen.add(match.group(1))
        found.append(Migration(path.stem, path))
    return found


async def migrate(dsn: str, postgres_dir: Path | None = None) -> list[str]:
    """Bring the database up to date. Returns the versions applied by *this* call.

    A version merely recorded (the baseline on an existing volume) is not
    "applied" and is not returned.
    """
    migrations = discover(postgres_dir or default_postgres_dir())
    applied_now: list[str] = []
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("SELECT pg_advisory_lock($1)", ADVISORY_LOCK_KEY)
        try:
            await conn.execute(
                """CREATE TABLE IF NOT EXISTS schema_migrations (
                       version    TEXT PRIMARY KEY,
                       checksum   TEXT NOT NULL,
                       applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                   )"""
            )
            rows = await conn.fetch("SELECT version, checksum FROM schema_migrations")
            recorded = {r["version"]: r["checksum"] for r in rows}

            for version in sorted(recorded.keys() - {m.version for m in migrations}):
                logger.warning("schema_migrations has %s but no such file exists", version)

            for m in migrations:
                if m.version in recorded and recorded[m.version] != m.checksum:
                    raise MigrationError(
                        f"{m.version} was modified after it was applied "
                        f"(recorded {recorded[m.version][:12]}, file {m.checksum[:12]}). "
                        "Never edit an applied migration; add a new one."
                    )

            for m in migrations:
                if m.version in recorded:
                    continue
                if m.version == BASELINE_VERSION and await conn.fetchval("SELECT to_regclass('public.products')"):
                    missing = await missing_baseline_markers(conn)
                    if missing:
                        raise MigrationError(
                            "this database predates the current baseline schema (missing: "
                            + ", ".join(missing)
                            + "), so it cannot be recorded as being at baseline. Recreate it with "
                            "`./scripts/dev.sh --clean` (this discards its data), or apply the missing "
                            "parts of docker/postgres/init.sql by hand and run the migration again."
                        )
                    await conn.execute(
                        "INSERT INTO schema_migrations (version, checksum) VALUES ($1, $2)", m.version, m.checksum
                    )
                    logger.info("%s: existing schema detected, recorded without re-running", m.version)
                    continue
                try:
                    async with conn.transaction():
                        await conn.execute(m.sql)
                        await conn.execute(
                            "INSERT INTO schema_migrations (version, checksum) VALUES ($1, $2)", m.version, m.checksum
                        )
                except asyncpg.PostgresError as exc:
                    raise MigrationError(f"{m.version} failed and was rolled back: {exc}") from exc
                applied_now.append(m.version)
                logger.info("%s: applied", m.version)
        finally:
            await conn.execute("SELECT pg_advisory_unlock($1)", ADVISORY_LOCK_KEY)
    finally:
        await conn.close()
    if not applied_now:
        logger.info("schema is up to date")
    return applied_now


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    dsn = os.environ.get("DATABASE_URL", "postgresql://ecommerce:ecommerce_secret@localhost:5432/ecommerce_agents")
    try:
        asyncio.run(migrate(dsn))
    except MigrationError as exc:
        logger.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
