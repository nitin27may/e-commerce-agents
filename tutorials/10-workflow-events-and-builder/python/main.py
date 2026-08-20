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
That's why `normalize` and `validate` only ever yield ProgressPayload, and `log`
is the sole executor that yields the pipeline's actual result — the earlier
`WorkflowEvent.emit()` API let one executor mix both freely, but that API is
deprecated in favor of this explicit split (see the module docstring on
`agent_framework._workflows._events.WorkflowEvent.emit`).

Run:
    python tutorials/10-workflow-events-and-builder/python/main.py "ord-8842"
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

class NormalizeOrderExecutor(Executor):
    def __init__(self) -> None:
        super().__init__(id="normalize-order")

    @handler
    async def run(self, order_id: str, ctx: WorkflowContext[str, ProgressPayload]) -> None:
        await ctx.yield_output(ProgressPayload("normalize-order", 33))
        await ctx.send_message(order_id.strip().upper())


class ValidateOrderExecutor(Executor):
    def __init__(self) -> None:
        super().__init__(id="validate-order")

    @handler
    async def run(self, order_id: str, ctx: WorkflowContext[str, ProgressPayload | str]) -> None:
        await ctx.yield_output(ProgressPayload("validate-order", 66))
        if not order_id:
            # Short-circuits before log ever runs. Because validate is
            # intermediate-designated (see intermediate_output_from below),
            # this yield carries the same event type as its progress payload
            # above — that's fine here, since callers tell progress from
            # results by payload shape (ProgressPayload vs. plain str), not
            # by the workflow's output/intermediate label. See main_test.py.
            await ctx.yield_output("[rejected: empty order id]")
            return
        await ctx.send_message(order_id)


class LogOrderExecutor(Executor):
    def __init__(self) -> None:
        super().__init__(id="log-order")

    @handler
    async def run(self, order_id: str, ctx: WorkflowContext[None, ProgressPayload | str]) -> None:
        await ctx.yield_output(ProgressPayload("log-order", 100))
        await ctx.yield_output(f"ORDER LOGGED: {order_id}")


# ─────────────── Build + run ───────────────

def build_workflow():
    normalize = NormalizeOrderExecutor()
    validate = ValidateOrderExecutor()
    log = LogOrderExecutor()
    return (
        WorkflowBuilder(
            start_executor=normalize,
            intermediate_output_from=[normalize, validate],
            output_from=[log],
        )
        .add_edge(normalize, validate)
        .add_edge(validate, log)
        .build()
    )


async def run_with_events(order_id: str) -> tuple[list[ProgressPayload], list[object]]:
    """Run the workflow and return (progress events, final outputs).

    Bucketed by payload shape (isinstance ProgressPayload), not by the
    workflow's own type='output' / type='intermediate' label — validate's
    early-exit "[rejected: empty order id]" yield shares its executor's
    intermediate designation (see ValidateOrderExecutor), so the type label
    alone can't tell progress from a result here. The payload shape can.
    """
    workflow = build_workflow()
    progress: list[ProgressPayload] = []
    outputs: list[object] = []
    async for event in workflow.run(order_id, stream=True):
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
    order_id = sys.argv[1] if len(sys.argv) > 1 else "ord-8842"
    print(f"input: {order_id!r}")
    progress, outputs = await run_with_events(order_id)
    for p in progress:
        print(f"  progress: {p.step} → {p.percent}%")
    for output in outputs:
        print(f"output: {output!r}")


if __name__ == "__main__":
    asyncio.run(main())
