"""
Phase 7 Refactor 11 — PostgresCheckpointStorage tests.

Uses the shared Postgres testcontainer fixture (never mock the DB —
schema drift has burned us before). Each test rolls through the full
save/load/list/get_latest/delete cycle against a real table.
"""

from __future__ import annotations

import pathlib
import sys

import pytest
from agent_framework._workflows._checkpoint import (
    WorkflowCheckpoint,
    WorkflowCheckpointException,
)


# Reuse the conftest fixtures (postgres_pool + clean_db) defined at
# agents/tests/conftest.py.
pytestmark = pytest.mark.asyncio


@pytest.fixture
async def storage(postgres_pool):
    """Return a PostgresCheckpointStorage backed by the testcontainer pool."""
    from shared.checkpoint_storage import PostgresCheckpointStorage
    # Clear any prior checkpoints from other tests.
    async with postgres_pool.acquire() as conn:
        await conn.execute("TRUNCATE TABLE workflow_checkpoints")
    return PostgresCheckpointStorage(postgres_pool)


def _checkpoint(workflow_name: str = "demo", *, iteration: int = 0) -> WorkflowCheckpoint:
    return WorkflowCheckpoint(
        workflow_name=workflow_name,
        graph_signature_hash="deadbeef",
        state={"total": iteration},
        iteration_count=iteration,
    )


async def test_save_then_load_round_trip(storage) -> None:
    cp = _checkpoint(iteration=3)
    saved_id = await storage.save(cp)
    assert saved_id == cp.checkpoint_id

    loaded = await storage.load(cp.checkpoint_id)
    assert loaded.workflow_name == "demo"
    assert loaded.iteration_count == 3
    assert loaded.state == {"total": 3}


async def test_load_missing_raises(storage) -> None:
    with pytest.raises(WorkflowCheckpointException, match="No checkpoint found"):
        await storage.load("00000000-0000-0000-0000-000000000000")


async def test_list_checkpoints_scoped_to_workflow_name(storage) -> None:
    for i in range(3):
        await storage.save(_checkpoint("wf-a", iteration=i))
    for i in range(2):
        await storage.save(_checkpoint("wf-b", iteration=i))

    a = await storage.list_checkpoints(workflow_name="wf-a")
    b = await storage.list_checkpoints(workflow_name="wf-b")

    assert len(a) == 3
    assert len(b) == 2
    assert all(cp.workflow_name == "wf-a" for cp in a)
    assert all(cp.workflow_name == "wf-b" for cp in b)


async def test_list_checkpoint_ids_ordered_newest_first(storage) -> None:
    saved_ids = []
    for i in range(3):
        cp = _checkpoint("wf-ids", iteration=i)
        saved_ids.append(await storage.save(cp))

    ids = await storage.list_checkpoint_ids(workflow_name="wf-ids")
    assert len(ids) == 3
    # Newest-first by created_at; in our rapid-fire test that equals insertion order reversed.
    assert ids[0] == saved_ids[-1]


async def test_get_latest_returns_most_recent(storage) -> None:
    await storage.save(_checkpoint("late", iteration=1))
    await storage.save(_checkpoint("late", iteration=2))
    latest_saved = _checkpoint("late", iteration=42)
    await storage.save(latest_saved)

    latest = await storage.get_latest(workflow_name="late")
    assert latest is not None
    assert latest.iteration_count == 42
    assert latest.checkpoint_id == latest_saved.checkpoint_id


async def test_get_latest_none_when_no_checkpoints(storage) -> None:
    latest = await storage.get_latest(workflow_name="never-ran")
    assert latest is None


async def test_delete_removes_row_and_reports_true(storage) -> None:
    cp = _checkpoint(iteration=5)
    await storage.save(cp)

    assert await storage.delete(cp.checkpoint_id) is True
    with pytest.raises(WorkflowCheckpointException):
        await storage.load(cp.checkpoint_id)


async def test_delete_missing_returns_false(storage) -> None:
    assert await storage.delete("00000000-0000-0000-0000-000000000000") is False


async def test_save_upserts_existing_checkpoint(storage) -> None:
    cp = _checkpoint(iteration=1)
    await storage.save(cp)
    # Mutate in place and save again — DB row must update, not duplicate.
    cp.iteration_count = 99
    cp.state = {"total": 99}
    await storage.save(cp)

    all_cps = await storage.list_checkpoints(workflow_name=cp.workflow_name)
    assert len(all_cps) == 1
    assert all_cps[0].iteration_count == 99


async def test_factory_returns_postgres_storage_when_backend_is_postgres(
    postgres_pool, monkeypatch
) -> None:
    """Passing the pool explicitly bypasses shared.db.get_pool(), which
    may not be initialized in the test runner."""
    import importlib

    monkeypatch.setenv("MAF_CHECKPOINT_BACKEND", "postgres")
    from shared import config as config_mod, factory as factory_mod
    importlib.reload(config_mod)
    config_mod.Settings.model_config["env_file"] = None
    config_mod.settings = config_mod.Settings()
    importlib.reload(factory_mod)

    storage = factory_mod.get_checkpoint_storage(pool=postgres_pool)
    from shared.checkpoint_storage import PostgresCheckpointStorage
    assert isinstance(storage, PostgresCheckpointStorage)
