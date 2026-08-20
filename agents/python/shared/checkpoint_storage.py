"""Postgres-backed MAF CheckpointStorage for durable workflow state.

Reads + writes the ``workflow_checkpoints`` table from
``docker/postgres/init.sql``. Every checkpoint is stored as an encoded
JSONB blob produced by MAF's own ``encode_checkpoint_value`` helper so
the wire format is identical to what FileCheckpointStorage writes to
disk — we just keep it in Postgres instead of a file.

Wired through ``shared.factory.get_checkpoint_storage`` when
``MAF_CHECKPOINT_BACKEND=postgres`` (the default for production).
"""

from __future__ import annotations

import asyncpg
import json
import logging
from datetime import datetime

from agent_framework._workflows._checkpoint import (
    CheckpointID,
    CheckpointStorage,
    WorkflowCheckpoint,
    WorkflowCheckpointException,
)
from agent_framework._workflows._checkpoint_encoding import (
    decode_checkpoint_value,
    encode_checkpoint_value,
)

logger = logging.getLogger(__name__)


class PostgresCheckpointStorage(CheckpointStorage):
    """CheckpointStorage implementation backed by asyncpg + JSONB.

    Args:
        pool: asyncpg connection pool shared with the rest of the app.
        table: table name (defaults to ``workflow_checkpoints``). Only
            override in tests.
    """

    def __init__(self, pool: asyncpg.Pool, *, table: str = "workflow_checkpoints") -> None:
        self._pool = pool
        self._table = table

    async def save(self, checkpoint: WorkflowCheckpoint) -> CheckpointID:
        payload = encode_checkpoint_value(checkpoint.to_dict())
        async with self._pool.acquire() as conn:
            await conn.execute(
                f"""
                INSERT INTO {self._table} (checkpoint_id, workflow_name, payload, created_at)
                VALUES ($1, $2, $3::jsonb, $4)
                ON CONFLICT (checkpoint_id)
                DO UPDATE SET payload = EXCLUDED.payload, created_at = EXCLUDED.created_at
                """,
                checkpoint.checkpoint_id,
                checkpoint.workflow_name,
                json.dumps(payload),
                _parse_ts(checkpoint.timestamp),
            )
        logger.debug("saved checkpoint %s for workflow %s", checkpoint.checkpoint_id, checkpoint.workflow_name)
        return checkpoint.checkpoint_id

    async def load(self, checkpoint_id: CheckpointID) -> WorkflowCheckpoint:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT payload FROM {self._table} WHERE checkpoint_id = $1",
                checkpoint_id,
            )
        if row is None:
            raise WorkflowCheckpointException(f"No checkpoint found with ID {checkpoint_id}")
        data = decode_checkpoint_value(json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"])
        return WorkflowCheckpoint.from_dict(data)

    async def list_checkpoints(self, *, workflow_name: str) -> list[WorkflowCheckpoint]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT payload FROM {self._table} "
                f"WHERE workflow_name = $1 ORDER BY created_at DESC",
                workflow_name,
            )
        return [WorkflowCheckpoint.from_dict(decode_checkpoint_value(_payload(r))) for r in rows]

    async def list_checkpoint_ids(self, *, workflow_name: str) -> list[CheckpointID]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT checkpoint_id FROM {self._table} "
                f"WHERE workflow_name = $1 ORDER BY created_at DESC",
                workflow_name,
            )
        return [str(r["checkpoint_id"]) for r in rows]

    async def get_latest(self, *, workflow_name: str) -> WorkflowCheckpoint | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT payload FROM {self._table} "
                f"WHERE workflow_name = $1 ORDER BY created_at DESC LIMIT 1",
                workflow_name,
            )
        if row is None:
            return None
        return WorkflowCheckpoint.from_dict(decode_checkpoint_value(_payload(row)))

    async def delete(self, checkpoint_id: CheckpointID) -> bool:
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                f"DELETE FROM {self._table} WHERE checkpoint_id = $1",
                checkpoint_id,
            )
        # asyncpg returns the command tag e.g. "DELETE 1"; trailing number is the row count.
        affected = int(result.split()[-1]) if result else 0
        return affected > 0


class RecordingCheckpointStorage(CheckpointStorage):
    """Wraps a ``CheckpointStorage`` and records every checkpoint_id saved
    through it, in order.

    MAF's own ``WorkflowEvent`` stream never surfaces a save — verified
    directly against a live run: attaching ``checkpoint_storage=...`` to
    ``Workflow.run()`` saves one checkpoint per superstep with zero
    corresponding event. A caller that wants to emit its own
    ``kind="checkpoint"`` ``OrchestrationEvent`` as each save happens
    (``orchestrator/modes/workflow_mode.py``) drains ``.saved`` between
    iterations of its own event loop — safe because both the workflow's
    internal ``save()`` call and the caller's loop run on the same task,
    so nothing saved mid-step can be missed or duplicated.
    """

    def __init__(self, inner: CheckpointStorage) -> None:
        self._inner = inner
        self.saved: list[CheckpointID] = []

    async def save(self, checkpoint: WorkflowCheckpoint) -> CheckpointID:
        checkpoint_id = await self._inner.save(checkpoint)
        self.saved.append(checkpoint_id)
        return checkpoint_id

    async def load(self, checkpoint_id: CheckpointID) -> WorkflowCheckpoint:
        return await self._inner.load(checkpoint_id)

    async def list_checkpoints(self, *, workflow_name: str) -> list[WorkflowCheckpoint]:
        return await self._inner.list_checkpoints(workflow_name=workflow_name)

    async def list_checkpoint_ids(self, *, workflow_name: str) -> list[CheckpointID]:
        return await self._inner.list_checkpoint_ids(workflow_name=workflow_name)

    async def get_latest(self, *, workflow_name: str) -> WorkflowCheckpoint | None:
        return await self._inner.get_latest(workflow_name=workflow_name)

    async def delete(self, checkpoint_id: CheckpointID) -> bool:
        return await self._inner.delete(checkpoint_id)


def drain_new_checkpoint_ids(recorder: RecordingCheckpointStorage, already_seen: int) -> list[CheckpointID]:
    """Return checkpoint ids saved since the caller last checked (index
    ``already_seen`` into ``recorder.saved``). Callers track the returned
    list's length as their new ``already_seen`` for the next call.
    """
    return recorder.saved[already_seen:]


# ─────────────────────── Helpers ───────────────────────


def _payload(row: asyncpg.Record) -> dict:
    """JSONB comes back as dict from asyncpg; as str from some drivers. Handle both."""
    value = row["payload"]
    return json.loads(value) if isinstance(value, str) else value


def _parse_ts(ts: str) -> datetime:
    """WorkflowCheckpoint.timestamp is ISO-8601 str; convert for TIMESTAMPTZ column."""
    return datetime.fromisoformat(ts)
