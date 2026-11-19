"""D-9a (#95): an approved return waits for the carrier's first scan and survives the wait.

Workflow level: with ``RETURN_LIFECYCLE_ENABLED`` the graph gains ``await-carrier-scan`` before ``finalize``. The run
pauses with the return created and its label issued, persists through the real ``PostgresCheckpointStorage``, and a
NEW workflow object resumes it from the checkpoint id with a ``ScanEvent``. With the flag off nothing changes (the
rest of the return tests are that proof).

No ``from __future__ import annotations``: MAF resolves ``@response_handler`` annotations at import time.
"""

from typing import Any

import pytest

from shared.checkpoint_storage import PostgresCheckpointStorage
from shared.config import settings
from shared.policy import PolicyDecision
from tests.test_return_replace_workflow import _tools
from workflows.return_replace import (
    AwaitCarrierScan,
    ReturnAndReplaceWorkflow,
    ScanEvent,
    WorkflowState,
)


async def _auto(_state: WorkflowState) -> PolicyDecision:
    return PolicyDecision(route="auto", reasons=("auto",), evidence={}, version="t")


@pytest.fixture
def lifecycle_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "RETURN_LIFECYCLE_ENABLED", True)


def _workflow(calls: list | None = None):
    return ReturnAndReplaceWorkflow(_tools(calls), policy_fn=_auto)._build_maf_workflow()


async def _run_to_pause(storage: PostgresCheckpointStorage) -> tuple[WorkflowState, str, AwaitCarrierScan]:
    state = WorkflowState(user_email="a@example.com", order_id="o-1", order_total=120.0, reason="broken")
    final = state
    request_id, request = "", None
    async for event in _workflow().run(state, stream=True, checkpoint_storage=storage):
        if getattr(event, "type", None) == "output" and isinstance(event.data, WorkflowState):
            final = event.data
        if getattr(event, "type", None) == "request_info":
            request_id, request = event.request_id, event.data
    assert isinstance(request, AwaitCarrierScan)
    return final, request_id, request


async def test_an_approved_return_pauses_for_the_scan_after_it_is_created(clean_db, lifecycle_on) -> None:
    storage = PostgresCheckpointStorage(clean_db)

    final, request_id, request = await _run_to_pause(storage)

    assert final.awaiting == "carrier_scan"
    assert "finalize" not in final.completed_steps
    # The return exists (and so does the label) before the wait: the customer can ship it back now.
    assert request.return_id == "ret-99" and final.return_id == "ret-99"
    assert request.timeout_days == settings.RETURN_SCAN_TIMEOUT_DAYS
    assert request_id


async def test_the_scan_resumes_the_run_from_a_fresh_workflow_and_keeps_its_state(clean_db, lifecycle_on) -> None:
    storage = PostgresCheckpointStorage(clean_db)
    _, request_id, _ = await _run_to_pause(storage)
    checkpoint_id = (await storage.list_checkpoint_ids(workflow_name="return-and-replace"))[0]  # newest first

    calls: list[tuple[str, dict[str, Any]]] = []
    final = None
    # A brand-new workflow object: the paused one lived in a process that is long gone.
    async for event in _workflow(calls).run(
        responses={request_id: ScanEvent(received=True, carrier="UPS", tracking="1Z9", scanned_at="2026-11-19")},
        checkpoint_id=checkpoint_id,
        checkpoint_storage=storage,
        stream=True,
    ):
        if getattr(event, "type", None) == "output" and isinstance(event.data, WorkflowState):
            final = event.data

    assert final is not None
    # The scan does not finish the return: it moves on to the inspection wait (D-9b).
    assert "carrier_scan" in final.completed_steps and "finalize" not in final.completed_steps
    assert final.awaiting == "inspection" and not final.errors
    assert final.return_id == "ret-99"  # restored from the checkpoint, not recreated
    assert [n for n, _ in calls] == []  # nothing before the wait ran again
    assert final.scan == {"carrier": "UPS", "tracking": "1Z9", "scanned_at": "2026-11-19"}
    assert [p["id"] for p in final.replacement_products] == ["p-1", "p-2"]


async def test_a_wait_that_expires_ends_the_run_with_an_error_and_no_finalize(clean_db, lifecycle_on) -> None:
    storage = PostgresCheckpointStorage(clean_db)
    _, request_id, _ = await _run_to_pause(storage)
    checkpoint_id = (await storage.list_checkpoint_ids(workflow_name="return-and-replace"))[0]

    final = None
    async for event in _workflow().run(
        responses={request_id: ScanEvent(received=False, note="no scan in 14 days")},
        checkpoint_id=checkpoint_id,
        checkpoint_storage=storage,
        stream=True,
    ):
        if getattr(event, "type", None) == "output" and isinstance(event.data, WorkflowState):
            final = event.data

    assert final is not None
    assert "finalize" not in final.completed_steps
    assert final.errors == ["carrier_scan: no scan in 14 days"]


async def test_with_the_flag_off_the_graph_has_no_wait(clean_db, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "RETURN_LIFECYCLE_ENABLED", False)

    final = await ReturnAndReplaceWorkflow(_tools(), policy_fn=_auto).execute(
        WorkflowState(user_email="a@example.com", order_id="o-1", order_total=120.0)
    )

    assert "finalize" in final.completed_steps and final.awaiting is None
