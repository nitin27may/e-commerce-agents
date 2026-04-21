"""
MAF v1 — Chapter 18: State and Checkpoints (Python)

Two-executor workflow: Accumulator adds an incoming amount to a seeded
running total and forwards to Finalizer, which yields the total as
workflow output. MAF checkpoints at every superstep boundary; we
persist snapshots via FileCheckpointStorage.

After the end-to-end run, we throw away the first workflow instance,
build a fresh one with a fresh Accumulator (different seed!), and
resume from the first checkpoint — proving that executor state
(Accumulator's total) round-trips through the JSON on disk.

Run:
    python tutorials/18-state-and-checkpoints/python/main.py           # seed=10 add=5 -> 15
    python tutorials/18-state-and-checkpoints/python/main.py 10 5
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
WORKFLOW_NAME = "accumulator-workflow"


class AccumulatorExecutor(Executor):
    """Seeded running total. Forwards the new total to the next executor.

    State (``self.total``) is captured in the checkpoint by
    ``on_checkpoint_save`` and rehydrated by ``on_checkpoint_restore``.
    """

    def __init__(self, seed: int) -> None:
        super().__init__(id="accumulator")
        self.total = seed

    @handler
    async def handle(self, amount: int, ctx: WorkflowContext[int, None]) -> None:
        self.total += amount
        await ctx.send_message(self.total)

    async def on_checkpoint_save(self) -> dict[str, Any]:
        return {"total": self.total}

    async def on_checkpoint_restore(self, state: dict[str, Any]) -> None:
        self.total = int(state.get("total", 0))


class FinalizerExecutor(Executor):
    """Stateless terminal node: yields whatever total it receives as output."""

    def __init__(self) -> None:
        super().__init__(id="finalizer")

    @handler
    async def handle(self, total: int, ctx: WorkflowContext[None, int]) -> None:
        await ctx.yield_output(total)


def build_workflow(storage: FileCheckpointStorage, *, seed: int):
    accumulator = AccumulatorExecutor(seed)
    finalizer = FinalizerExecutor()
    return (
        WorkflowBuilder(
            start_executor=accumulator,
            name=WORKFLOW_NAME,
            checkpoint_storage=storage,
        )
        .add_edge(accumulator, finalizer)
        .build()
    )


async def run_once(storage: FileCheckpointStorage, *, seed: int, amount: int) -> int:
    """Run the workflow end to end and return the final total."""
    workflow = build_workflow(storage, seed=seed)
    outputs: list[int] = []
    async for event in workflow.run(amount, stream=True):
        if getattr(event, "type", None) == "output":
            data = getattr(event, "data", None)
            if isinstance(data, int):
                outputs.append(data)
    return outputs[-1] if outputs else 0


async def resume_from_checkpoint(
    storage: FileCheckpointStorage,
    checkpoint_id: str,
    *,
    resume_seed: int,
) -> int:
    """Build a fresh workflow (with a different seed!) and resume from a checkpoint.

    If checkpointing works, the resumed Accumulator's ``total`` is restored
    from the checkpoint, not from ``resume_seed`` — proving state survives
    the fresh ``AccumulatorExecutor(seed=resume_seed)`` construction.
    """
    workflow = build_workflow(storage, seed=resume_seed)
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


async def demo(seed: int, amount: int) -> None:
    if CHECKPOINT_DIR.exists():
        shutil.rmtree(CHECKPOINT_DIR)
    CHECKPOINT_DIR.mkdir()
    storage = FileCheckpointStorage(str(CHECKPOINT_DIR))

    # ─── Phase 1: run end to end, checkpoints are written on every superstep ──
    print(f"Phase 1: seed={seed}, add={amount}")
    result = await run_once(storage, seed=seed, amount=amount)
    print(f"Phase 1 result: total = {result}")

    files = list(CHECKPOINT_DIR.iterdir())
    print(f"\n{len(files)} checkpoint file(s) on disk.")

    # ─── Phase 2: rehydrate into a fresh workflow with a WRONG seed ──────────
    # Seeding with 999 proves the checkpoint is the source of truth: the
    # resumed Accumulator starts with self.total = 999, then
    # on_checkpoint_restore overwrites it with the snapshot's total before
    # the Finalizer's superstep runs.
    #
    # We pick the *first* checkpoint (superstep 1, before Finalizer emitted
    # output). Resuming from the latest one would replay a workflow that
    # has no pending messages — MAF happily completes with no output.
    checkpoints = await storage.list_checkpoints(workflow_name=WORKFLOW_NAME)
    if not checkpoints:
        print("No checkpoints produced — nothing to resume.")
        return
    checkpoints.sort(key=lambda cp: cp.timestamp)
    first = checkpoints[0]

    wrong_seed = 999
    print(f"Resuming from {first.checkpoint_id[:8]}… with seed={wrong_seed}")
    replayed = await resume_from_checkpoint(
        storage, first.checkpoint_id, resume_seed=wrong_seed
    )
    print(f"Phase 2 result: total = {replayed} (expected {result})")


async def main() -> None:
    seed = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    amount = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    await demo(seed, amount)


if __name__ == "__main__":
    asyncio.run(main())
