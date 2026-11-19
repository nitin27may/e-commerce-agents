"""D-9a (#95): the carrier-scan wait, end to end through the real outbox worker.

A return is approved, created, paused at ``await-carrier-scan`` and persisted (``hitl_requests`` + a checkpoint + an
expiry timer). A ``return.carrier_scanned`` event then resumes it through ``resume_workflow`` as the requester, and the
timer expires it if no scan comes. Includes the kill-and-restart test: the resume runs in a *separate Python
interpreter* that shares nothing with the one that paused.

No ``from __future__ import annotations``: this module builds a MAF workflow whose response handlers resolve
annotations at import time.
"""

import json
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

import asyncpg
import pytest

from orchestrator.modes.workflow_mode import _scan_wait
from shared import db as shared_db
from shared.checkpoint_storage import PostgresCheckpointStorage
from shared.config import settings
from shared.policy import PolicyDecision
from shared.return_lifecycle import (
    TOPIC_CARRIER_SCANNED,
    TOPIC_SCAN_TIMEOUT,
    open_scan_wait,
    request_scan,
)
from tests.test_return_replace_workflow import _tools
from worker.return_handlers import HANDLERS
from worker.runner import run_once
from workflows.return_replace import AwaitCarrierScan, ReturnAndReplaceWorkflow, WorkflowState

pytestmark = pytest.mark.integration

CUSTOMER = "alice@example.com"
TICK: dict[str, Any] = {
    "batch_size": 20,
    "lease_seconds": 60,
    "max_attempts": 3,
    "backoff_base": 0.0,
    "backoff_cap": 0.0,
}
PYTHON_DIR = Path(__file__).resolve().parent.parent


async def _auto(_state: WorkflowState) -> PolicyDecision:
    return PolicyDecision(route="auto", reasons=("auto",), evidence={}, version="t")


@pytest.fixture(autouse=True)
def _lifecycle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "RETURN_LIFECYCLE_ENABLED", True)


@pytest.fixture
def pool_bound(clean_db: asyncpg.Pool):
    """The worker and the resume machinery read the process-wide pool, as they do in production."""
    shared_db.use_pool(clean_db)
    yield clean_db
    shared_db.use_pool(None)


async def _paused_return(pool: asyncpg.Pool) -> dict[str, Any]:
    """Create a customer, order and return, run the workflow to the scan wait, and open the wait rows."""
    user_id = await pool.fetchval(
        "INSERT INTO users (email, password_hash, name, role) VALUES ($1, 'h', 'Alice', 'customer') RETURNING id",
        CUSTOMER,
    )
    order_id = await pool.fetchval(
        "INSERT INTO orders (user_id, status, total, shipping_address) VALUES ($1, 'delivered', 120, '{}'::jsonb) "
        "RETURNING id",
        user_id,
    )
    return_id = str(
        await pool.fetchval(
            "INSERT INTO returns (order_id, user_id, reason, status, refund_amount) "
            "VALUES ($1, $2, 'broken', 'requested', 120) RETURNING id",
            order_id,
            user_id,
        )
    )
    run_id = await pool.fetchval(
        "INSERT INTO usage_logs (user_id, agent_name, input_summary, status) "
        "VALUES ($1, 'orchestrator', 'return', 'success') RETURNING id",
        user_id,
    )

    async def initiate(order_id: str, reason: str, refund_method: str) -> dict[str, Any]:
        return {"return_id": return_id, "refund_amount": 120.0, "return_label_url": f"/api/returns/{return_id}/label"}

    storage = PostgresCheckpointStorage(pool)
    workflow = ReturnAndReplaceWorkflow(_tools(initiate_return=initiate), policy_fn=_auto)._build_maf_workflow()
    state = WorkflowState(user_email=CUSTOMER, order_id=str(order_id), order_total=120.0, reason="broken")
    final, request_id, request = state, "", None
    async for event in workflow.run(state, stream=True, checkpoint_storage=storage):
        if getattr(event, "type", None) == "output" and isinstance(event.data, WorkflowState):
            final = event.data
        if getattr(event, "type", None) == "request_info":
            request_id, request = event.request_id, event.data
    assert isinstance(request, AwaitCarrierScan)
    checkpoint_id = (await storage.list_checkpoint_ids(workflow_name="return-and-replace"))[0]  # newest first
    wait = _scan_wait(final, request_id, request)
    assert wait is not None
    hitl_id = await open_scan_wait(
        pool, workflow_run_id=run_id, user_email=CUSTOMER, wait=wait, checkpoint_id=checkpoint_id
    )
    return {
        "return_id": return_id,
        "hitl_id": hitl_id,
        "request_id": request_id,
        "checkpoint_id": checkpoint_id,
        "run_id": run_id,
    }


