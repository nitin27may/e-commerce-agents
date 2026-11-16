"""D-7 -- the outbox worker, approval SLAs and notifications, against real Postgres.

One tick over back-dated timers performs the reminder and the escalation; two workers never double-process;
a failing handler backs off and parks; redelivery, decided approvals and stale timers are no-ops; a lease that
ran out lets another worker take the row over.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import asyncpg
import pytest

from shared.approvals import repo
from shared.approvals.repo import ApprovalStatus
from worker import approval_handlers
from worker.approval_handlers import HANDLERS
from worker.runner import OutboxRow, backoff_seconds, claim, error_class, process_row, run_once

pytestmark = pytest.mark.integration

REQUESTER = "requester@example.com"
REVIEWER = "reviewer@example.com"
TICK: dict[str, Any] = {
    "batch_size": 20,
    "lease_seconds": 60,
    "max_attempts": 3,
    "backoff_base": 0.0,
    "backoff_cap": 0.0,
}


async def _tick(pool: asyncpg.Pool, handlers: Any = HANDLERS, worker_id: str = "w1", **over: Any):
    return await run_once(pool, handlers, worker_id, **{**TICK, **over})


async def _review(pool: asyncpg.Pool, *, role: str = "support", route: str = "support", **kw: Any) -> dict:
    return await repo.create(
        pool,
        source=kw.pop("source", "tool"),
        kind="return_approval",
        requester_email=REQUESTER,
        status=ApprovalStatus.PENDING_REVIEW,
        route=route,
        assigned_role=role,
        **kw,
    )


async def _due_now(pool: asyncpg.Pool) -> None:
    await pool.execute("UPDATE outbox SET available_at = NOW() - interval '1 minute' WHERE processed_at IS NULL")


async def _outbox(pool: asyncpg.Pool, topic: str | None = None) -> list[asyncpg.Record]:
    if topic:
        return await pool.fetch("SELECT * FROM outbox WHERE topic = $1 ORDER BY id", topic)
    return await pool.fetch("SELECT * FROM outbox ORDER BY id")


async def _actions(pool: asyncpg.Pool, approval_id: str) -> list[str]:
    return [e["action"] for e in await repo.list_events(pool, approval_id)]


async def _enqueue(pool: asyncpg.Pool, topic: str, n: int = 1, **payload: Any) -> None:
    for i in range(n):
        await pool.execute(
            "INSERT INTO outbox (topic, payload, available_at) VALUES ($1, $2::jsonb, NOW() - interval '1 second')",
            topic,
            json.dumps({"i": i, **payload}),
        )


# ── scheduling at the repository layer ────────────────────────────────────────


async def test_entering_review_sets_a_deadline_and_two_timers(clean_db: asyncpg.Pool) -> None:
    row = await _review(clean_db)

    assert row["due_at"] is not None
    timers = await _outbox(clean_db)
    assert [t["topic"] for t in timers] == ["approval.reminder_due", "approval.sla_breached"]
    reminder, breach = timers
    assert reminder["available_at"] < breach["available_at"] == row["due_at"]
    assert str(reminder["aggregate_id"]) == str(row["id"])
    assert json.loads(breach["payload"])["level"] == 0


async def test_transition_into_review_schedules_and_a_rollback_leaves_nothing(clean_db: asyncpg.Pool) -> None:
    row = await repo.create(clean_db, source="tool", kind="return_approval", requester_email=REQUESTER)
    assert await _outbox(clean_db) == []  # pending_confirm is not in review: no clock yet

    async with clean_db.acquire() as conn:
        with pytest.raises(RuntimeError):
            async with conn.transaction():
                await repo.transition(conn, str(row["id"]), [ApprovalStatus.PENDING_CONFIRM], "pending_review")
                raise RuntimeError("the business write failed")
    assert await _outbox(clean_db) == []

    moved = await repo.transition(clean_db, str(row["id"]), [ApprovalStatus.PENDING_CONFIRM], "pending_review")
    assert moved["due_at"] is not None
    assert len(await _outbox(clean_db)) == 2


async def test_unknown_kind_creates_no_timers(clean_db: asyncpg.Pool) -> None:
    row = await repo.create(
        clean_db, source="tool", kind="not_in_policy", requester_email=REQUESTER, status="pending_review"
    )
    assert row["due_at"] is None
    assert await _outbox(clean_db) == []


# ── the SLA handlers ──────────────────────────────────────────────────────────


async def test_one_tick_sends_the_reminder_and_escalates_support_to_manager(clean_db: asyncpg.Pool) -> None:
    row = await _review(clean_db)
    approval_id = str(row["id"])
    await _due_now(clean_db)

    result = await _tick(clean_db)

    assert (result.claimed, result.done, result.failed) == (2, 2, 0)
    after = await repo.get(clean_db, approval_id)
    assert after["status"] == "pending_review"
    assert after["assigned_role"] == "admin" and after["escalation_level"] == 1
    assert after["due_at"] > row["due_at"]
    assert await _actions(clean_db, approval_id) == ["created", "sla_reminder_sent", "escalated"]
    notes = await clean_db.fetch("SELECT kind, role, user_email FROM notifications ORDER BY created_at")
    assert [(n["kind"], n["role"], n["user_email"]) for n in notes] == [
        ("approval.reminder", "support", None),
        ("approval.escalated", "admin", None),
    ]
    # The new level has its own clock.
    fresh = [t for t in await _outbox(clean_db) if t["processed_at"] is None]
    assert [json.loads(t["payload"])["level"] for t in fresh] == [1, 1]


async def test_breach_at_the_top_level_applies_on_timeout_and_tells_the_requester(clean_db: asyncpg.Pool) -> None:
    row = await _review(clean_db, role="admin", route="manager")
    approval_id = str(row["id"])
    await _due_now(clean_db)

    await _tick(clean_db)

    after = await repo.get(clean_db, approval_id)
    assert after["status"] == "expired"  # return_approval: on_timeout: expire
    assert after["decided_by"] is None
    assert after["decision"] == {"decision": "expired", "reason": "sla_timeout"}
    assert "sla_timeout" in await _actions(clean_db, approval_id)
    note = await clean_db.fetchrow("SELECT * FROM notifications WHERE kind = 'approval.timed_out'")
    assert note["user_email"] == REQUESTER and note["role"] is None


async def test_escalated_approval_then_times_out_at_the_second_breach(clean_db: asyncpg.Pool) -> None:
    row = await _review(clean_db)
    await _due_now(clean_db)
    await _tick(clean_db)
    await _due_now(clean_db)  # the manager level's timers

    await _tick(clean_db)

    after = await repo.get(clean_db, str(row["id"]))
    assert after["status"] == "expired" and after["escalation_level"] == 1


async def test_on_timeout_reject_is_honoured(clean_db: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        approval_handlers,
        "get_policy",
        lambda: SimpleNamespace(kind=lambda _k: SimpleNamespace(on_timeout="reject")),
    )
    row = await _review(clean_db, role="admin", route="manager")
    await _due_now(clean_db)

    await _tick(clean_db)

    assert (await repo.get(clean_db, str(row["id"])))["status"] == "rejected"


async def test_a_redelivered_event_does_nothing_the_second_time(clean_db: asyncpg.Pool) -> None:
    row = await _review(clean_db)
    approval_id = str(row["id"])
    await _due_now(clean_db)
    await _tick(clean_db)
    events = await _actions(clean_db, approval_id)
    notes = await clean_db.fetchval("SELECT count(*) FROM notifications")
    timers = len(await _outbox(clean_db))

    # Redeliver both events: same payloads, new rows (so the outbox's own dedupe key does not hide them).
    for t in [t for t in await _outbox(clean_db) if json.loads(t["payload"])["level"] == 0]:
        await clean_db.execute(
            "INSERT INTO outbox (topic, aggregate_id, payload, available_at) VALUES ($1, $2, $3::jsonb, NOW())",
            t["topic"],
            t["aggregate_id"],
            t["payload"],
        )
    result = await _tick(clean_db)

    assert result.done == 2
    assert await _actions(clean_db, approval_id) == events
    assert await clean_db.fetchval("SELECT count(*) FROM notifications") == notes
    assert len(await _outbox(clean_db)) == timers + 2  # and nothing new was scheduled


async def test_the_reminder_alone_is_idempotent(clean_db: asyncpg.Pool) -> None:
    row = await _review(clean_db)
    await _due_now(clean_db)
    reminder = (await _outbox(clean_db, "approval.reminder_due"))[0]
    await _tick(clean_db, handlers={"approval.reminder_due": HANDLERS["approval.reminder_due"]})
    await clean_db.execute("UPDATE outbox SET processed_at = NULL, attempts = 0 WHERE id = $1", reminder["id"])

    await _tick(clean_db)

    assert await clean_db.fetchval("SELECT count(*) FROM notifications WHERE kind = 'approval.reminder'") == 1
    assert (await _actions(clean_db, str(row["id"]))).count("sla_reminder_sent") == 1


@pytest.mark.parametrize("decision", ["approved", "rejected"])
async def test_a_decided_approval_is_left_alone(clean_db: asyncpg.Pool, decision: str) -> None:
    row = await _review(clean_db)
    approval_id = str(row["id"])
    await repo.transition(clean_db, approval_id, [ApprovalStatus.PENDING_REVIEW], decision, decided_by=REVIEWER)
    await _due_now(clean_db)

    result = await _tick(clean_db)

    assert result.done == 2  # consumed, not retried
    after = await repo.get(clean_db, approval_id)
    assert after["status"] == decision and after["escalation_level"] == 0
    assert await clean_db.fetchval("SELECT count(*) FROM notifications") == 0
    assert await _actions(clean_db, approval_id) == ["created", f"status.{decision}"]


async def test_timers_from_an_earlier_trip_through_review_are_stale(clean_db: asyncpg.Pool) -> None:
    row = await _review(clean_db)
    approval_id = str(row["id"])
    old = [t["id"] for t in await _outbox(clean_db)]
    await repo.transition(clean_db, approval_id, [ApprovalStatus.PENDING_REVIEW], "needs_info")
    await asyncio.sleep(0.01)
    back = await repo.transition(clean_db, approval_id, [ApprovalStatus.NEEDS_INFO], "pending_review")
    assert back["due_at"] > row["due_at"]
    await clean_db.execute("UPDATE outbox SET available_at = NOW() - interval '1 minute' WHERE id = ANY($1)", old)

    await _tick(clean_db)

    after = await repo.get(clean_db, approval_id)
    assert after["escalation_level"] == 0 and after["assigned_role"] == "support"
    assert await clean_db.fetchval("SELECT count(*) FROM notifications") == 0


async def test_needs_info_schedules_a_breach_timer_only(clean_db: asyncpg.Pool) -> None:
    row = await _review(clean_db)
    approval_id = str(row["id"])
    before = {t["id"] for t in await _outbox(clean_db)}

    waiting = await repo.transition(clean_db, approval_id, [ApprovalStatus.PENDING_REVIEW], "needs_info")

    added = [t for t in await _outbox(clean_db) if t["id"] not in before]
    assert [t["topic"] for t in added] == ["approval.sla_breached"], (
        "staff are not reminded while the customer owes an answer"
    )
    assert added[0]["available_at"] == waiting["due_at"]


@pytest.mark.parametrize("role,route", [("support", "support"), ("admin", "manager")])
async def test_an_unanswered_needs_info_approval_expires(clean_db: asyncpg.Pool, role: str, route: str) -> None:
    row = await _review(clean_db, role=role, route=route)
    approval_id = str(row["id"])
    await repo.transition(clean_db, approval_id, [ApprovalStatus.PENDING_REVIEW], "needs_info")
    await _due_now(clean_db)

    await _tick(clean_db)

    after = await repo.get(clean_db, approval_id)
    assert after["status"] == "expired", "on_timeout applies; a support-level approval is not escalated instead"
    assert after["escalation_level"] == 0
    assert "sla_timeout" in await _actions(clean_db, approval_id)
    assert await clean_db.fetchval("SELECT count(*) FROM notifications WHERE kind = 'approval.timed_out'") == 1


async def test_an_answered_needs_info_approval_ignores_the_old_breach_timer(clean_db: asyncpg.Pool) -> None:
    row = await _review(clean_db)
    approval_id = str(row["id"])
    await repo.transition(clean_db, approval_id, [ApprovalStatus.PENDING_REVIEW], "needs_info")
    stale = [t["id"] for t in await _outbox(clean_db, "approval.sla_breached")]
    await asyncio.sleep(0.01)
    await repo.transition(clean_db, approval_id, [ApprovalStatus.NEEDS_INFO], "pending_review")
    await clean_db.execute("UPDATE outbox SET available_at = NOW() - interval '1 minute' WHERE id = ANY($1)", stale)

    await _tick(clean_db)

    assert (await repo.get(clean_db, approval_id))["status"] == "pending_review"


async def test_a_timer_does_not_fire_before_it_is_due(clean_db: asyncpg.Pool) -> None:
    await _review(clean_db)
    result = await _tick(clean_db)
    assert result.claimed == 0


# ── the queue mechanics ───────────────────────────────────────────────────────


async def test_two_workers_process_every_row_exactly_once(clean_db: asyncpg.Pool) -> None:
    await _enqueue(clean_db, "test.count", n=40)
    seen: list[int] = []

    async def handler(conn: asyncpg.Connection, row: OutboxRow) -> None:
        seen.append(row.id)
        await asyncio.sleep(0.005)

    async def worker(name: str) -> int:
        total = 0
        while (r := await _tick(clean_db, {"test.count": handler}, worker_id=name, batch_size=5)).claimed:
            total += r.done
        return total

    a, b = await asyncio.gather(worker("w-a"), worker("w-b"))

    assert a + b == 40
    assert sorted(seen) == sorted(set(seen)) and len(seen) == 40
    assert await clean_db.fetchval("SELECT count(*) FROM outbox WHERE processed_at IS NULL") == 0


async def test_a_failing_handler_backs_off_and_then_parks(clean_db: asyncpg.Pool) -> None:
    await _enqueue(clean_db, "test.boom", secret="card-4111-1111")
    calls = 0

    async def boom(conn: asyncpg.Connection, row: OutboxRow) -> None:
        nonlocal calls
        calls += 1
        raise ValueError(f"cannot process {row.payload['secret']}")

    over = {"backoff_base": 60.0, "backoff_cap": 120.0}
    first = await _tick(clean_db, {"test.boom": boom}, **over)
    row = (await _outbox(clean_db))[0]
    assert (first.failed, first.parked) == (1, 0)
    assert row["attempts"] == 1 and row["failed_at"] is None and row["processed_at"] is None
    assert row["last_error"] == "handler_error"  # the class, never the message or the payload
    assert row["locked_by"] is None and row["locked_until"] is None
    assert (await clean_db.fetchval("SELECT available_at > NOW() + interval '50 seconds' FROM outbox")) is True
    assert (await _tick(clean_db, {"test.boom": boom}, **over)).claimed == 0  # backing off

    await clean_db.execute("UPDATE outbox SET available_at = NOW()")
    await _tick(clean_db, {"test.boom": boom}, **over)
    assert (await clean_db.fetchval("SELECT available_at > NOW() + interval '100 seconds' FROM outbox")) is True  # 2x
    await clean_db.execute("UPDATE outbox SET available_at = NOW()")
    last = await _tick(clean_db, {"test.boom": boom}, **over)

    parked = (await _outbox(clean_db))[0]
    assert (last.failed, last.parked) == (0, 1) and calls == 3
    assert parked["failed_at"] is not None and parked["attempts"] == 3
    assert "4111" not in (parked["last_error"] or "")
    await clean_db.execute("UPDATE outbox SET available_at = NOW() - interval '1 hour'")
    assert (await _tick(clean_db, {"test.boom": boom})).claimed == 0 and calls == 3  # parked rows stay parked


async def test_a_failed_handler_rolls_back_its_writes(clean_db: asyncpg.Pool) -> None:
    await _enqueue(clean_db, "test.half")

    async def half(conn: asyncpg.Connection, row: OutboxRow) -> None:
        await conn.execute("INSERT INTO notifications (user_email, kind, title) VALUES ('a@b.co', 'k', 't')")
        raise RuntimeError("after the write")

    await _tick(clean_db, {"test.half": half})

    assert await clean_db.fetchval("SELECT count(*) FROM notifications") == 0


async def test_an_unknown_topic_fails_with_a_fixed_class(clean_db: asyncpg.Pool) -> None:
    await _enqueue(clean_db, "nobody.handles_this")

    result = await _tick(clean_db, {})

    assert result.failed == 1
    assert (await _outbox(clean_db))[0]["last_error"] == "unknown_topic"


async def test_when_the_lease_runs_out_another_worker_takes_over(clean_db: asyncpg.Pool) -> None:
    await _enqueue(clean_db, "test.once")
    handled: list[str] = []

    async def handler(conn: asyncpg.Connection, row: OutboxRow) -> None:
        handled.append("ran")

    kw = {"batch_size": 5, "lease_seconds": 60, "max_attempts": 3}
    (stale,) = await claim(clean_db, "crashed-worker", **kw)
    assert await claim(clean_db, "w2", **kw) == []  # leased: nobody else may take it
    await clean_db.execute("UPDATE outbox SET locked_until = NOW() - interval '1 second'")

    result = await _tick(clean_db, {"test.once": handler}, worker_id="w2")
    assert (result.claimed, result.done) == (1, 1)

    # The first worker wakes up late holding its old claim: it no longer owns the row, so it does nothing.
    outcome = await process_row(
        clean_db,
        stale,
        {"test.once": handler},
        "crashed-worker",
        lease_seconds=60,
        max_attempts=3,
        backoff_base=0,
        backoff_cap=0,
    )
    assert outcome == "skipped" and handled == ["ran"]
    assert (await _outbox(clean_db))[0]["attempts"] == 2  # claimed twice, handled once


async def test_a_row_that_keeps_crashing_its_worker_is_eventually_parked(clean_db: asyncpg.Pool) -> None:
    await _enqueue(clean_db, "test.poison")
    for n in range(3):  # three claims, each followed by a "crash": no outcome recorded, lease lapses
        assert len(await claim(clean_db, f"w{n}", batch_size=5, lease_seconds=60, max_attempts=3)) == 1
        await clean_db.execute("UPDATE outbox SET locked_until = NOW() - interval '1 second'")

    assert await claim(clean_db, "w9", batch_size=5, lease_seconds=60, max_attempts=3) == []
    result = await _tick(clean_db, {})

    row = (await _outbox(clean_db))[0]
    assert result.parked == 1 and row["failed_at"] is not None and row["last_error"] == "lease_expired"


async def test_stop_hands_unstarted_rows_back(clean_db: asyncpg.Pool) -> None:
    await _enqueue(clean_db, "test.x", n=3)
    stop = asyncio.Event()
    stop.set()

    async def handler(conn: asyncpg.Connection, row: OutboxRow) -> None:
        raise AssertionError("must not run after stop")

    result = await _tick(clean_db, {"test.x": handler}, stop=stop)

    assert result.claimed == 3 and result.done == 0
    rows = await _outbox(clean_db)
    assert all(r["locked_by"] is None and r["attempts"] == 0 for r in rows)


def test_backoff_doubles_and_is_capped() -> None:
    assert [backoff_seconds(n, 5, 30) for n in (1, 2, 3, 4, 5)] == [5, 10, 20, 30, 30]


def test_error_classes_are_fixed_labels() -> None:
    assert error_class(ValueError("card 4111")) == "handler_error"
    assert error_class(TimeoutError("x")) == "timeout"
    assert error_class(asyncpg.PostgresError("secret")) == "database_error"


def test_the_worker_serves_the_approval_topics() -> None:
    assert set(HANDLERS) == {"approval.reminder_due", "approval.sla_breached", "approval.timeout_apply"}
