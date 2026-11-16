"""D-3 -- the approvals repository against real Postgres.

Covers the full transition matrix (every from/to pair, accepted or refused), the self-approval rule in
both the repository and the database, the append-only audit trail, creation idempotency and the claim fence.
"""

from __future__ import annotations

import asyncio
import itertools
import uuid

import asyncpg
import pytest

from shared.approvals import repo
from shared.approvals.repo import ALLOWED_TRANSITIONS, ApprovalStatus

pytestmark = pytest.mark.integration

REQUESTER = "requester@example.com"
REVIEWER = "reviewer@example.com"
ALL = list(ApprovalStatus)


async def _make(pool: asyncpg.Pool, status: ApprovalStatus = ApprovalStatus.PENDING_CONFIRM, **kw) -> dict:
    return await repo.create(
        pool, source=kw.pop("source", "tool"), kind="return_approval", requester_email=REQUESTER, status=status, **kw
    )


async def _force_status(pool: asyncpg.Pool, approval_id: str, status: ApprovalStatus) -> None:
    await pool.execute("UPDATE approvals SET status = $2 WHERE id = $1", approval_id, status.value)


# ── create ────────────────────────────────────────────────────────────────────


async def test_create_defaults_and_audits(clean_db: asyncpg.Pool) -> None:
    row = await _make(clean_db, amount=42.5, recommendation={"reasons": ["small"]}, source_ref=str(uuid.uuid4()))

    assert row["status"] == "pending_confirm"
    assert row["escalation_level"] == 0 and row["claimed_at"] is None and row["decided_by"] is None
    assert row["recommendation"] == {"reasons": ["small"]}
    events = await repo.list_events(clean_db, str(row["id"]))
    assert [e["action"] for e in events] == ["created"]
    assert events[0]["payload"]["kind"] == "return_approval"


async def test_create_is_idempotent_per_source_ref(clean_db: asyncpg.Pool) -> None:
    ref = str(uuid.uuid4())
    first = await _make(clean_db, source_ref=ref)
    again = await _make(clean_db, source_ref=ref)

    assert again["id"] == first["id"]
    assert await clean_db.fetchval("SELECT count(*) FROM approvals") == 1
    assert await clean_db.fetchval("SELECT count(*) FROM approval_events") == 1
    # The same ref under another source is a different record.
    other = await _make(clean_db, source="workflow", source_ref=ref)
    assert other["id"] != first["id"]


async def test_create_rejects_unknown_source_and_route(clean_db: asyncpg.Pool) -> None:
    with pytest.raises(ValueError):
        await repo.create(clean_db, source="cron", kind="k", requester_email=REQUESTER)
    with pytest.raises(ValueError):
        await _make(clean_db, route="somebody")


# ── the full transition matrix ───────────────────────────────────────────────


@pytest.mark.parametrize(
    ("src", "dst"),
    [(s, d) for s, d in itertools.product(ALL, ALL) if d in ALLOWED_TRANSITIONS[s]],
    ids=lambda s: s.value,
)
async def test_every_lifecycle_edge_is_accepted(
    clean_db: asyncpg.Pool, src: ApprovalStatus, dst: ApprovalStatus
) -> None:
    row = await _make(clean_db)
    await _force_status(clean_db, str(row["id"]), src)

    moved = await repo.transition(clean_db, str(row["id"]), [src], dst, actor_email=REVIEWER, actor_role="support")

    assert moved["status"] == dst.value
    events = await repo.list_events(clean_db, str(row["id"]))
    assert events[-1]["action"] == f"status.{dst.value}"
    assert events[-1]["payload"] == {"from": src.value, "to": dst.value}
    assert events[-1]["actor_email"] == REVIEWER


@pytest.mark.parametrize(
    ("src", "dst"),
    [(s, d) for s, d in itertools.product(ALL, ALL) if d not in ALLOWED_TRANSITIONS[s]],
    ids=lambda s: s.value,
)
async def test_a_non_edge_is_refused_before_touching_the_row(
    clean_db: asyncpg.Pool, src: ApprovalStatus, dst: ApprovalStatus
) -> None:
    row = await _make(clean_db)
    await _force_status(clean_db, str(row["id"]), src)

    with pytest.raises(ValueError, match="not an approval lifecycle edge"):
        await repo.transition(clean_db, str(row["id"]), [src], dst)

    assert (await repo.get(clean_db, str(row["id"])))["status"] == src.value


