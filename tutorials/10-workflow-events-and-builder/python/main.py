"""
MAF v1 — Chapter 10: Workflow Events and Builder (Python)

Extend the Ch09 pipeline with a *custom* progress event. Each executor emits
ProgressPayload('executor-id', percent) via WorkflowEvent.emit so callers can
show a live progress indicator while the workflow runs.

Run:
    python tutorials/10-workflow-events-and-builder/python/main.py "hello world"
"""

from __future__ import annotations

import asyncio
import pathlib
import sys
from dataclasses import dataclass

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

from agent_framework._workflows._events import WorkflowEvent  # noqa: E402
from agent_framework._workflows._executor import Executor, handler  # noqa: E402
from agent_framework._workflows._workflow_builder import WorkflowBuilder  # noqa: E402
from agent_framework._workflows._workflow_context import WorkflowContext  # noqa: E402


# ─────────────── Custom event payload ───────────────

@dataclass(frozen=True)
class ProgressPayload:
    step: str
    percent: int


# ─────────────── Executors ───────────────

class UppercaseExecutor(Executor):
    def __init__(self) -> None:
        super().__init__(id="uppercase")

    @handler
    async def run(self, message: str, ctx: WorkflowContext[str]) -> None:
        await ctx.add_event(WorkflowEvent.emit("uppercase", ProgressPayload("uppercase", 33)))
        await ctx.send_message(message.upper())


class ValidateExecutor(Executor):
    def __init__(self) -> None:
        super().__init__(id="validate")

    @handler
    async def run(self, message: str, ctx: WorkflowContext[str, str]) -> None:
        await ctx.add_event(WorkflowEvent.emit("validate", ProgressPayload("validate", 66)))
        if not message.strip():
            await ctx.yield_output("[skipped: empty input]")
            return
        await ctx.send_message(message)


class LogExecutor(Executor):
    def __init__(self) -> None:
        super().__init__(id="log")

    @handler
    async def run(self, message: str, ctx: WorkflowContext[None, str]) -> None:
        await ctx.add_event(WorkflowEvent.emit("log", ProgressPayload("log", 100)))
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


async def run_with_events(text: str) -> tuple[list[ProgressPayload], list[object]]:
    """Run the workflow and return (progress events, final outputs)."""
    workflow = build_workflow()
    progress: list[ProgressPayload] = []
    outputs: list[object] = []
    async for event in workflow.run(text, stream=True):
        etype = getattr(event, "type", None)
        if etype == "data" and isinstance(getattr(event, "data", None), ProgressPayload):
            progress.append(event.data)
        elif etype == "output":
            outputs.append(getattr(event, "data", None))
    return progress, outputs


async def main() -> None:
    text = sys.argv[1] if len(sys.argv) > 1 else "hello world"
    print(f"input: {text!r}")
    progress, outputs = await run_with_events(text)
    for p in progress:
        print(f"  progress: {p.step} → {p.percent}%")
    for output in outputs:
        print(f"output: {output!r}")


if __name__ == "__main__":
    asyncio.run(main())
