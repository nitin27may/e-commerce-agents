"""M-3/M-4 (#93) -- the worker and merchant proposals: SLA reminder and expiry, the daily run, an interrupted decide."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import asyncpg
import pytest

from orchestrator import proposals_service as svc
from orchestrator.approvals_service import Actor, ApprovalHTTPError
from shared.approvals import repo
from shared.config import settings
from shared.merchant import execute as merchant_execute
from shared.merchant.proposals import generate_proposal
from tests import merchant_support as ms
from worker import approval_handlers, merchant_handlers
from worker.approval_handlers import HANDLERS as APPROVAL_HANDLERS
from worker.main import HANDLERS
from worker.merchant_handlers import TOPIC_DAILY_RUN, next_run_at
from worker.runner import run_once

D = Decimal
TICK: dict[str, Any] = {
    "batch_size": 20,
    "lease_seconds": 60,
    "max_attempts": 3,
    "backoff_base": 0.0,
    "backoff_cap": 0.0,
}


@pytest.fixture
async def w(clean_db: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch) -> ms.World:
    monkeypatch.setattr("shared.db._pool", clean_db, raising=False)
    return await ms.world(clean_db)


async def _tick(pool: asyncpg.Pool):
    return await run_once(pool, HANDLERS, "w1", **TICK)


async def _due_now(pool: asyncpg.Pool, topic: str | None = None) -> None:
    await pool.execute(
        "UPDATE outbox SET available_at = NOW() - interval '1 minute' "
        "WHERE processed_at IS NULL AND ($1::text IS NULL OR topic = $1)",
        topic,
    )


def test_the_worker_serves_the_approval_topics_and_the_daily_run() -> None:
    assert set(APPROVAL_HANDLERS) <= set(HANDLERS) and TOPIC_DAILY_RUN in HANDLERS


# ── SLA: reminder and expiry ─────────────────────────────────────────────────


async def test_the_reminder_goes_to_the_seller_and_the_unanswered_batch_expires_with_nothing_executed(
    w: ms.World,
) -> None:
    price = await ms.product(w, "Overstock", price="100.00", stock=(300, 300), sold=20)
    result = await generate_proposal(w.pool, w.seller_id)
    await w.pool.execute("DELETE FROM notifications")

    await _due_now(w.pool)
    await _tick(w.pool)  # the reminder and the breach are both due: reminder first, then the expiry

    approval = await repo.get(w.pool, result.approval_id)
    assert approval["status"] == "expired"
    assert (
        await w.pool.fetchval("SELECT status FROM proposals WHERE id = $1", uuid.UUID(result.proposal_id))
        == "cancelled"
    )
    kinds = {r["kind"]: r["user_email"] for r in await w.pool.fetch("SELECT kind, user_email FROM notifications")}
    assert kinds["merchant.proposal_reminder"] == ms.SELLER and kinds["merchant.proposal_expired"] == ms.SELLER
    assert "approval.timed_out" not in kinds  # the system actor is never notified
    assert await w.pool.fetchval("SELECT price FROM products WHERE id = $1", price) == D("100.00")
    assert await w.pool.fetchval("SELECT count(*) FROM price_history WHERE product_id = $1", price) == 0


async def test_an_expired_proposal_cannot_be_decided(w: ms.World) -> None:
    await ms.product(w, "Overstock", stock=(300, 300), sold=20)
    result = await generate_proposal(w.pool, w.seller_id)
    await _due_now(w.pool)
    await _tick(w.pool)
    line_id = str(await w.pool.fetchval("SELECT id FROM proposal_lines"))

    with pytest.raises(ApprovalHTTPError) as err:
        await svc.decide(
            result.proposal_id, Actor(ms.SELLER, "seller"), lines=[{"line_id": line_id, "decision": "accept"}]
        )
    assert err.value.status_code == 409


async def test_a_decided_proposal_is_not_expired_by_its_late_timer(w: ms.World) -> None:
    price = await ms.product(w, "Overstock", stock=(300, 300), sold=20)
    result = await generate_proposal(w.pool, w.seller_id)
    line_id = str(await w.pool.fetchval("SELECT id FROM proposal_lines"))
    await svc.decide(result.proposal_id, Actor(ms.SELLER, "seller"), lines=[{"line_id": line_id, "decision": "accept"}])

    await _due_now(w.pool)
    await _tick(w.pool)

    assert (await repo.get(w.pool, result.approval_id))["status"] == "executed"
    assert (
        await w.pool.fetchval("SELECT status FROM proposals WHERE id = $1", uuid.UUID(result.proposal_id)) == "executed"
    )
    assert await w.pool.fetchval("SELECT price FROM products WHERE id = $1", price) == D("90.00")


# ── SLA expiry never races a decide that is in flight ────────────────────────


async def _status(w: ms.World, result: Any) -> tuple[str, str]:
    approval = await repo.get(w.pool, result.approval_id)
    proposal = await w.pool.fetchval("SELECT status FROM proposals WHERE id = $1", uuid.UUID(result.proposal_id))
    return approval["status"], proposal


async def test_the_deadline_passing_while_a_decide_holds_the_claim_does_not_expire_the_batch(w: ms.World) -> None:
    price = await ms.product(w, "Overstock", price="100.00", stock=(300, 300), sold=20)
    result = await generate_proposal(w.pool, w.seller_id)
    await w.pool.execute("UPDATE approvals SET claimed_at = NOW(), claimed_by = 'a-decide-in-flight'")

    await _due_now(w.pool)
    await _tick(w.pool)

    assert await _status(w, result) == ("pending_review", "pending_review")
    kinds = {r["kind"] for r in await w.pool.fetch("SELECT kind FROM notifications")}
    assert "merchant.proposal_expired" not in kinds
    deferred = await w.pool.fetch(
        "SELECT payload FROM outbox WHERE topic = 'approval.sla_breached' AND processed_at IS NULL"
    )
    assert len(deferred) == 1  # the timer was re-booked
    rebooked = json.loads(deferred[0]["payload"])
    # A look taken while the claim is live is counted, but does not spend the after-the-claim budget.
    assert (rebooked["waits"], rebooked["defers"]) == (1, 0)
    assert await w.pool.fetchval("SELECT price FROM products WHERE id = $1", price) == D("100.00")

    await w.pool.execute("UPDATE approvals SET claimed_at = NULL, claimed_by = NULL")  # the decide finished or died
    await _due_now(w.pool)
    await _tick(w.pool)

    assert await _status(w, result) == ("expired", "cancelled")


async def test_a_claim_held_for_longer_than_the_defer_budget_is_still_not_expired_under(w: ms.World) -> None:
    # The budget was 10 looks x 30 s = 300 s against a 600 s claim: a slow decide was expired while it still
    # held the claim. A live claim must outlast any number of looks the budget allows.
    await ms.product(w, "Overstock", price="100.00", stock=(300, 300), sold=20)
    result = await generate_proposal(w.pool, w.seller_id)

    for _ in range(approval_handlers._PROPOSAL_MAX_DEFERS + 3):
        await w.pool.execute("UPDATE approvals SET claimed_at = NOW(), claimed_by = 'a-slow-decide'")
        await _due_now(w.pool)
        await _tick(w.pool)
        assert await _status(w, result) == ("pending_review", "pending_review")

    await w.pool.execute("UPDATE approvals SET claimed_at = NULL, claimed_by = NULL")
    await _due_now(w.pool)
    await _tick(w.pool)
    assert await _status(w, result) == ("expired", "cancelled")


async def test_the_timer_cannot_rebook_itself_for_ever_even_under_a_claim_that_never_lapses(w: ms.World) -> None:
    await ms.product(w, "Overstock", price="100.00", stock=(300, 300), sold=20)
    result = await generate_proposal(w.pool, w.seller_id)
    await w.pool.execute("UPDATE approvals SET claimed_at = NOW(), claimed_by = 'never-lets-go'")
    await w.pool.execute(
        "UPDATE outbox SET payload = payload || jsonb_build_object('waits', $1::int) "
        "WHERE topic = 'approval.sla_breached' AND processed_at IS NULL",
        approval_handlers._proposal_max_waits(),
    )

    await _due_now(w.pool)
    await _tick(w.pool)

    assert (await _status(w, result))[0] == "expired"
    assert (
        await w.pool.fetchval(
            "SELECT count(*) FROM outbox WHERE topic = 'approval.sla_breached' AND processed_at IS NULL"
        )
        == 0
    )


async def test_a_deadline_that_lands_mid_execution_does_not_end_with_prices_applied_and_the_batch_expired(
    w: ms.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    price = await ms.product(w, "Overstock", price="100.00", stock=(300, 300), sold=20)
    result = await generate_proposal(w.pool, w.seller_id)
    line_id = str(await w.pool.fetchval("SELECT id FROM proposal_lines"))
    real = merchant_execute.execute_line
    seen: list[tuple[str, str]] = []

    async def breach_after_the_write(*args: Any, **kwargs: Any) -> dict[str, Any]:
        outcome = await real(*args, **kwargs)  # the price is now changed; the approval is not yet recorded
        await _due_now(w.pool)
        await _tick(w.pool)  # the SLA timer fires right here
        seen.append(await _status(w, result))
        return outcome

    monkeypatch.setattr(merchant_execute, "execute_line", breach_after_the_write)

    done = await svc.decide(
        result.proposal_id, Actor(ms.SELLER, "seller"), lines=[{"line_id": line_id, "decision": "accept"}]
    )

    assert seen == [("pending_review", "pending_review")]  # the timer looked, saw a decide, and stood down
    assert done["approval"]["status"] == "executed"
    assert await _status(w, result) == ("executed", "executed")
    assert await w.pool.fetchval("SELECT price FROM products WHERE id = $1", price) == D("90.00")
    kinds = {r["kind"] for r in await w.pool.fetch("SELECT kind FROM notifications")}
    assert "merchant.proposal_expired" not in kinds


async def test_a_decide_that_executed_lines_and_died_is_closed_truthfully_not_as_nothing_changed(
    w: ms.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    price = await ms.product(w, "Overstock", price="100.00", stock=(300, 300), sold=20)
    result = await generate_proposal(w.pool, w.seller_id)
    line_id = str(await w.pool.fetchval("SELECT id FROM proposal_lines"))
    await merchant_execute.apply_line(w.pool, line_id, D("90.00"))  # applied; the decision was never recorded
    assert await w.pool.fetchval("SELECT price FROM products WHERE id = $1", price) == D("90.00")

    for _ in range(12):  # the timer keeps looking while a retry could still finish it, then closes it
        await _due_now(w.pool)
        await _tick(w.pool)

    approval = await repo.get(w.pool, result.approval_id)
    assert approval["status"] == "expired" and approval["decision"]["executed_lines"] == 1
    assert (await _status(w, result))[1] == "executed"  # not "cancelled": a change did run
    body = await w.pool.fetchval("SELECT body FROM notifications WHERE kind = 'merchant.proposal_expired'")
    assert "1 change(s) had already been applied" in body and "Nothing was changed" not in body


# ── an interrupted decide ────────────────────────────────────────────────────


async def test_a_decide_that_dies_midway_can_be_retried_and_nothing_is_applied_twice(
    w: ms.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = await ms.product(w, "Alpha", price="100.00", stock=(500, 300), sold=20)
    second = await ms.product(w, "Beta", price="80.00", stock=(300, 300), sold=20)
    result = await generate_proposal(w.pool, w.seller_id)
    rows = await w.pool.fetch("SELECT l.id, p.name FROM proposal_lines l JOIN products p ON p.id = l.product_id")
    lines = [{"line_id": str(r["id"]), "decision": "accept"} for r in rows]
    real = merchant_execute.execute_line
    calls = {"n": 0}

    async def flaky(pool, line_id, value, **kw):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("the process died")
        return await real(pool, line_id, value, **kw)

    monkeypatch.setattr(merchant_execute, "execute_line", flaky)
    actor = Actor(ms.SELLER, "seller")
    with pytest.raises(RuntimeError):
        await svc.decide(result.proposal_id, actor, lines=lines)
    assert (await repo.get(w.pool, result.approval_id))["status"] == "pending_review"  # still open, claim handed back
    assert (
        await w.pool.fetchval("SELECT count(*) FROM price_history WHERE product_id = ANY($1::uuid[])", [first, second])
        == 1
    )

    monkeypatch.setattr(merchant_execute, "execute_line", real)
    done = await svc.decide(result.proposal_id, actor, lines=lines)

    assert done["approval"]["status"] == "executed"
    assert (
        await w.pool.fetchval("SELECT count(*) FROM price_history WHERE product_id = ANY($1::uuid[])", [first, second])
        == 2
    )
    assert {x["status"] for x in done["proposal"]["lines"]} == {"executed"}


# ── the daily run ────────────────────────────────────────────────────────────


def test_next_run_is_the_first_run_hour_strictly_after_now() -> None:
    early = datetime(2026, 11, 16, 5, 59, tzinfo=UTC)
    exact = datetime(2026, 11, 16, 6, 0, tzinfo=UTC)
    assert next_run_at(early, 6) == datetime(2026, 11, 16, 6, 0, tzinfo=UTC)
    assert next_run_at(exact, 6) == datetime(2026, 11, 17, 6, 0, tzinfo=UTC)
    assert next_run_at(early + timedelta(hours=20), 6) == datetime(2026, 11, 17, 6, 0, tzinfo=UTC)


def test_the_schedule_is_off_by_default() -> None:
    assert settings.MERCHANT_OPS_SCHEDULE_ENABLED is False


async def test_with_the_schedule_off_nothing_is_booked_and_a_stray_row_does_nothing_and_does_not_reschedule(
    w: ms.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    await ms.product(w, "Overstock", stock=(300, 300), sold=20)
    monkeypatch.setattr(settings, "MERCHANT_OPS_SCHEDULE_ENABLED", False)

    await merchant_handlers.ensure_scheduled(w.pool)
    assert await w.pool.fetchval("SELECT count(*) FROM outbox") == 0

    await w.pool.execute(
        "INSERT INTO outbox (topic, payload, available_at) VALUES ($1, '{}'::jsonb, NOW())", TOPIC_DAILY_RUN
    )
    await _tick(w.pool)
    assert await w.pool.fetchval("SELECT count(*) FROM proposals") == 0
    assert await w.pool.fetchval("SELECT count(*) FROM outbox WHERE processed_at IS NULL") == 0


async def test_the_daily_run_generates_for_every_seller_and_books_the_next_day_once(
    w: ms.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    await ms.product(w, "Mine", stock=(300, 300), sold=20)
    await ms.product(w, "Theirs", seller=w.other_seller_id, stock=(300, 300), sold=20)
    monkeypatch.setattr(settings, "MERCHANT_OPS_SCHEDULE_ENABLED", True)
    await merchant_handlers.ensure_scheduled(w.pool)
    await merchant_handlers.ensure_scheduled(w.pool)  # a restart or a second worker: still one row
    (booked,) = await w.pool.fetch("SELECT * FROM outbox WHERE topic = $1", TOPIC_DAILY_RUN)
    assert booked["available_at"] > datetime.now(UTC)

    await _due_now(w.pool, TOPIC_DAILY_RUN)
    await _tick(w.pool)

    triggers = await w.pool.fetch("SELECT trigger, seller_id FROM proposals ORDER BY seller_id")
    assert {t["trigger"] for t in triggers} == {"scheduled"} and len(triggers) == 2
    pending = await w.pool.fetch("SELECT * FROM outbox WHERE topic = $1 AND processed_at IS NULL", TOPIC_DAILY_RUN)
    assert len(pending) == 1 and pending[0]["available_at"] > datetime.now(UTC)
    assert json.loads(pending[0]["payload"])["run_date"] == pending[0]["available_at"].date().isoformat()

    # A second run the same day adds nothing: the open lines are not proposed again.
    await _due_now(w.pool, TOPIC_DAILY_RUN)
    await _tick(w.pool)
    assert await w.pool.fetchval("SELECT count(*) FROM proposals") == 2
    assert (
        await w.pool.fetchval("SELECT count(*) FROM outbox WHERE topic = $1 AND processed_at IS NULL", TOPIC_DAILY_RUN)
        == 1
    )


async def test_one_failing_seller_does_not_stop_the_others(w: ms.World, monkeypatch: pytest.MonkeyPatch) -> None:
    await ms.product(w, "Mine", stock=(300, 300), sold=20)
    await ms.product(w, "Theirs", seller=w.other_seller_id, stock=(300, 300), sold=20)
    monkeypatch.setattr(settings, "MERCHANT_OPS_SCHEDULE_ENABLED", True)
    real = merchant_handlers.generate_proposal

    async def sometimes(conn, seller_id, **kw):
        if seller_id == w.seller_id:
            await conn.execute(
                "INSERT INTO proposals (seller_id, kind) VALUES ($1, 'markdown')", seller_id
            )  # a write to undo
            raise RuntimeError("boom")
        return await real(conn, seller_id, **kw)

    monkeypatch.setattr(merchant_handlers, "generate_proposal", sometimes)
    await merchant_handlers.ensure_scheduled(w.pool)
    await _due_now(w.pool, TOPIC_DAILY_RUN)
    await _tick(w.pool)

    rows = await w.pool.fetch("SELECT seller_id FROM proposals")
    assert [r["seller_id"] for r in rows] == [w.other_seller_id]  # the failing seller's partial write rolled back alone
    assert (
        await w.pool.fetchval("SELECT count(*) FROM outbox WHERE topic = $1 AND processed_at IS NULL", TOPIC_DAILY_RUN)
        == 1
    )
