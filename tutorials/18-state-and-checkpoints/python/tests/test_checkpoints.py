"""
Chapter 18 — State and Checkpoints: tests.

No LLM — checkpoint plumbing is deterministic.
"""

import pathlib
import shutil
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[4]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from main import (  # noqa: E402
    WORKFLOW_NAME,
    CounterExecutor,
    build_workflow,
    restore_and_replay,
    run_once,
)

from agent_framework._workflows._checkpoint import FileCheckpointStorage  # noqa: E402


@pytest.fixture
def tmp_storage():
    path = pathlib.Path(tempfile.mkdtemp(prefix="maf-v1-ch18-"))
    try:
        yield FileCheckpointStorage(str(path)), path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest.mark.asyncio
async def test_counter_increments_its_internal_state() -> None:
    counter = CounterExecutor()
    saved_state = await counter.on_checkpoint_save()
    assert saved_state == {"total": 0}

    counter.total = 42
    saved_state = await counter.on_checkpoint_save()
    assert saved_state == {"total": 42}


@pytest.mark.asyncio
async def test_on_checkpoint_restore_populates_state() -> None:
    counter = CounterExecutor()
    await counter.on_checkpoint_restore({"total": 17})
    assert counter.total == 17


@pytest.mark.asyncio
async def test_running_workflow_writes_checkpoint(tmp_storage) -> None:
    storage, directory = tmp_storage
    await run_once(storage, amount=5)
    files = list(directory.iterdir())
    assert files, "expected at least one checkpoint file on disk"


@pytest.mark.asyncio
async def test_storage_get_latest_returns_a_checkpoint(tmp_storage) -> None:
    storage, _ = tmp_storage
    await run_once(storage, amount=5)
    latest = await storage.get_latest(workflow_name=WORKFLOW_NAME)
    assert latest is not None
    assert latest.checkpoint_id
    assert latest.workflow_name == WORKFLOW_NAME


@pytest.mark.asyncio
async def test_restore_from_checkpoint_does_not_crash(tmp_storage) -> None:
    """The replay path must complete cleanly and produce a final value."""
    storage, _ = tmp_storage
    await run_once(storage, amount=5)
    latest = await storage.get_latest(workflow_name=WORKFLOW_NAME)
    assert latest is not None
    # Even if replay yields no new output (all work was done), the call must
    # not raise.
    result = await restore_and_replay(storage, latest.checkpoint_id)
    assert isinstance(result, int)


@pytest.mark.asyncio
async def test_multiple_runs_produce_multiple_checkpoints(tmp_storage) -> None:
    storage, directory = tmp_storage
    for _ in range(3):
        await run_once(storage, amount=1)
    files = list(directory.iterdir())
    assert len(files) >= 3, f"expected ≥3 checkpoint files, got {len(files)}"


@pytest.mark.asyncio
async def test_workflow_builds_with_checkpoint_storage(tmp_storage) -> None:
    storage, _ = tmp_storage
    workflow = build_workflow(storage)
    assert workflow is not None