async def _tick(pool: asyncpg.Pool) -> Any:
    return await run_once(pool, HANDLERS, "w1", **TICK)


async def _make_due(pool: asyncpg.Pool, topic: str) -> None:
    await pool.execute(
        "UPDATE outbox SET available_at = NOW() - interval '1 minute' WHERE topic = $1 AND processed_at IS NULL", topic
    )


async def _notes(pool: asyncpg.Pool) -> list[str]:
    return [r["kind"] for r in await pool.fetch("SELECT kind FROM notifications ORDER BY created_at")]


async def test_a_paused_return_has_a_wait_a_timer_and_tells_the_customer(pool_bound) -> None:
    pool = pool_bound

    p = await _paused_return(pool)

    wait = await pool.fetchrow("SELECT * FROM hitl_requests WHERE id = $1", p["hitl_id"])
    assert wait["kind"] == "carrier_scan" and wait["status"] == "pending" and wait["user_email"] == CUSTOMER
    assert json.loads(wait["payload"])["return_id"] == p["return_id"]
    timer = await pool.fetchrow("SELECT * FROM outbox WHERE topic = $1", TOPIC_SCAN_TIMEOUT)
    days = await pool.fetchval("SELECT EXTRACT(EPOCH FROM ($1::timestamptz - NOW())) / 86400", timer["available_at"])
    assert settings.RETURN_SCAN_TIMEOUT_DAYS - 1 < days <= settings.RETURN_SCAN_TIMEOUT_DAYS
    assert await _notes(pool) == ["return.label_ready"]


async def test_opening_the_same_wait_twice_adds_nothing(pool_bound) -> None:
    pool = pool_bound
    p = await _paused_return(pool)
    wait = _scan_wait(
        WorkflowState(user_email=CUSTOMER, order_id="o", awaiting="carrier_scan", return_id=p["return_id"]),
        p["request_id"],
        None,
    )

    again = await open_scan_wait(
        pool, workflow_run_id=p["run_id"], user_email=CUSTOMER, wait=wait, checkpoint_id=p["checkpoint_id"]
    )

    assert again == p["hitl_id"]
    assert await pool.fetchval("SELECT count(*) FROM hitl_requests") == 1
    assert await pool.fetchval("SELECT count(*) FROM outbox WHERE topic = $1", TOPIC_SCAN_TIMEOUT) == 1


async def test_a_carrier_scan_resumes_the_return_and_marks_it_shipped_back(pool_bound) -> None:
    pool = pool_bound
    p = await _paused_return(pool)

    assert await request_scan(pool, p["return_id"], carrier="UPS", tracking="1Z9") is not None
    result = await _tick(pool)

    assert (result.claimed, result.done, result.failed) == (1, 1, 0)
    wait = await pool.fetchrow("SELECT status, response FROM hitl_requests WHERE id = $1", p["hitl_id"])
    assert wait["status"] == "completed" and json.loads(wait["response"])["carrier"] == "UPS"
    ret = await pool.fetchrow("SELECT status, scanned_at FROM returns WHERE id = $1::uuid", p["return_id"])
    assert ret["status"] == "shipped_back" and ret["scanned_at"] is not None
    assert await _notes(pool) == ["return.label_ready", "return.carrier_scanned"]


async def test_a_second_scan_for_the_same_return_is_ignored(pool_bound) -> None:
    pool = pool_bound
    p = await _paused_return(pool)
    assert await request_scan(pool, p["return_id"]) is not None
    await _tick(pool)

    with pytest.raises(LookupError):  # the wait is answered: nothing is waiting for a scan any more
        await request_scan(pool, p["return_id"])


async def test_a_return_that_is_not_waiting_cannot_be_scanned(pool_bound) -> None:
    with pytest.raises(LookupError):
        await request_scan(pool_bound, str(uuid.uuid4()))


async def test_the_timer_expires_a_return_nobody_scanned(pool_bound) -> None:
    pool = pool_bound
    p = await _paused_return(pool)

    await _make_due(pool, TOPIC_SCAN_TIMEOUT)
    result = await _tick(pool)

    assert (result.claimed, result.done, result.failed) == (1, 1, 0)
    assert await pool.fetchval("SELECT status FROM hitl_requests WHERE id = $1", p["hitl_id"]) == "timeout"
    assert await pool.fetchval("SELECT status FROM returns WHERE id = $1::uuid", p["return_id"]) == "expired"
    assert await _notes(pool) == ["return.label_ready", "return.expired"]


