"""``GET /ready`` — readiness, as distinct from ``/health`` liveness (#102).

``/health`` stays static: it answers "is the process up", which is what a
restart policy wants. ``/ready`` answers "can this service do its job right
now", which is what ``depends_on: condition: service_healthy`` and a load
balancer want:

* ``database``   — ``SELECT 1`` over a short-lived private connection, 2 s timeout. Critical.
* ``migrations`` — ``schema_migrations`` exists and has at least one row (so the
  schema was applied by ``scripts/migrate.py``, not left half-initialised). Critical.
* ``redis``      — ``PING``, 2 s timeout. Reported but **not** critical: rate
  limiting fails open, so an unreachable Redis must not take the stack down.

The body is ``{"status", "service", "checks": {name: {"ok", "critical", "detail"}}}``
with ``status`` of ``ready`` (HTTP 200) or ``not_ready`` (HTTP 503). The .NET
stack (``ECommerceAgents.Shared/Health/ReadinessEndpoint.cs``) returns the same shape.

``detail`` carries an exception *class name*, never its message — a connection
error's text can include the DSN.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import asyncpg
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from shared.config import settings
from shared.rate_limit import get_redis_client

logger = logging.getLogger(__name__)

CHECK_TIMEOUT_S = 2.0


def _result(ok: bool, *, critical: bool, detail: str) -> dict[str, Any]:
    return {"ok": ok, "critical": critical, "detail": detail}


async def _probe_database(timeout: float) -> tuple[dict[str, Any], dict[str, Any]]:
    """The ``database`` and ``migrations`` checks, over one short-lived connection.

    Deliberately *not* the service's pool. A probe that borrows a pooled connection
    from a database that stopped answering (a paused container, a dropped network)
    cannot be abandoned cleanly: asyncpg's query cancellation opens a second
    connection to the same dead server and waits for it, so ``wait_for`` itself
    hung for minutes -- found by pausing the Postgres container under a running
    specialist. A private connection is torn down with ``terminate()``, which just
    closes the socket, so the probe really does answer within the timeout.
    """
    conn: asyncpg.Connection | None = None

    async def run() -> tuple[dict[str, Any], dict[str, Any]]:
        nonlocal conn
        conn = await asyncpg.connect(settings.DATABASE_URL, timeout=timeout)
        await conn.fetchval("SELECT 1")
        database = _result(True, critical=True, detail="ok")
        try:
            row = await conn.fetchrow("SELECT count(*) AS n, max(version) AS latest FROM schema_migrations")
        except Exception as exc:
            # UndefinedTableError: scripts/migrate.py has not run against this database.
            return database, _result(False, critical=True, detail=type(exc).__name__)
        if not row or row["n"] == 0:
            return database, _result(False, critical=True, detail="schema_migrations is empty")
        return database, _result(True, critical=True, detail=f"{row['n']} applied, latest {row['latest']}")

    task = asyncio.ensure_future(run())
    try:
        done, _pending = await asyncio.wait({task}, timeout=timeout)
        if not done:
            if conn is not None:
                conn.terminate()
            task.cancel()
            await asyncio.wait({task}, timeout=1.0)
            slow = _result(False, critical=True, detail=f"timed out after {timeout:g}s")
            return slow, dict(slow)
        try:
            return task.result()
        except Exception as exc:
            failed = _result(False, critical=True, detail=type(exc).__name__)
            return failed, dict(failed)
    finally:
        if conn is not None:
            conn.terminate()


async def _check_redis(timeout: float) -> dict[str, Any]:
    try:
        await asyncio.wait_for(get_redis_client().ping(), timeout)
    except TimeoutError:
        return _result(False, critical=False, detail=f"timed out after {timeout:g}s")
    except Exception as exc:
        return _result(False, critical=False, detail=type(exc).__name__)
    return _result(True, critical=False, detail="ok")


async def readiness(service: str, timeout: float = CHECK_TIMEOUT_S) -> tuple[int, dict[str, Any]]:
    """Run every check concurrently and return ``(http_status, body)``."""
    (database, migrations), redis_check = await asyncio.gather(_probe_database(timeout), _check_redis(timeout))
    checks = {"database": database, "migrations": migrations, "redis": redis_check}
    ready = all(c["ok"] for c in checks.values() if c["critical"])
    if not ready:
        logger.warning(
            "readiness.not_ready service=%s failing=%s",
            service,
            [name for name, c in checks.items() if c["critical"] and not c["ok"]],
        )
    body = {"status": "ready" if ready else "not_ready", "service": service, "checks": checks}
    return (200 if ready else 503), body


def register_readiness(app: FastAPI, service: str) -> None:
    """Mount ``GET /ready`` on ``app``."""

    @app.get("/ready", include_in_schema=False)
    async def ready() -> JSONResponse:
        status_code, body = await readiness(service)
        return JSONResponse(body, status_code=status_code)