@pytest.mark.parametrize("current", ALL, ids=lambda s: s.value)
async def test_transition_fails_when_the_row_is_not_in_an_allowed_source_state(
    clean_db: asyncpg.Pool, current: ApprovalStatus
) -> None:
    """Guard: the caller says it must be in pending_review; every other current state is refused."""
    row = await _make(clean_db)
    await _force_status(clean_db, str(row["id"]), current)

    if current is ApprovalStatus.PENDING_REVIEW:
        moved = await repo.transition(
            clean_db, str(row["id"]), [ApprovalStatus.PENDING_REVIEW], ApprovalStatus.APPROVED
        )
        assert moved["status"] == "approved"
        return
    with pytest.raises(repo.InvalidTransitionError):
        await repo.transition(clean_db, str(row["id"]), [ApprovalStatus.PENDING_REVIEW], ApprovalStatus.APPROVED)

    after = await repo.get(clean_db, str(row["id"]))
    assert after["status"] == current.value
    # A refused transition leaves no audit row behind.
    assert [e["action"] for e in await repo.list_events(clean_db, str(row["id"]))] == ["created"]


async def test_transition_of_a_missing_approval(clean_db: asyncpg.Pool) -> None:
    with pytest.raises(repo.ApprovalNotFoundError):
        await repo.transition(clean_db, str(uuid.uuid4()), [ApprovalStatus.PENDING_REVIEW], ApprovalStatus.APPROVED)


async def test_transition_sets_decision_fields(clean_db: asyncpg.Pool) -> None:
    row = await _make(clean_db, status=ApprovalStatus.PENDING_REVIEW, route="support", assigned_role="support")

    moved = await repo.transition(
        clean_db,
        str(row["id"]),
        [ApprovalStatus.PENDING_REVIEW],
        ApprovalStatus.APPROVED_WITH_EDITS,
        decided_by=REVIEWER,
        decision={"decision": "approve", "edits": {"amount": 10}},
        actor_email=REVIEWER,
        actor_role="support",
    )

    assert moved["decided_by"] == REVIEWER and moved["decided_at"] is not None
    assert moved["decision"] == {"decision": "approve", "edits": {"amount": 10}}
    assert moved["updated_at"] >= row["updated_at"]


async def test_transition_refuses_columns_outside_the_settable_set(clean_db: asyncpg.Pool) -> None:
    row = await _make(clean_db, status=ApprovalStatus.PENDING_REVIEW)
    with pytest.raises(TypeError):
        await repo.transition(
            clean_db, str(row["id"]), [ApprovalStatus.PENDING_REVIEW], ApprovalStatus.APPROVED, requester_email="x"
        )


async def test_concurrent_transitions_one_wins(clean_db: asyncpg.Pool) -> None:
    row = await _make(clean_db, status=ApprovalStatus.PENDING_REVIEW)

    async def approve(who: str):
        return await repo.transition(
            clean_db, str(row["id"]), [ApprovalStatus.PENDING_REVIEW], ApprovalStatus.APPROVED, decided_by=who
        )

    results = await asyncio.gather(approve("a@example.com"), approve("b@example.com"), return_exceptions=True)

    assert sum(isinstance(r, dict) for r in results) == 1
    assert sum(isinstance(r, repo.InvalidTransitionError) for r in results) == 1


async def test_transition_joins_the_callers_transaction(clean_db: asyncpg.Pool) -> None:
    row = await _make(clean_db, status=ApprovalStatus.PENDING_REVIEW)

    async with clean_db.acquire() as conn:
        with pytest.raises(RuntimeError):
            async with conn.transaction():
                await repo.transition(conn, str(row["id"]), [ApprovalStatus.PENDING_REVIEW], ApprovalStatus.REJECTED)
                raise RuntimeError("business write failed")

    after = await repo.get(clean_db, str(row["id"]))
    assert after["status"] == "pending_review"
    assert [e["action"] for e in await repo.list_events(clean_db, str(row["id"]))] == ["created"]