async def test_a_timer_that_fires_after_the_scan_changes_nothing(pool_bound) -> None:
    pool = pool_bound
    p = await _paused_return(pool)
    await request_scan(pool, p["return_id"])
    await _tick(pool)

    await _make_due(pool, TOPIC_SCAN_TIMEOUT)
    result = await _tick(pool)

    assert (result.claimed, result.failed) == (1, 0)  # consumed, not retried
    assert await pool.fetchval("SELECT status FROM hitl_requests WHERE id = $1", p["hitl_id"]) == "completed"
    assert await pool.fetchval("SELECT status FROM returns WHERE id = $1::uuid", p["return_id"]) == "shipped_back"
    assert "return.expired" not in await _notes(pool)


async def test_with_the_lifecycle_off_a_scan_is_consumed_and_does_nothing(
    pool_bound, monkeypatch: pytest.MonkeyPatch
) -> None:
    pool = pool_bound
    p = await _paused_return(pool)
    await request_scan(pool, p["return_id"])
    monkeypatch.setattr(settings, "RETURN_LIFECYCLE_ENABLED", False)

    result = await _tick(pool)

    assert (result.claimed, result.done, result.failed) == (1, 1, 0)
    assert await pool.fetchval("SELECT status FROM hitl_requests WHERE id = $1", p["hitl_id"]) == "pending"
    assert await pool.fetchval("SELECT status FROM returns WHERE id = $1::uuid", p["return_id"]) == "requested"


async def test_a_return_approval_cannot_be_answered_with_a_scan_and_a_scan_wait_not_with_a_decision(
    pool_bound,
) -> None:
    """The staff routes resume only an approval gate; the worker only a scan wait. Neither crosses over."""
    from orchestrator.approvals_service import ApprovalHTTPError, resume_workflow
    from workflows.return_replace import ReturnDecision

    p = await _paused_return(pool_bound)

    with pytest.raises(ApprovalHTTPError) as err:  # the default kind is the approval gate
        await resume_workflow(p["hitl_id"], ReturnDecision(decision="approve"), reviewer_email="staff@example.com")

    assert err.value.status_code == 400
    # The refusal released the claim, so the real answer still works.
    await request_scan(pool_bound, p["return_id"])
    assert (await _tick(pool_bound)).done == 1


