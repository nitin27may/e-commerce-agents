"""The inbox fallback routes to a manager, never to the lowest staff tier.

Where the policy decision is missing (the best-effort open at pause time failed, a pre-inbox row) the repair
path re-runs the real return policy when the paused run recorded its facts, and otherwise fails closed.
"""

from __future__ import annotations

import json

import pytest

from orchestrator import approvals_service as svc
from shared.approvals import intake
from tests.test_approvals_routes import _paused, pool  # noqa: F401  (pool is a fixture)

pytestmark = pytest.mark.integration


async def _unlinked_copy(pool, run_id, payload: str | None = None) -> str:  # noqa: F811
    """A second hitl_requests row for the run with no inbox approval: a pre-inbox / failed-open row."""
    return str(
        await pool.fetchval(
            """INSERT INTO hitl_requests (workflow_run_id, request_id, checkpoint_id, user_email, kind, payload, status)
               SELECT workflow_run_id, request_id, checkpoint_id, user_email, kind, COALESCE($2::jsonb, payload), status
                 FROM hitl_requests WHERE workflow_run_id = $1 AND approval_id IS NOT NULL
               RETURNING id""",
            run_id,
            payload,
        )
    )


def test_the_engine_fallback_is_a_manager_review() -> None:
    assert intake.fallback_decision().route == "manager"


@pytest.mark.asyncio
async def test_an_empty_policy_opens_a_manager_approval(pool, monkeypatch) -> None:  # noqa: F811
    _, run_id, _, _ = await _paused(pool, monkeypatch)
    hitl_id = await _unlinked_copy(pool, run_id)

    approval = await intake.open_workflow_approval(
        pool, hitl_request_id=hitl_id, user_email="buyer@example.com", conversation_id=None, policy={}, amount=None
    )

    assert (approval["route"], approval["assigned_role"]) == ("manager", "admin")


@pytest.mark.asyncio
async def test_repair_without_recorded_facts_fails_closed_to_a_manager(pool, monkeypatch) -> None:  # noqa: F811
    _, run_id, _, _ = await _paused(pool, monkeypatch)
    hitl_id = await _unlinked_copy(pool, run_id, "{}")

    repaired = await svc.ensure_for_hitl_request(hitl_id)

    row = await pool.fetchrow("SELECT route, assigned_role FROM approvals WHERE id = $1", repaired)
    assert (row["route"], row["assigned_role"]) == ("manager", "admin")


@pytest.mark.asyncio
async def test_repair_re_evaluates_the_policy_from_the_recorded_facts(pool, monkeypatch) -> None:  # noqa: F811
    order_id, run_id, _, _ = await _paused(pool, monkeypatch)
    hitl_id = await _unlinked_copy(pool, run_id)
    payload = json.loads(await pool.fetchval("SELECT payload::text FROM hitl_requests WHERE id = $1", hitl_id))
    assert payload["order_id"] == order_id and payload["order_total"], "the pause records the facts it needs"
    seen: dict = {}
    real = intake.policy_for_return

    async def spy(db, **kw):
        seen.update(kw)
        return await real(db, **kw)

    monkeypatch.setattr(intake, "policy_for_return", spy)

    repaired = await svc.ensure_for_hitl_request(hitl_id)

    assert seen["order_id"] == order_id
    row = await pool.fetchrow("SELECT route, recommendation::text AS rec FROM approvals WHERE id = $1", repaired)
    assert row["route"] == "manager" and "rule" in row["rec"], "routed by the policy's own evidence, not a default"
