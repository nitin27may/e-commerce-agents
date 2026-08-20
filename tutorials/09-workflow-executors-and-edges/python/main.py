"""
MAF v1 — Chapter 09: Workflow Executors and Edges (Python)

Three executors chained via edges, plus one conditional edge that routes
based on the previous executor's output. No LLM — workflows are deterministic
coordinators; the agents come back in Ch11.

Run:
    python tutorials/09-workflow-executors-and-edges/python/main.py "ord-8842"
    python tutorials/09-workflow-executors-and-edges/python/main.py ""   # empty → short-circuit
"""

from __future__ import annotations

import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

from agent_framework._workflows._executor import Executor, handler  # noqa: E402
from agent_framework._workflows._workflow_builder import WorkflowBuilder  # noqa: E402
from agent_framework._workflows._workflow_context import WorkflowContext  # noqa: E402

# ─────────────── Executors ───────────────

class NormalizeOrderExecutor(Executor):
    def __init__(self) -> None:
        super().__init__(id="normalize-order")

    @handler
    async def run(self, order_id: str, ctx: WorkflowContext[str]) -> None:
        await ctx.send_message(order_id.strip().upper())


class ValidateOrderExecutor(Executor):
    """Routes valid order ids downstream; short-circuits empty ids to a terminal output."""

    def __init__(self) -> None:
        super().__init__(id="validate-order")

    @handler
    async def run(self, order_id: str, ctx: WorkflowContext[str, str]) -> None:
        if not order_id:
            # Yield a workflow-terminating output; no downstream executor will run.
            await ctx.yield_output("[rejected: empty order id]")
            return
        await ctx.send_message(order_id)


class LogOrderExecutor(Executor):
    def __init__(self) -> None:
        super().__init__(id="log-order")

    @handler
    async def run(self, order_id: str, ctx: WorkflowContext[None, str]) -> None:
        await ctx.yield_output(f"ORDER LOGGED: {order_id}")


# ─────────────── Build + run ───────────────

def build_workflow():
    normalize = NormalizeOrderExecutor()
    validate = ValidateOrderExecutor()
    log = LogOrderExecutor()
    return (
        WorkflowBuilder(start_executor=normalize)
        .add_edge(normalize, validate)
        .add_edge(validate, log)
        .build()
    )


async def run(order_id: str) -> list[object]:
    """Run the workflow and return the list of yielded workflow outputs."""
    workflow = build_workflow()
    outputs: list[object] = []
    async for event in workflow.run(order_id, stream=True):
        # WorkflowEvent is a tagged union; filter by its `type` field.
        if getattr(event, "type", None) == "output":
            outputs.append(getattr(event, "data", None))
    return outputs


async def main() -> None:
    order_id = sys.argv[1] if len(sys.argv) > 1 else "ord-8842"
    print(f"input: {order_id!r}")
    for output in await run(order_id):
        print(f"output: {output!r}")


if __name__ == "__main__":
    asyncio.run(main())
