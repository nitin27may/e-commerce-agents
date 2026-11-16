"""D-8 -- migration 0010 expires return approvals paused under the pre-policy workflow.

A checkpoint written by the old graph cannot be resumed by the new one, so every still-open ``return_approval``
becomes ``expired`` (and its linked inbox approval with an audit event). Decided rows and other kinds are untouched.
"""

from __future__ import annotations

import json
import shutil
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio

from tests.conftest import POSTGRES_DIR, _load_migrate

pytestmark = pytest.mark.integration

migrate_mod = _load_migrate()
VERSION = "0010_expire_pre_policy_return_approvals"


@pytest_asyncio.fixture
async def throwaway_url(database_url: str) -> AsyncGenerator[str, None]:
    name = f"mig_{uuid.uuid4().hex[:12]}"
    admin = await asyncpg.connect(database_url)
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
    finally:
        await admin.close()
    yield database_url.rsplit("/", 1)[0] + f"/{name}"
    admin = await asyncpg.connect(database_url)
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    finally:
        await admin.close()


def test_the_migration_file_exists() -> None:
    assert VERSION in [m.version for m in migrate_mod.discover(POSTGRES_DIR)]


async def test_open_return_approvals_are_expired_and_everything_else_is_left_alone(
    throwaway_url: str, tmp_path: Path
) -> None:
    schema = tmp_path / "postgres"
    shutil.copytree(POSTGRES_DIR, schema)
    held_back: dict[str, Path] = {}
    for path in sorted((schema / "migrations").glob("*.sql")):
        if path.name >= f"{VERSION}.sql":
            held_back[path.name] = tmp_path / path.name
            path.rename(held_back[path.name])
    assert VERSION not in await migrate_mod.migrate(throwaway_url, schema)

    conn = await asyncpg.connect(throwaway_url)
    try:
        run_id = await conn.fetchval("INSERT INTO usage_logs (agent_name) VALUES ('orchestrator') RETURNING id")

        async def _hitl(kind: str, status: str, *, with_approval: str | None = None) -> uuid.UUID:
            approval_id = None
            if with_approval:
                approval_id = await conn.fetchval(
                    """INSERT INTO approvals (source, kind, requester_email, status, route, assigned_role)
                       VALUES ('workflow', 'return_approval', 'c@example.com', $1, 'support', 'support')
                       RETURNING id""",
                    with_approval,
                )
                await conn.execute(
                    "INSERT INTO approval_events (approval_id, action) VALUES ($1, 'created')", approval_id
                )
            hitl_id = await conn.fetchval(
                """INSERT INTO hitl_requests
                       (workflow_run_id, request_id, user_email, kind, payload, status, approval_id)
                   VALUES ($1, 'r', 'c@example.com', $2, '{}'::jsonb, $3, $4) RETURNING id""",
                run_id,
                kind,
                status,
                approval_id,
            )
            if approval_id:
                await conn.execute("UPDATE approvals SET source_ref = $2 WHERE id = $1", approval_id, hitl_id)
            return hitl_id

        pending = await _hitl("return_approval", "pending", with_approval="pending_review")
        processing = await _hitl("return_approval", "processing")
        unlinked = await _hitl("return_approval", "pending")
        approved = await _hitl("return_approval", "approved")
        rejected = await _hitl("return_approval", "rejected")
        other_kind = await _hitl("tool_approval", "pending")
    finally:
        await conn.close()

    for name, aside in held_back.items():
        shutil.copy(aside, schema / "migrations" / name)
    assert (await migrate_mod.migrate(throwaway_url, schema))[-1] == VERSION

    conn = await asyncpg.connect(throwaway_url)
    try:
        status = {r["id"]: r["status"] for r in await conn.fetch("SELECT id, status FROM hitl_requests")}
        assert status[pending] == status[processing] == status[unlinked] == "expired"
        assert status[approved] == "approved" and status[rejected] == "rejected"
        assert status[other_kind] == "pending", "only return approvals are affected"

        response = await conn.fetchval("SELECT response FROM hitl_requests WHERE id = $1", pending)
        assert json.loads(response)["approved"] is False

        approval = await conn.fetchrow("SELECT status FROM approvals WHERE source_ref = $1", pending)
        assert approval["status"] == "expired"
        events = [
            r["action"]
            for r in await conn.fetch(
                """SELECT action FROM approval_events
                    WHERE approval_id = (SELECT id FROM approvals WHERE source_ref = $1) ORDER BY id""",
                pending,
            )
        ]
        assert events == ["created", "expired"]
    finally:
        await conn.close()

    # Idempotent: the runner never applies it twice, and re-running the statements matches nothing new.
    assert await migrate_mod.migrate(throwaway_url, schema) == []
