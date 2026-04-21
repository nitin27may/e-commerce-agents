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
    AccumulatorExecutor,
    build_workflow,
    resume_from_checkpoint,
    run_once,
)

from agent_framework._workflows._checkpoint import (  # noqa: E402
    FileCheckpointStorage,
    InMemoryCheckpointStorage,
)


@pytest.fixture
def tmp_file_storage():
    path = pathlib.Path(tempfile.mkdtemp(prefix="maf-v1-ch18-"))
    try:
        yield FileCheckpointStorage(str(path)), path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest.mark.asyncio
async def test_on_checkpoint_save_roundtrips_total() -> None:
    counter = AccumulatorExecutor(seed=10)
    assert (await counter.on_checkpoint_save()) == {"total": 10}

    counter.total = 42
    assert (await counter.on_checkpoint_save()) == {"total": 42}


@pytest.mark.asyncio
async def test_on_checkpoint_restore_overwrites_seeded_state() -> None:
    """Restore must clobber whatever seed was passed to __init__."""
    counter = AccumulatorExecutor(seed=999)
    assert counter.total == 999
    await counter.on_checkpoint_restore({"total": 17})
    assert counter.total == 17


@pytest.mark.asyncio
async def test_on_checkpoint_restore_defaults_when_key_missing() -> None:
    counter = AccumulatorExecutor(seed=5)
    await counter.on_checkpoint_restore({})
    assert counter.total == 0


@pytest.mark.asyncio
async def test_running_workflow_writes_checkpoints_to_disk(tmp_file_storage) -> None:
    storage, directory = tmp_file_storage
    result = await run_once(storage, seed=10, amount=5)
    assert result == 15
    files = list(directory.iterdir())
    assert files, "expected at least one checkpoint file on disk"


@pytest.mark.asyncio
async def test_list_checkpoints_returns_non_empty(tmp_file_storage) -> None:
    storage, _ = tmp_file_storage
    await run_once(storage, seed=10, amount=5)
    checkpoints = await storage.list_checkpoints(workflow_name=WORKFLOW_NAME)
    assert checkpoints
    assert all(cp.workflow_name == WORKFLOW_NAME for cp in checkpoints)


@pytest.mark.asyncio
async def test_resume_restores_state_across_fresh_workflow(tmp_file_storage) -> None:
    """The round-trip contract: resume from first checkpoint with a
    deliberately wrong seed; on_checkpoint_restore must bring the total
    back so Finalizer yields the original result."""
    storage, _ = tmp_file_storage
    expected = await run_once(storage, seed=10, amount=5)
    assert expected == 15

    checkpoints = await storage.list_checkpoints(workflow_name=WORKFLOW_NAME)
    checkpoints.sort(key=lambda cp: cp.timestamp)
    first = checkpoints[0]

    replayed = await resume_from_checkpoint(storage, first.checkpoint_id, resume_seed=999)
    assert replayed == expected


@pytest.mark.asyncio
async def test_in_memory_storage_produces_same_replay_result() -> None:
    """Swap FileCheckpointStorage for InMemoryCheckpointStorage: same outcome."""
    storage = InMemoryCheckpointStorage()
    expected = await run_once(storage, seed=7, amount=3)
    assert expected == 10

    checkpoints = await storage.list_checkpoints(workflow_name=WORKFLOW_NAME)
    checkpoints.sort(key=lambda cp: cp.timestamp)
    replayed = await resume_from_checkpoint(
        storage, checkpoints[0].checkpoint_id, resume_seed=999
    )
    assert replayed == expected


@pytest.mark.asyncio
async def test_workflow_builds_with_checkpoint_storage(tmp_file_storage) -> None:
    storage, _ = tmp_file_storage
    workflow = build_workflow(storage, seed=0)
    assert workflow is not None
    assert workflow.name == WORKFLOW_NAME
