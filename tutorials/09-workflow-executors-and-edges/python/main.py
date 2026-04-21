"""
MAF v1 — Chapter 09: Workflow Executors and Edges (Python)

Three executors chained via edges, plus one conditional edge that routes
based on the previous executor's output. No LLM — workflows are deterministic
coordinators; the agents come back in Ch11.

Run:
    python tutorials/09-workflow-executors-and-edges/python/main.py "hello"
    python tutorials/09-workflow-executors-and-edges/python/main.py ""   # empty → short-circuit
"""

from __future__ import annotations

import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

from agent_framework._workflows._events import WorkflowEvent  # noqa: E402
from agent_framework._workflows._executor import Executor, handler  # noqa: E402
from agent_framework._workflows._workflow_builder import WorkflowBuilder  # noqa: E402
from agent_framework._workflows._workflow_context import WorkflowContext  # noqa: E402


# ─────────────── Executors ───────────────

class UppercaseExecutor(Executor):
    def __init__(self) -> None:
        super().__init__(id="uppercase")

    @handler
    async def run(self, message: str, ctx: WorkflowContext[str]) -> None:
        await ctx.send_message(message.upper())


class ValidateExecutor(Executor):
    """Routes valid inputs downstream; short-circuits empty inputs to a terminal output."""

    def __init__(self) -> None:
        super().__init__(id="validate")

    @handler
    async def run(self, message: str, ctx: WorkflowContext[str, str]) -> None:
        if not message.strip():
            # Yield a workflow-terminating output; no downstream executor will run.
            await ctx.yield_output("[skipped: empty input]")
            return
        await ctx.send_message(message)


class LogExecutor(Executor):
    def __init__(self) -> None:
        super().__init__(id="log")

    @handler
    async def run(self, message: str, ctx: WorkflowContext[None, str]) -> None:
        await ctx.yield_output(f"LOGGED: {message}")


# ─────────────── Build + run ───────────────

def build_workflow():
    up = UppercaseExecutor()
    validate = ValidateExecutor()
    log = LogExecutor()
    return (
        WorkflowBuilder(start_executor=up)
        .add_edge(up, validate)
        .add_edge(validate, log)
        .build()
    )


async def run(text: str) -> list[object]:
    """Run the workflow and return the list of yielded workflow outputs."""
    workflow = build_workflow()
    outputs: list[object] = []
    async for event in workflow.run(text, stream=True):
        # WorkflowEvent is a tagged union; filter by its `type` field.
        if getattr(event, "type", None) == "output":
            outputs.append(getattr(event, "data", None))
    return outputs


async def main() -> None:
    text = sys.argv[1] if len(sys.argv) > 1 else "hello world"
    print(f"input: {text!r}")
    for output in await run(text):
        print(f"output: {output!r}")


if __name__ == "__main__":
    asyncio.run(main())
