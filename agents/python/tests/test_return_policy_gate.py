"""D-8 -- the return workflow's policy gate against the real engine and real Postgres.

``tests/test_return_replace_workflow.py`` covers the graph with an injected policy; this covers the default
policy path (facts gathered from the requester's own rows, evaluated by ``shared/policy``), the amount
adjuster the workflow applies after a reviewer edits, and what the default policy does with the demo tour's
return.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from shared.context import current_user_email
from shared.policy import evaluate
from shared.return_ops import adjust_return_amount
from workflows.return_replace import ReturnAndReplaceWorkflow, WorkflowState

EMAIL = "gate-customer@example.com"


async def _customer(pool, *, total: float, spend: float, account_days: int, delivered_days_ago: int = 5):
    user_id = uuid.uuid4()
    await pool.execute(
        """INSERT INTO users (id, email, password_hash, name, role, total_spend, created_at)
           VALUES ($1, $2, 'hash', 'Gate', 'customer', $3, NOW() - make_interval(days => $4))""",
        user_id,
        EMAIL,
        spend,
        account_days,
    )
    order_id = uuid.uuid4()
    await pool.execute(
        """INSERT INTO orders (id, user_id, status, total, shipping_address)
           VALUES ($1, $2, 'delivered', $3, '{}'::jsonb)""",
        order_id,
        user_id,
        total,
    )
    await pool.execute(
        """INSERT INTO order_status_history (order_id, status, timestamp)
           VALUES ($1, 'delivered', NOW() - make_interval(days => $2))""",
        order_id,
        delivered_days_ago,
    )
    return user_id, str(order_id)


def _stub_tools(calls: list[str]) -> dict[str, Any]:
    async def check(order_id: str) -> dict[str, Any]:
        return {"eligible": True}

    async def initiate(order_id: str, reason: str, refund_method: str) -> dict[str, Any]:
        calls.append("initiate_return")
        return {"return_id": "ret-1", "refund_amount": 30.0}

    async def search(max_price: float, min_rating: float, limit: int) -> list[dict[str, Any]]:
        return []

    async def tier() -> dict[str, Any]:
        return {"tier": "bronze", "discount_pct": 0.0}

    return {
        "check_return_eligibility": check,
        "initiate_return": initiate,
        "search_products": search,
        "get_loyalty_tier": tier,
    }


@pytest.mark.asyncio
async def test_a_small_in_good_standing_return_is_auto_approved_by_the_engine(clean_db, monkeypatch) -> None:
    monkeypatch.setattr("shared.db._pool", clean_db, raising=False)
    _, order_id = await _customer(clean_db, total=30.0, spend=1000.0, account_days=200)
    current_user_email.set(EMAIL)
    calls: list[str] = []

    result = await ReturnAndReplaceWorkflow(_stub_tools(calls)).execute(
        WorkflowState(user_email=EMAIL, order_id=order_id, order_total=30.0)
    )

    assert result.hitl_requested is False and "finalize" in result.completed_steps
    assert calls == ["initiate_return"], "auto proceeds without pausing"


@pytest.mark.asyncio
async def test_a_large_return_pauses_for_a_manager_with_the_engines_evidence(clean_db, monkeypatch) -> None:
    from workflows.return_replace import ReturnApprovalRequest

    monkeypatch.setattr("shared.db._pool", clean_db, raising=False)
    _, order_id = await _customer(clean_db, total=649.99, spend=2000.0, account_days=200)
    current_user_email.set(EMAIL)
    calls: list[str] = []

    workflow = ReturnAndReplaceWorkflow(_stub_tools(calls))._build_maf_workflow()
    requests: list[ReturnApprovalRequest] = []
    async for event in workflow.run(
        WorkflowState(user_email=EMAIL, order_id=order_id, order_total=649.99), stream=True
    ):
        if getattr(event, "type", None) == "request_info" and isinstance(event.data, ReturnApprovalRequest):
            requests.append(event.data)

    assert calls == [], "nothing is created before the approval"
    (request,) = requests
    assert request.route == "manager"
    assert request.evidence["rule"] == "high-value"
    assert request.evidence["facts"]["days_since_delivery"] == 5
    assert request.policy_version


@pytest.mark.asyncio
async def test_an_engine_that_cannot_decide_falls_back_to_the_threshold(monkeypatch) -> None:
    """No pool and no identity: gather_facts cannot run, so the gate uses RETURN_HITL_THRESHOLD."""
    current_user_email.set("")
    calls: list[str] = []
    workflow = ReturnAndReplaceWorkflow(_stub_tools(calls))

    small = await workflow.execute(WorkflowState(user_email="x", order_id="o", order_total=20.0))
    large = await workflow.execute(WorkflowState(user_email="x", order_id="o", order_total=900.0))

    assert small.hitl_requested is False and large.hitl_requested is True


def test_the_demo_tours_return_pauses_for_a_manager_under_the_default_policy() -> None:
    """The tour's return chip targets tour@example.com's delivered Dyson V15 ($649.99, delivered 5 days ago).

    The default policy routes it to a MANAGER (amount over 500), so the tour pauses for approval as it must,
    and the approval step has to be done as an admin: `support` may not decide a manager route.
    """
    facts = {
        "amount": 649.99,
        "days_since_delivery": 5,
        "loyalty_tier": "bronze",
        "returns_90d": 0,
        "refund_to_spend_ratio": None,
        "account_age_days": 0,
    }

    decision = evaluate("return_approval", facts)

    assert (decision.route, decision.evidence["rule"]) == ("manager", "high-value")


# ── adjust_return_amount: the non-tool the workflow applies after a reviewer's edit ──────────────


async def _return(pool, user_id: uuid.UUID, order_id: str, refund: float) -> str:
    return_id = uuid.uuid4()
    await pool.execute(
        """INSERT INTO returns (id, order_id, user_id, reason, status, refund_method, refund_amount)
           VALUES ($1, $2, $3, 'r', 'requested', 'store_credit', $4)""",
        return_id,
        uuid.UUID(order_id),
        user_id,
        refund,
    )
    return str(return_id)


@pytest.mark.asyncio
async def test_adjust_return_amount_lowers_the_requesters_own_refund(clean_db, monkeypatch) -> None:
    monkeypatch.setattr("shared.db._pool", clean_db, raising=False)
    user_id, order_id = await _customer(clean_db, total=100.0, spend=500.0, account_days=100)
    return_id = await _return(clean_db, user_id, order_id, 100.0)
    current_user_email.set(EMAIL)

    outcome = await adjust_return_amount(return_id, 40.0)

    assert outcome["success"] is True
    assert float(await clean_db.fetchval("SELECT refund_amount FROM returns WHERE id::text = $1", return_id)) == 40.0


@pytest.mark.asyncio
@pytest.mark.parametrize("amount", [100.01, -1.0])
async def test_adjust_return_amount_refuses_a_raise_or_a_negative(clean_db, monkeypatch, amount: float) -> None:
    monkeypatch.setattr("shared.db._pool", clean_db, raising=False)
    user_id, order_id = await _customer(clean_db, total=100.0, spend=500.0, account_days=100)
    return_id = await _return(clean_db, user_id, order_id, 100.0)
    current_user_email.set(EMAIL)

    outcome = await adjust_return_amount(return_id, amount)

    assert outcome["success"] is False
    assert float(await clean_db.fetchval("SELECT refund_amount FROM returns WHERE id::text = $1", return_id)) == 100.0


@pytest.mark.asyncio
async def test_adjust_return_amount_cannot_touch_someone_elses_return_or_run_anonymously(clean_db, monkeypatch) -> None:
    monkeypatch.setattr("shared.db._pool", clean_db, raising=False)
    user_id, order_id = await _customer(clean_db, total=100.0, spend=500.0, account_days=100)
    return_id = await _return(clean_db, user_id, order_id, 100.0)

    current_user_email.set("intruder@example.com")  # identity comes from the ContextVar, never an argument
    assert (await adjust_return_amount(return_id, 10.0))["success"] is False
    current_user_email.set("")
    assert (await adjust_return_amount(return_id, 10.0))["success"] is False
    assert float(await clean_db.fetchval("SELECT refund_amount FROM returns WHERE id::text = $1", return_id)) == 100.0
