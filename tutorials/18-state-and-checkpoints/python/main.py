"""
MAF v1 — Chapter 18: State and Checkpoints (Python)

A counter executor keeps state across its handler invocations and supports
checkpoint save/restore. FileCheckpointStorage writes each superstep's
state to disk so the workflow can resume after a process restart.

Run:
    python tutorials/18-state-and-checkpoints/python/main.py 5   # run 5 increments
"""

import asyncio
import pathlib
import shutil
import sys
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

from agent_framework._workflows._checkpoint import FileCheckpointStorage  # noqa: E402
from agent_framework._workflows._executor import Executor, handler  # noqa: E402
from agent_framework._workflows._workflow_builder import WorkflowBuilder  # noqa: E402
from agent_framework._workflows._workflow_context import WorkflowContext  # noqa: E402


CHECKPOINT_DIR = pathlib.Path(__file__).resolve().parent / ".checkpoints"


class CounterExecutor(Executor):
    """Adds incoming numbers to a running total. Stateful across invocations."""

    def __init__(self) -> None:
        super().__init__(id="counter")
        self.total = 0

    @handler
    async def increment(self, amount: int, ctx: WorkflowContext[None, int]) -> None:
        self.total += amount
        await ctx.yield_output(self.total)

    async def on_checkpoint_save(self) -> dict[str, Any]:
        return {"total": self.total}

    async def on_checkpoint_restore(self, state: dict[str, Any]) -> None:
        self.total = int(state.get("total", 0))


WORKFLOW_NAME = "counter-workflow"


def build_workflow(storage: FileCheckpointStorage):
    counter = CounterExecutor()
    return (
        WorkflowBuilder(
            start_executor=counter,
            name=WORKFLOW_NAME,
            checkpoint_storage=storage,
        )
        .build()
    )


async def run_once(storage: FileCheckpointStorage, amount: int) -> int:
    """Run the workflow with a fresh counter, feeding one amount."""
    workflow = build_workflow(storage)
    outputs: list[int] = []
    async for event in workflow.run(amount, stream=True):
        if getattr(event, "type", None) == "output":
            data = getattr(event, "data", None)
            if isinstance(data, int):
                outputs.append(data)
    return outputs[-1] if outputs else 0


async def restore_and_replay(storage: FileCheckpointStorage, checkpoint_id: str) -> int:
    """Build a fresh workflow, restore a checkpoint, and let it run to completion."""
    workflow = build_workflow(storage)
    outputs: list[int] = []
    async for event in workflow.run(
        stream=True,
        checkpoint_id=checkpoint_id,
        checkpoint_storage=storage,
    ):
        if getattr(event, "type", None) == "output":
            data = getattr(event, "data", None)
            if isinstance(data, int):
                outputs.append(data)
    return outputs[-1] if outputs else 0


async def demo(n: int) -> None:
    if CHECKPOINT_DIR.exists():
        shutil.rmtree(CHECKPOINT_DIR)
    CHECKPOINT_DIR.mkdir()
    storage = FileCheckpointStorage(str(CHECKPOINT_DIR))

    # Run N increments sequentially (fresh workflow each time — demonstrates
    # that state is checkpointed to disk, not carried in the workflow object).
    last_total = 0
    for i in range(1, n + 1):
        last_total = await run_once(storage, 1)
        print(f"run {i}: total = {last_total}")

    # Show what's on disk.
    files = list(CHECKPOINT_DIR.iterdir())
    print(f"\n{len(files)} checkpoint file(s) on disk.")

    # Restore from the latest checkpoint — demonstrates resume semantics.
    latest = await storage.get_latest(workflow_name=WORKFLOW_NAME)
    if latest:
        print(f"latest checkpoint id: {latest.checkpoint_id[:8]}…")
        replayed = await restore_and_replay(storage, latest.checkpoint_id)
        print(f"replayed total: {replayed}")


async def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    await demo(n)


if __name__ == "__main__":
    asyncio.run(main())
