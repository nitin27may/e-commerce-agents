"""Spike (D-8): a dataclass response type survives MAF's checkpoint encoder and a fresh-workflow resume.

Pause with ``request_info`` carrying a nested dataclass, persist through the app's real
``PostgresCheckpointStorage`` (MAF's JSON+pickle encoder, a real Postgres row), then build a NEW workflow
object and resume from the checkpoint id with a dataclass response.
No ``from __future__ import annotations``: ``@response_handler`` resolves annotations at import time.
"""

from dataclasses import dataclass, field

import pytest
from agent_framework import Executor, WorkflowBuilder, WorkflowContext, handler, response_handler

from shared.checkpoint_storage import PostgresCheckpointStorage


@dataclass
class _State:
    order_id: str
    replacement_products: list = field(default_factory=list)
    errors: list = field(default_factory=list)


@dataclass
class _Request:
    state: _State
    route: str = "support"


@dataclass
class _Decision:
    decision: str = "approve"
    resolution: str = "refund"
    amount: float | None = None
    note: str = ""
    decided_by: str = ""
    approval_id: str = ""


class _Gate(Executor):
    def __init__(self) -> None:
        super().__init__(id="gate")

    @handler
    async def run(self, state: _State, ctx: WorkflowContext[_State, dict]) -> None:
        await ctx.request_info(_Request(state=state), response_type=_Decision)

    @response_handler(request=_Request, response=_Decision)
    async def on_decision(self, original_request, response, ctx: WorkflowContext[_State, dict]) -> None:
        # What the resumed run actually sees: the types, not just the values.
        seen = {
            "request_type": type(original_request).__name__,
            "state_type": type(original_request.state).__name__,
            "response_type": type(response).__name__,
            "amount": response.amount,
            "products": list(original_request.state.replacement_products),
        }
        await ctx.yield_output(seen)


def _build():
    return WorkflowBuilder(start_executor=_Gate(), name="spike").build()


@pytest.mark.asyncio
async def test_dataclass_decision_round_trips_through_the_encoder_and_a_fresh_workflow(clean_db) -> None:
    storage = PostgresCheckpointStorage(clean_db)
    request_id = None
    async for event in _build().run(_State("o1", [{"id": "p1"}]), stream=True, checkpoint_storage=storage):
        if getattr(event, "type", None) == "request_info":
            request_id = event.request_id
    checkpoint_id = (await storage.list_checkpoint_ids(workflow_name="spike"))[0]  # newest first

    outputs = []
    decision = _Decision("approve", "store_credit", 42.5, decided_by="staff", approval_id="a1")
    async for event in _build().run(
        responses={request_id: decision}, checkpoint_id=checkpoint_id, checkpoint_storage=storage, stream=True
    ):
        if getattr(event, "type", None) == "output":
            outputs.append(event.data)

    assert outputs == [
        {
            "request_type": "_Request",
            "state_type": "_State",
            "response_type": "_Decision",
            "amount": 42.5,
            "products": [{"id": "p1"}],
        }
    ]
