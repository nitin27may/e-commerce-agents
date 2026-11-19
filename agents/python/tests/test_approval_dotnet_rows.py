"""D-5n -- the Python worker against approval rows the .NET stack wrote.

One Python worker (``worker/``) drains the outbox for both stacks, so what the .NET repository writes when an
approval enters review has to be exactly what the Python handlers read. ``tests/data/dotnet_approval_rows.json``
is a dump, straight out of Postgres, of the rows .NET writes for a gated tool call: the ``approvals`` row, its
audit events and its two SLA timers. It is produced by ``ApprovalCrossStackTests`` in the .NET suite (which also
fails if the .NET write path stops matching it), and this test loads it into a Python database and runs the REAL
handlers over it:

* the reminder timer notifies the assigned role and is audited once;
* the breach timer escalates a support review to a manager, which only happens if the payload's ``level`` and
  ``due_at`` match the approval row exactly (the handlers compare them as instants, so a different timestamp
  format would make every timer look stale and be dropped silently);
* the escalation Python then writes is itself a normal level-1 pair of timers.

Regenerate the fixture by running the .NET test ``ApprovalCrossStackTests`` with ``APPROVAL_DUMP_PATH`` set to
``agents/python/tests/data/dotnet_approval_rows.json``.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import asyncpg
import pytest

from shared.approvals import repo
from shared.approvals.repo import ApprovalStatus
from worker.approval_handlers import HANDLERS
from worker.runner import run_once

pytestmark = pytest.mark.integration

FIXTURE = Path(__file__).parent / "data" / "dotnet_approval_rows.json"
TICK: dict[str, Any] = {
    "batch_size": 20,
    "lease_seconds": 60,
    "max_attempts": 3,
    "backoff_base": 0.0,
    "backoff_cap": 0.0,
}


async def _load_dotnet_rows(pool: asyncpg.Pool) -> dict[str, Any]:
    """Insert the .NET-written rows verbatim (ids and all) through the table's own row type."""
    dump = json.loads(FIXTURE.read_text())
    await pool.execute(
        "INSERT INTO approvals SELECT * FROM jsonb_populate_record(NULL::approvals, $1::jsonb)",
        json.dumps(dump["approval"]),
    )
    for event in dump["events"]:
        await pool.execute(
            "INSERT INTO approval_events SELECT * FROM jsonb_populate_record(NULL::approval_events, $1::jsonb)",
            json.dumps(event),
        )
    for row in dump["outbox"]:
        await pool.execute(
            "INSERT INTO outbox SELECT * FROM jsonb_populate_record(NULL::outbox, $1::jsonb)", json.dumps(row)
        )
    # The rows carry their ids; move the sequences past them so Python's own inserts do not collide.
    for table in ("approval_events", "outbox"):
        await pool.execute(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), (SELECT max(id) FROM {table}))")  # noqa: S608
    return dump


async def _due(pool: asyncpg.Pool, topic: str) -> None:
    await pool.execute(
        "UPDATE outbox SET available_at = NOW() - interval '1 minute' WHERE topic = $1 AND processed_at IS NULL", topic
    )


async def _not_due(pool: asyncpg.Pool, topic: str) -> None:
    """The dump's timestamps are absolute, so its later timers fall into the past as the calendar moves on."""
    await pool.execute(
        "UPDATE outbox SET available_at = NOW() + interval '1 hour' WHERE topic = $1 AND processed_at IS NULL", topic
    )


async def test_the_fixture_is_what_the_dotnet_stack_writes(clean_db: asyncpg.Pool) -> None:
    dump = await _load_dotnet_rows(clean_db)
    approval = await repo.get(clean_db, dump["approval"]["id"])

    assert approval is not None
    assert approval["status"] == ApprovalStatus.PENDING_REVIEW.value
    assert approval["assigned_role"] == "support" and approval["escalation_level"] == 0
    assert approval["due_at"] is not None and approval["due_at"].tzinfo is not None
    # Python reads the JSON columns .NET wrote as ordinary decoded objects.
    assert approval["recommendation"]["route"] == "support"
    assert [e["action"] for e in await repo.list_events(clean_db, dump["approval"]["id"])] == ["created", "routed"]
    for timer in dump["outbox"]:
        # The worker compares this as an instant with the approval's due_at; it must parse and be equal.
        assert datetime.fromisoformat(timer["payload"]["due_at"]) == approval["due_at"]
        assert timer["payload"]["level"] == approval["escalation_level"]
        assert timer["payload"]["approval_id"] == str(approval["id"])