# ── self-approval ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("decider", [REQUESTER, REQUESTER.upper(), f"  {REQUESTER} "])
async def test_repository_refuses_self_approval(clean_db: asyncpg.Pool, decider: str) -> None:
    row = await _make(clean_db, status=ApprovalStatus.PENDING_REVIEW)

    with pytest.raises(repo.SelfApprovalError):
        await repo.transition(
            clean_db, str(row["id"]), [ApprovalStatus.PENDING_REVIEW], ApprovalStatus.APPROVED, decided_by=decider
        )

    after = await repo.get(clean_db, str(row["id"]))
    assert after["status"] == "pending_review" and after["decided_by"] is None


async def test_database_refuses_self_approval_even_around_the_repository(clean_db: asyncpg.Pool) -> None:
    row = await _make(clean_db, status=ApprovalStatus.PENDING_REVIEW)

    with pytest.raises(asyncpg.CheckViolationError, match="approvals_no_self_approval"):
        await clean_db.execute("UPDATE approvals SET decided_by = $2 WHERE id = $1", row["id"], REQUESTER)
    with pytest.raises(asyncpg.CheckViolationError, match="approvals_no_self_approval"):
        await clean_db.execute("UPDATE approvals SET decided_by = $2 WHERE id = $1", row["id"], REQUESTER.upper())
    with pytest.raises(asyncpg.CheckViolationError, match="approvals_no_self_approval"):
        await clean_db.execute(
            "INSERT INTO approvals (source, kind, requester_email, decided_by) VALUES ('tool', 'k', $1, $1)", REQUESTER
        )


async def test_a_different_decider_is_fine(clean_db: asyncpg.Pool) -> None:
    row = await _make(clean_db, status=ApprovalStatus.PENDING_REVIEW)
    moved = await repo.transition(
        clean_db, str(row["id"]), [ApprovalStatus.PENDING_REVIEW], ApprovalStatus.APPROVED, decided_by=REVIEWER
    )
    assert moved["decided_by"] == REVIEWER


# ── schema constraints ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "column,value",
    [
        ("status", "bogus"),
        ("route", "bogus"),
        ("assigned_role", "customer"),
        ("source", "bogus"),
        ("escalation_level", -1),
    ],
)
async def test_enumerated_columns_are_checked(clean_db: asyncpg.Pool, column: str, value: object) -> None:
    row = await _make(clean_db)
    with pytest.raises(asyncpg.CheckViolationError):
        await clean_db.execute(f"UPDATE approvals SET {column} = $2 WHERE id = $1", row["id"], value)


# ── append-only audit ────────────────────────────────────────────────────────


async def test_audit_events_cannot_be_updated_or_deleted(clean_db: asyncpg.Pool) -> None:
    row = await _make(clean_db)
    event_id = await repo.append_event(clean_db, str(row["id"]), "note", actor_email=REVIEWER, payload={"n": 1})

    with pytest.raises(asyncpg.RestrictViolationError, match="append-only"):
        await clean_db.execute("UPDATE approval_events SET action = 'tampered' WHERE id = $1", event_id)
    with pytest.raises(asyncpg.RestrictViolationError, match="append-only"):
        await clean_db.execute("DELETE FROM approval_events WHERE id = $1", event_id)
    with pytest.raises(asyncpg.RestrictViolationError, match="append-only"):
        await clean_db.execute("DELETE FROM approval_events")

    events = await repo.list_events(clean_db, str(row["id"]))
    assert [e["action"] for e in events] == ["created", "note"]
    assert events[1]["payload"] == {"n": 1}


async def test_an_approval_with_history_cannot_be_deleted(clean_db: asyncpg.Pool) -> None:
    row = await _make(clean_db)
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await clean_db.execute("DELETE FROM approvals WHERE id = $1", row["id"])


async def test_event_for_a_missing_approval_is_refused(clean_db: asyncpg.Pool) -> None:
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await repo.append_event(clean_db, str(uuid.uuid4()), "note")


# ── claim fence ──────────────────────────────────────────────────────────────


