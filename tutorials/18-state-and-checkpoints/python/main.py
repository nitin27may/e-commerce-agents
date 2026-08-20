"""
MAF v1 — Chapter 18: State and Checkpoints (Python)

Two-executor workflow: ReturnRequestExecutor accumulates a refund amount
as return line items get processed, then forwards to
FinalizeReturnExecutor, which yields the refund total as workflow
output. MAF checkpoints at every superstep boundary; we persist
snapshots via FileCheckpointStorage.

After the end-to-end run, we throw away the first workflow instance,
build a fresh one with a fresh ReturnRequestExecutor (different initial
refund!), and resume from the first checkpoint — proving that executor
state (the running refund_amount) round-trips through the JSON on disk.

This is a small approximation of the production ``workflow:return-replace``
chain (`agents/python/workflows/return_replace.py`) — that workflow carries
a much larger ``WorkflowState`` through six HITL-gated steps. This chapter
only teaches the checkpoint save/restore mechanic itself, at toy scale.

Run:
    python tutorials/18-state-and-checkpoints/python/main.py                 # initial=10.0 item=5.0 -> 15.0
    python tutorials/18-state-and-checkpoints/python/main.py 10.0 5.0
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
WORKFLOW_NAME = "return-request-workflow"


class ReturnRequestExecutor(Executor):
    """Running refund total for a return request. Forwards the updated
    refund_amount to the next executor.

    State (``self.refund_amount``) is captured in the checkpoint by
    ``on_checkpoint_save`` and rehydrated by ``on_checkpoint_restore``.
    """

    def __init__(self, initial_refund: float) -> None:
        super().__init__(id="return-request")
        self.refund_amount = initial_refund

    @handler
    async def handle(self, item_refund: float, ctx: WorkflowContext[float, None]) -> None:
        self.refund_amount += item_refund
        await ctx.send_message(self.refund_amount)

    async def on_checkpoint_save(self) -> dict[str, Any]:
        return {"refund_amount": self.refund_amount}

    async def on_checkpoint_restore(self, state: dict[str, Any]) -> None:
        self.refund_amount = float(state.get("refund_amount", 0.0))


class FinalizeReturnExecutor(Executor):
    """Stateless terminal node: yields whatever refund total it receives as output."""

    def __init__(self) -> None:
        super().__init__(id="finalize-return")

    @handler
    async def handle(self, refund_amount: float, ctx: WorkflowContext[None, float]) -> None:
        await ctx.yield_output(refund_amount)


def build_workflow(storage: FileCheckpointStorage, *, initial_refund: float):
    return_request = ReturnRequestExecutor(initial_refund)
    finalize = FinalizeReturnExecutor()
    return (
        WorkflowBuilder(
            start_executor=return_request,
            name=WORKFLOW_NAME,
            checkpoint_storage=storage,
        )
        .add_edge(return_request, finalize)
        .build()
    )


async def run_once(storage: FileCheckpointStorage, *, initial_refund: float, item_refund: float) -> float:
    """Run the workflow end to end and return the final refund amount."""
    workflow = build_workflow(storage, initial_refund=initial_refund)
    outputs: list[float] = []
    async for event in workflow.run(item_refund, stream=True):
        if getattr(event, "type", None) == "output":
            data = getattr(event, "data", None)
            if isinstance(data, (int, float)):
                outputs.append(data)
    return outputs[-1] if outputs else 0.0


async def resume_from_checkpoint(
    storage: FileCheckpointStorage,
    checkpoint_id: str,
    *,
    resume_initial_refund: float,
) -> float:
    """Build a fresh workflow (with a different initial refund!) and resume
    from a checkpoint.

    If checkpointing works, the resumed ReturnRequestExecutor's
    ``refund_amount`` is restored from the checkpoint, not from
    ``resume_initial_refund`` — proving state survives the fresh
    ``ReturnRequestExecutor(initial_refund=resume_initial_refund)``
    construction.
    """
    workflow = build_workflow(storage, initial_refund=resume_initial_refund)
    outputs: list[float] = []
    async for event in workflow.run(
        stream=True,
        checkpoint_id=checkpoint_id,
        checkpoint_storage=storage,
    ):
        if getattr(event, "type", None) == "output":
            data = getattr(event, "data", None)
            if isinstance(data, (int, float)):
                outputs.append(data)
    return outputs[-1] if outputs else 0.0


async def demo(initial_refund: float, item_refund: float) -> None:
    if CHECKPOINT_DIR.exists():
        shutil.rmtree(CHECKPOINT_DIR)
    CHECKPOINT_DIR.mkdir()
    storage = FileCheckpointStorage(str(CHECKPOINT_DIR))

    # ─── Phase 1: run end to end, checkpoints are written on every superstep ──
    print(f"Phase 1: initial_refund={initial_refund}, item_refund={item_refund}")
    result = await run_once(storage, initial_refund=initial_refund, item_refund=item_refund)
    print(f"Phase 1 result: refund_amount = {result}")

    files = list(CHECKPOINT_DIR.iterdir())
    print(f"\n{len(files)} checkpoint file(s) on disk.")

    # ─── Phase 2: rehydrate into a fresh workflow with a WRONG initial refund ─
    # Seeding with 999.0 proves the checkpoint is the source of truth: the
    # resumed ReturnRequestExecutor starts with self.refund_amount = 999.0,
    # then on_checkpoint_restore overwrites it with the snapshot's
    # refund_amount before the FinalizeReturn's superstep runs.
    #
    # We pick the *first* checkpoint (superstep 1, before FinalizeReturn
    # emitted output). Resuming from the latest one would replay a
    # workflow that has no pending messages — MAF happily completes with
    # no output.
    checkpoints = await storage.list_checkpoints(workflow_name=WORKFLOW_NAME)
    if not checkpoints:
        print("No checkpoints produced — nothing to resume.")
        return
    checkpoints.sort(key=lambda cp: cp.timestamp)
    first = checkpoints[0]

    wrong_initial_refund = 999.0
    print(f"Resuming from {first.checkpoint_id[:8]}… with initial_refund={wrong_initial_refund}")
    replayed = await resume_from_checkpoint(
        storage, first.checkpoint_id, resume_initial_refund=wrong_initial_refund
    )
    print(f"Phase 2 result: refund_amount = {replayed} (expected {result})")


async def main() -> None:
    initial_refund = float(sys.argv[1]) if len(sys.argv) > 1 else 10.0
    item_refund = float(sys.argv[2]) if len(sys.argv) > 2 else 5.0
    await demo(initial_refund, item_refund)


if __name__ == "__main__":
    asyncio.run(main())