async def test_python_worker_sends_the_reminder_dotnet_scheduled(clean_db: asyncpg.Pool) -> None:
    dump = await _load_dotnet_rows(clean_db)
    approval_id = dump["approval"]["id"]
    await _due(clean_db, "approval.reminder_due")
    # Only the reminder is under test; the breach timer in the dump is a fixed 2026-11-16 instant, long past by now.
    await _not_due(clean_db, "approval.sla_breached")

    result = await run_once(clean_db, HANDLERS, "w1", **TICK)

    assert (result.claimed, result.done, result.failed) == (1, 1, 0)
    assert "sla_reminder_sent" in [e["action"] for e in await repo.list_events(clean_db, approval_id)]
    notes = await clean_db.fetch("SELECT kind, role, user_email FROM notifications")
    assert [(n["kind"], n["role"], n["user_email"]) for n in notes] == [("approval.reminder", "support", None)]
    processed = await clean_db.fetchval(
        "SELECT processed_at FROM outbox WHERE topic = 'approval.reminder_due' AND aggregate_id = $1::uuid", approval_id
    )
    assert processed is not None


async def test_python_worker_escalates_the_review_dotnet_scheduled(clean_db: asyncpg.Pool) -> None:
    dump = await _load_dotnet_rows(clean_db)
    approval_id = dump["approval"]["id"]
    before = await repo.get(clean_db, approval_id)
    await _due(clean_db, "approval.reminder_due")
    await _due(clean_db, "approval.sla_breached")

    result = await run_once(clean_db, HANDLERS, "w1", **TICK)

    # Not dropped as stale: that is what a mismatched level or due_at would look like.
    assert (result.claimed, result.done, result.failed) == (2, 2, 0)
    after = await repo.get(clean_db, approval_id)
    assert after["status"] == "pending_review"
    assert after["assigned_role"] == "admin" and after["escalation_level"] == 1
    assert after["due_at"] > before["due_at"]
    assert [e["action"] for e in await repo.list_events(clean_db, approval_id)] == [
        "created",
        "routed",
        "sla_reminder_sent",
        "escalated",
    ]
    # The new level has its own clock, written by Python in the same shape .NET writes.
    fresh = await clean_db.fetch("SELECT topic, payload FROM outbox WHERE processed_at IS NULL ORDER BY id")
    assert [t["topic"] for t in fresh] == ["approval.reminder_due", "approval.sla_breached"]
    assert [json.loads(t["payload"])["level"] for t in fresh] == [1, 1]


async def test_a_dotnet_approval_is_decidable_by_the_python_repository(clean_db: asyncpg.Pool) -> None:
    """The row is an ordinary approval to Python: the guarded transition and the self-approval rule apply to it."""
    dump = await _load_dotnet_rows(clean_db)
    approval_id = dump["approval"]["id"]
    requester = dump["approval"]["requester_email"]

    with pytest.raises(repo.SelfApprovalError):
        await repo.transition(
            clean_db,
            approval_id,
            [ApprovalStatus.PENDING_REVIEW],
            ApprovalStatus.APPROVED,
            decided_by=requester.upper(),
        )
    decided = await repo.transition(
        clean_db,
        approval_id,
        [ApprovalStatus.PENDING_REVIEW],
        ApprovalStatus.REJECTED,
        decided_by="reviewer@example.com",
        action="decided",
    )
    assert decided["status"] == "rejected"

    # Its timers are now stale and the worker drops them without acting.
    await _due(clean_db, "approval.reminder_due")
    await _due(clean_db, "approval.sla_breached")
    result = await run_once(clean_db, HANDLERS, "w1", **TICK)
    assert (result.claimed, result.failed) == (2, 0)
    assert (await repo.get(clean_db, approval_id))["status"] == "rejected"