async def test_claim_is_exclusive_until_released_or_stale(clean_db: asyncpg.Pool) -> None:
    row = await _make(clean_db, status=ApprovalStatus.PENDING_REVIEW)
    aid = str(row["id"])
    states = [ApprovalStatus.PENDING_REVIEW]

    assert await repo.claim(clean_db, aid, "worker-a", statuses=states) is not None
    assert await repo.claim(clean_db, aid, "worker-b", statuses=states) is None
    assert await repo.release_claim(clean_db, aid, "worker-b") is False  # not the holder
    assert await repo.release_claim(clean_db, aid, "worker-a") is True
    assert await repo.claim(clean_db, aid, "worker-b", statuses=states) is not None

    await clean_db.execute("UPDATE approvals SET claimed_at = NOW() - interval '1 hour' WHERE id = $1", row["id"])
    taken = await repo.claim(clean_db, aid, "worker-c", statuses=states, ttl_seconds=600)
    assert taken is not None and taken["claimed_by"] == "worker-c"


async def test_claim_respects_the_status_guard(clean_db: asyncpg.Pool) -> None:
    row = await _make(clean_db, status=ApprovalStatus.REJECTED)
    assert await repo.claim(clean_db, str(row["id"]), "w", statuses=[ApprovalStatus.APPROVED]) is None


async def test_concurrent_claims_have_one_winner(clean_db: asyncpg.Pool) -> None:
    row = await _make(clean_db, status=ApprovalStatus.APPROVED)
    results = await asyncio.gather(
        *(repo.claim(clean_db, str(row["id"]), f"w{i}", statuses=[ApprovalStatus.APPROVED]) for i in range(5))
    )
    assert sum(r is not None for r in results) == 1


# ── claim-fenced transitions (D-5a) ──────────────────────────────────────────


async def test_a_fenced_transition_clears_the_claim(clean_db: asyncpg.Pool) -> None:
    row = await _make(clean_db, status=ApprovalStatus.PENDING_REVIEW)
    aid = str(row["id"])
    await repo.claim(clean_db, aid, "token-a", statuses=[ApprovalStatus.PENDING_REVIEW])

    moved = await repo.transition(
        clean_db, aid, [ApprovalStatus.PENDING_REVIEW], ApprovalStatus.APPROVED, claimed_by="token-a"
    )

    assert moved["status"] == "approved" and moved["claimed_at"] is None and moved["claimed_by"] is None


async def test_a_fenced_transition_refuses_a_lost_claim_and_changes_nothing(clean_db: asyncpg.Pool) -> None:
    row = await _make(clean_db, status=ApprovalStatus.PENDING_REVIEW)
    aid = str(row["id"])
    await repo.claim(clean_db, aid, "token-b", statuses=[ApprovalStatus.PENDING_REVIEW])  # someone took over

    with pytest.raises(repo.ClaimLostError):
        await repo.transition(
            clean_db, aid, [ApprovalStatus.PENDING_REVIEW], ApprovalStatus.APPROVED, claimed_by="token-a"
        )

    current = await repo.get(clean_db, aid)
    assert current["status"] == "pending_review" and current["claimed_by"] == "token-b"
    assert [e["action"] for e in await repo.list_events(clean_db, aid)] == ["created"]
    with pytest.raises(repo.InvalidTransitionError):  # a ClaimLostError is also an InvalidTransitionError
        await repo.transition(clean_db, aid, [ApprovalStatus.PENDING_REVIEW], ApprovalStatus.REJECTED, claimed_by="x")


# ── queue reads ──────────────────────────────────────────────────────────────


async def test_list_for_staff_filters_by_status_and_assigned_role(clean_db: asyncpg.Pool) -> None:
    mine = await _make(clean_db, status=ApprovalStatus.PENDING_REVIEW, assigned_role="support")
    await _make(clean_db, status=ApprovalStatus.PENDING_REVIEW, assigned_role="admin")
    await _make(clean_db, status=ApprovalStatus.REJECTED, assigned_role="support")

    open_for_support = await repo.list_for_staff(
        clean_db, statuses=[ApprovalStatus.PENDING_REVIEW], assigned_roles=["support"]
    )
    everything = await repo.list_for_staff(clean_db)

    assert [r["id"] for r in open_for_support] == [mine["id"]]
    assert len(everything) == 3


async def test_list_for_requester_is_case_insensitive_and_scoped(clean_db: asyncpg.Pool) -> None:
    row = await _make(clean_db)
    await repo.create(clean_db, source="tool", kind="x", requester_email="someone-else@example.com")

    mine = await repo.list_for_requester(clean_db, REQUESTER.upper())

    assert [r["id"] for r in mine] == [row["id"]]
