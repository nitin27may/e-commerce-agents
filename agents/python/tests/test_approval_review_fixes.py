# ruff: noqa: F811, F401
"""Review fixes on the Python decide path: W3, S1, S2 and the rank / unknown-route parity nits."""

from __future__ import annotations

from typing import Any

import pytest

from orchestrator import approvals_service as svc
from shared.approvals import intake, repo
from tests.test_approvals_routes import REQUESTER, _paused, _status, _tool_approval, executed, pool

SUPPORT = svc.Actor("support-agent@example.com", "support")
ADMIN = svc.Actor("manager@example.com", "admin")


# ── parity nits (no database) ────────────────────────────────────────────────


def test_staff_rank_is_case_insensitive_like_the_dotnet_rank() -> None:
    assert [svc.repo_rank(r) for r in ("support", "Support", "ADMIN", "admin", "customer", "", None)] == [
        1,
        1,
        2,
        2,
        0,
        0,
        0,
    ]
    assert svc.can_review({"assigned_role": "support"}, svc.Actor("a@example.com", "Admin"))


@pytest.mark.integration
@pytest.mark.asyncio
async def test_an_unknown_route_fails_closed_to_a_manager_instead_of_raising(pool, caplog) -> None:
    approval_id = await _tool_approval(pool, route="bogus")

    row = await pool.fetchrow("SELECT route, assigned_role FROM approvals WHERE id = $1", approval_id)
    assert (row["route"], row["assigned_role"]) == ("manager", "admin")
    assert any("approval route unknown" in r.getMessage() for r in caplog.records)


# ── W3: an escalation between the read and the claim ─────────────────────────


@pytest.fixture
def escalate_before_claim(monkeypatch: pytest.MonkeyPatch):
    """Make the SLA escalation (support -> manager) land exactly between decide()'s read and the claim."""
    real = svc._claim
    fired: list[str] = []

    async def _escalating(approval: dict[str, Any], *statuses: Any) -> str:
        if not fired:
            fired.append(str(approval["id"]))
            await svc.get_pool().execute(
                "UPDATE approvals SET assigned_role = 'admin', route = 'manager' WHERE id = $1", approval["id"]
            )
        return await real(approval, *statuses)

    monkeypatch.setattr(svc, "_claim", _escalating)
    return fired


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("decision", "kwargs"),
    [
        ("approve", {}),
        ("reject", {"note": "no"}),
        ("request_info", {"note": "which item?"}),
    ],
)
async def test_a_support_reviewer_cannot_finish_a_tool_decision_escalated_after_their_check(
    pool, executed, escalate_before_claim, decision: str, kwargs: dict[str, Any]
) -> None:
    approval_id = await _tool_approval(pool, route="support")

    with pytest.raises(svc.ApprovalHTTPError) as exc:
        await svc.decide(approval_id, SUPPORT, decision=decision, **kwargs)

    assert escalate_before_claim, "the escalation must have landed between the read and the claim"
    assert exc.value.status_code == 403
    assert executed == [], "nothing may run for a reviewer who is now below the assigned role"
    row = await pool.fetchrow("SELECT status, claimed_by FROM approvals WHERE id = $1", approval_id)
    assert (row["status"], row["claimed_by"]) == ("pending_review", None), "refused, and the claim is handed back"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_a_manager_still_decides_after_the_same_escalation(pool, executed, escalate_before_claim) -> None:
    approval_id = await _tool_approval(pool, route="support")

    result = await svc.decide(approval_id, ADMIN, decision="approve")

    assert result["approval"]["status"] == "executed"
    assert len(executed) == 1


# ── S1: an executed action whose recording lost the claim ────────────────────


@pytest.fixture
def lose_the_claim_once(monkeypatch: pytest.MonkeyPatch):
    """The first ``_record`` fails the way a lost claim does (409), after the action has already run."""
    real = svc._record
    state = {"failed": 0}

    async def _flaky(*args: Any, **kwargs: Any) -> dict[str, Any]:
        if not state["failed"]:
            state["failed"] = 1
            raise svc.ApprovalHTTPError(409, "This approval was taken over by another reviewer")
        return await real(*args, **kwargs)

    monkeypatch.setattr(svc, "_record", _flaky)
    return state


@pytest.mark.integration
@pytest.mark.asyncio
async def test_a_retry_after_a_lost_claim_records_the_executed_action_instead_of_409(
    pool, executed, lose_the_claim_once
) -> None:
    approval_id = await _tool_approval(pool, route="support")

    with pytest.raises(svc.ApprovalHTTPError) as first:
        await svc.decide(approval_id, SUPPORT, decision="approve")
    assert first.value.status_code == 409
    assert await _status(pool, approval_id) == "pending_review", "the first attempt did not record"
    request = await pool.fetchrow("SELECT status FROM tool_approval_requests WHERE approval_id = $1", approval_id)
    assert request["status"] == "executed", "but the action ran"

    retried = await svc.decide(approval_id, SUPPORT, decision="approve")

    assert retried["approval"]["status"] == "executed"
    assert len(executed) == 1, "the retry replays the recorded run; it never acts twice"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_the_timeout_handler_does_not_expire_an_action_that_already_ran(
    pool, executed, lose_the_claim_once
) -> None:
    approval_id = await _tool_approval(pool, route="support")
    with pytest.raises(svc.ApprovalHTTPError):
        await svc.decide(approval_id, SUPPORT, decision="approve")

    result = await svc.expire(approval_id)

    assert result["approval"]["status"] == "executed"
    assert await _status(pool, approval_id) == "executed"
    assert len(executed) == 1
    kinds = [e["action"] for e in await repo.list_events(pool, approval_id)]
    assert "expired" not in kinds


@pytest.mark.integration
@pytest.mark.asyncio
async def test_a_genuinely_resolved_request_is_still_a_conflict(pool, executed) -> None:
    """The recovery only applies to a request executed *for this approval*."""
    approval_id = await _tool_approval(pool, route="support")
    await pool.execute(
        "UPDATE tool_approval_requests SET status = 'denied' WHERE approval_id = $1",
        approval_id,
    )

    with pytest.raises(svc.ApprovalHTTPError) as exc:
        await svc.decide(approval_id, SUPPORT, decision="approve")

    assert exc.value.status_code == 409
    assert await _status(pool, approval_id) == "pending_review"


# ── S2: a lost claim on the workflow path is a 409 ───────────────────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_a_claim_lost_while_recording_a_workflow_decision_is_409_not_500(pool, monkeypatch) -> None:
    _, _, approval_id, _ = await _paused(pool, monkeypatch)
    real = repo.transition

    async def _lost(db: Any, aid: str, froms: Any, to: Any, **kw: Any) -> dict[str, Any]:
        if kw.get("action") == "decided":
            raise repo.ClaimLostError("the execution claim is no longer held")
        return await real(db, aid, froms, to, **kw)

    monkeypatch.setattr(repo, "transition", _lost)

    with pytest.raises(svc.ApprovalHTTPError) as exc:
        await svc.decide(approval_id, ADMIN, decision="approve")

    assert exc.value.status_code == 409
    assert "taken over" in exc.value.detail
