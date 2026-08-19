"""
MAF v1 — Chapter 10: Workflow Events and Builder (Python)

Extend the Ch09 pipeline with a *custom* progress event. Each executor yields
ProgressPayload('executor-id', percent) via ctx.yield_output() so callers can
show a live progress indicator while the workflow runs, distinct from the
pipeline's final result.

Progress vs. final output is a build-time designation, not a per-call choice:
every yield_output() call from a given executor carries the same event type,
fixed by whether that executor is listed under WorkflowBuilder's
intermediate_output_from (progress-shaped) or output_from (final-result-shaped).
That's why `up` and `validate` only ever yield ProgressPayload, and `log` is the
sole executor that yields the pipeline's actual result — the earlier
`WorkflowEvent.emit()` API let one executor mix both freely, but that API is
deprecated in favor of this explicit split (see the module docstring on
`agent_framework._workflows._events.WorkflowEvent.emit`).

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
    async def run(self, message: str, ctx: WorkflowContext[str, ProgressPayload]) -> None:
        await ctx.yield_output(ProgressPayload("uppercase", 33))
        await ctx.send_message(message.upper())


class ValidateExecutor(Executor):
    def __init__(self) -> None:
        super().__init__(id="validate")

    @handler
    async def run(self, message: str, ctx: WorkflowContext[str, ProgressPayload | str]) -> None:
        await ctx.yield_output(ProgressPayload("validate", 66))
        if not message.strip():
            # Short-circuits before log ever runs. Because validate is
            # intermediate-designated (see intermediate_output_from below),
            # this yield carries the same event type as its progress payload
            # above — that's fine here, since callers tell progress from
            # results by payload shape (ProgressPayload vs. plain str), not
            # by the workflow's output/intermediate label. See main_test.py.
            await ctx.yield_output("[skipped: empty input]")
            return
        await ctx.send_message(message)


class LogExecutor(Executor):
    def __init__(self) -> None:
        super().__init__(id="log")

    @handler
    async def run(self, message: str, ctx: WorkflowContext[None, ProgressPayload | str]) -> None:
        await ctx.yield_output(ProgressPayload("log", 100))
        await ctx.yield_output(f"LOGGED: {message}")


# ─────────────── Build + run ───────────────

def build_workflow():
    up = UppercaseExecutor()
    validate = ValidateExecutor()
    log = LogExecutor()
    return (
        WorkflowBuilder(
            start_executor=up,
            intermediate_output_from=[up, validate],
            output_from=[log],
        )
        .add_edge(up, validate)
        .add_edge(validate, log)
        .build()
    )


async def run_with_events(text: str) -> tuple[list[ProgressPayload], list[object]]:
    """Run the workflow and return (progress events, final outputs).

    Bucketed by payload shape (isinstance ProgressPayload), not by the
    workflow's own type='output' / type='intermediate' label — validate's
    early-exit "[skipped: empty input]" yield shares its executor's
    intermediate designation (see ValidateExecutor), so the type label alone
    can't tell progress from a result here. The payload shape can.
    """
    workflow = build_workflow()
    progress: list[ProgressPayload] = []
    outputs: list[object] = []
    async for event in workflow.run(text, stream=True):
        etype = getattr(event, "type", None)
        if etype not in ("output", "intermediate"):
            continue
        data = getattr(event, "data", None)
        if isinstance(data, ProgressPayload):
            progress.append(data)
        else:
            outputs.append(data)
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
