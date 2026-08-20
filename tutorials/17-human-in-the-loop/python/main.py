"""
MAF v1 — Chapter 17: Human-in-the-Loop (Python)

A workflow that pauses mid-execution to ask a human for input, then
resumes with the response. Domain: refund approval — an executor holds a
proposed refund and pauses via request_info to ask a human approver to
approve or deny it before the refund takes effect.

Run interactively:
    python tutorials/17-human-in-the-loop/python/main.py
"""

import asyncio
import pathlib
import sys
from dataclasses import dataclass

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

from agent_framework._workflows._executor import Executor, handler  # noqa: E402
from agent_framework._workflows._request_info_mixin import response_handler  # noqa: E402
from agent_framework._workflows._workflow_builder import WorkflowBuilder  # noqa: E402
from agent_framework._workflows._workflow_context import WorkflowContext  # noqa: E402

# ─────────────── Request / response shapes ───────────────

@dataclass(frozen=True)
class RefundApprovalRequest:
    """A refund awaiting a human decision.

    Doubles as both the workflow's kick-off input (order_id + amount the
    customer wants refunded) and the payload the workflow hands to the
    caller when it pauses — there's nothing extra to derive along the way.
    """
    order_id: str
    amount: float


# ─────────────── Executors ───────────────

class RefundApprovalGate(Executor):
    """
    Receives a proposed refund. On each run it pauses via request_info to
    ask a human approver to approve or deny it. When the decision arrives
    it yields the outcome.
    """

    def __init__(self) -> None:
        super().__init__(id="refund-approval-gate")

    @handler
    async def start(self, refund: RefundApprovalRequest, ctx: WorkflowContext[None, str]) -> None:
        # Pause the workflow and wait for a human approve/deny decision.
        # The `bool` type tells MAF what shape to expect in the response.
        await ctx.request_info(request_data=refund, response_type=bool)

    @response_handler
    async def check(
        self,
        request: RefundApprovalRequest,
        approved: bool,
        ctx: WorkflowContext[None, str],
    ) -> None:
        if approved:
            await ctx.yield_output(f"refund approved for order {request.order_id}: ${request.amount:.2f}")
        else:
            await ctx.yield_output(f"refund denied for order {request.order_id}")


def build_workflow():
    gate = RefundApprovalGate()
    return WorkflowBuilder(start_executor=gate).build()


# ─────────────── Drivers ───────────────

async def run_with_response(order_id: str, amount: float, approved: bool) -> str:
    """Run once and feed a canned approval decision when the workflow pauses."""
    workflow = build_workflow()

    # First run — workflow pauses on request_info. Consume the full stream so
    # the workflow's internal run state is cleanly idle before we resume.
    pending_request_id: str | None = None
    async for event in workflow.run(RefundApprovalRequest(order_id=order_id, amount=amount), stream=True):
        if pending_request_id is None and getattr(event, "type", None) == "request_info":
            pending_request_id = getattr(event, "request_id", None)

    assert pending_request_id, "expected a request_info event to pause the workflow"

    # Resume with the canned decision. Run returns more events until completion.
    outputs: list[str] = []
    async for event in workflow.run(
        responses={pending_request_id: approved},
        stream=True,
    ):
        if getattr(event, "type", None) == "output":
            data = getattr(event, "data", None)
            if isinstance(data, str):
                outputs.append(data)
    return outputs[-1] if outputs else ""


async def main() -> None:
    order_id = "ord-482"
    amount = 245.50
    workflow = build_workflow()

    pending_request_id: str | None = None
    request_data: RefundApprovalRequest | None = None
    async for event in workflow.run(RefundApprovalRequest(order_id=order_id, amount=amount), stream=True):
        if getattr(event, "type", None) == "request_info":
            pending_request_id = getattr(event, "request_id", None)
            request_data = getattr(event, "data", None)
            break

    if not pending_request_id or request_data is None:
        print("Workflow finished without a pause — unexpected.")
        return

    prompt = f"Approve refund of ${request_data.amount:.2f} for order {request_data.order_id}? [y/n]: "
    answer = input(prompt).strip().lower()
    approved = answer in ("y", "yes")

    async for event in workflow.run(
        responses={pending_request_id: approved},
        stream=True,
    ):
        if getattr(event, "type", None) == "output":
            data = getattr(event, "data", None)
            if isinstance(data, str):
                print(data)


if __name__ == "__main__":
    asyncio.run(main())