async def test_kill_and_restart_a_fresh_interpreter_resumes_the_waiting_return(pool_bound, database_url: str) -> None:
    """Pause here, drop every object, and resume in a new process that has only the database to go on."""
    pool = pool_bound
    p = await _paused_return(pool)
    script = (
        "import asyncio, json, sys\n"
        "import asyncpg\n"
        "from shared.checkpoint_storage import PostgresCheckpointStorage\n"
        "from shared.config import settings\n"
        "settings.RETURN_LIFECYCLE_ENABLED = True\n"
        "from workflows.return_replace import ReturnAndReplaceWorkflow, ScanEvent, WorkflowState\n"
        "async def main(url, request_id, checkpoint_id):\n"
        "    pool = await asyncpg.create_pool(url, min_size=1, max_size=2)\n"
        "    wf = ReturnAndReplaceWorkflow({})._build_maf_workflow()\n"
        "    final = None\n"
        "    async for e in wf.run(responses={request_id: ScanEvent(received=True, carrier='UPS')},\n"
        "                          checkpoint_id=checkpoint_id, checkpoint_storage=PostgresCheckpointStorage(pool),\n"
        "                          stream=True):\n"
        "        if getattr(e, 'type', None) == 'output' and isinstance(e.data, WorkflowState):\n"
        "            final = e.data\n"
        "    await pool.close()\n"
        "    print(json.dumps({'steps': final.completed_steps, 'return_id': final.return_id,\n"
        "                      'errors': final.errors, 'scan': final.scan}))\n"
        "asyncio.run(main(*sys.argv[1:4]))\n"
    )

    out = subprocess.run(  # noqa: S603 - fixed argv, our own interpreter and script
        [sys.executable, "-c", script, database_url, p["request_id"], str(p["checkpoint_id"])],
        cwd=PYTHON_DIR,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert out.returncode == 0, out.stderr[-2000:]
    result = json.loads(out.stdout.strip().splitlines()[-1])
    assert result["errors"] == []
    assert "carrier_scan" in result["steps"] and "finalize" in result["steps"]
    assert result["return_id"] == p["return_id"]  # the return created before the pause, restored from Postgres
    assert result["scan"]["carrier"] == "UPS"


# ── the admin simulator route ────────────────────────────────────────────────────────────────────────────────


def _simulate_url(return_id: str) -> str:
    return f"/api/admin/returns/{return_id}/simulate-scan"


async def test_an_admin_can_simulate_a_scan_and_the_worker_resumes_the_return(pool_bound) -> None:
    from tests.test_proposal_execute import _as, _call

    p = await _paused_return(pool_bound)

    res = await _call(_as("admin@example.com", "admin"), "POST", _simulate_url(p["return_id"]), {"carrier": "UPS"})

    assert res.status_code == 200 and res.json()["queued"] is True
    again = await _call(_as("admin@example.com", "admin"), "POST", _simulate_url(p["return_id"]), {})
    assert again.json()["queued"] is False  # the same scan twice is one event
    assert (await _tick(pool_bound)).done == 1
    assert await pool_bound.fetchval("SELECT status FROM returns WHERE id = $1::uuid", p["return_id"]) == "shipped_back"


async def test_the_simulator_is_admin_only_and_404_when_the_lifecycle_is_off(
    pool_bound, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.test_proposal_execute import _as, _call

    p = await _paused_return(pool_bound)

    customer = await _call(_as(CUSTOMER, "customer"), "POST", _simulate_url(p["return_id"]), {})
    assert customer.status_code == 403
    monkeypatch.setattr(settings, "RETURN_LIFECYCLE_ENABLED", False)
    off = await _call(_as("admin@example.com", "admin"), "POST", _simulate_url(p["return_id"]), {})
    assert off.status_code == 404
    # Off is 404 for everyone, so a 403 cannot reveal that the route exists.
    assert (await _call(_as(CUSTOMER, "customer"), "POST", _simulate_url(p["return_id"]), {})).status_code == 404
    assert await pool_bound.fetchval("SELECT count(*) FROM outbox WHERE topic = $1", TOPIC_CARRIER_SCANNED) == 0


async def test_the_simulator_refuses_a_return_that_is_not_waiting_and_a_malformed_id(pool_bound) -> None:
    from tests.test_proposal_execute import _as, _call

    admin = _as("admin@example.com", "admin")

    assert (await _call(admin, "POST", _simulate_url(str(uuid.uuid4())), {})).status_code == 409
    assert (await _call(admin, "POST", _simulate_url("not-a-uuid"), {})).status_code == 404


def test_the_two_topics_are_not_the_same() -> None:
    assert TOPIC_CARRIER_SCANNED != TOPIC_SCAN_TIMEOUT


async def test_the_runs_api_reports_a_scan_wait_as_a_carrier_scan_not_an_approval(pool_bound) -> None:
    from httpx import ASGITransport, AsyncClient

    from tests.test_orchestration_resume import _app_for

    p = await _paused_return(pool_bound)
    user_id = await pool_bound.fetchval("SELECT id FROM users WHERE email = $1", CUSTOMER)

    async with AsyncClient(transport=ASGITransport(app=_app_for(CUSTOMER, user_id)), base_url="http://test") as http:
        resp = await http.get(f"/api/runs/{p['run_id']}/checkpoints", headers={"Authorization": "Bearer fake"})

    assert resp.status_code == 200
    hitl = resp.json()["hitl_request"]
    assert hitl["kind"] == "carrier_scan" and hitl["status"] == "pending"
    assert hitl["payload"]["return_id"] == p["return_id"]


def test_the_lifecycle_is_off_by_default() -> None:
    from shared.config import Settings

    fresh = Settings(_env_file=None)

    assert fresh.RETURN_LIFECYCLE_ENABLED is False
    assert fresh.RETURN_SCAN_TIMEOUT_DAYS == 14 and fresh.RETURN_INSPECTION_TIMEOUT_DAYS == 7


async def test_migration_0013_adds_the_return_columns_and_the_wait_index(clean_db: asyncpg.Pool) -> None:
    cols = {
        r["column_name"]
        for r in await clean_db.fetch("SELECT column_name FROM information_schema.columns WHERE table_name = 'returns'")
    }
    index = await clean_db.fetchval("SELECT 1 FROM pg_indexes WHERE indexname = 'idx_hitl_return_wait'")

    assert {"approval_id", "resolution", "scanned_at", "inspection"} <= cols
    assert index == 1
