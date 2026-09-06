"""Durable Postgres-backed :class:`~core.a2a.server.TaskStore`.

Closes the one durability gap in the A2A stack: ``InMemoryTaskStore`` loses
every task on restart, so a peer polling ``tasks/get`` across a redeploy got
``task not found``. This store persists tasks as JSONB rows (same conventions
as :mod:`core.orchestration.checkpoint_postgres`: shared async pool from
``core.db.connection``, tenant-scoped rows, idempotent self-initializing DDL).

Usage::

    store = PostgresTaskStore()
    await store.initialize()          # idempotent DDL
    server = MyAgent(task_store=store)
"""

from __future__ import annotations

import time

import orjson
from psycopg.rows import dict_row

from core.a2a.server import TaskStore
from core.a2a.types import Task
from core.context import get_current_tenant_id
from core.db.connection import get_async_cursor
from core.db.ddl import skip_runtime_ddl
from core.observability.logging import get_logger

logger = get_logger(__name__)

_DDL = """
CREATE TABLE IF NOT EXISTS a2a_tasks (
    task_id     TEXT PRIMARY KEY,
    tenant_id   TEXT NOT NULL DEFAULT 'default',
    status      TEXT NOT NULL,
    data        JSONB NOT NULL,
    updated_at  DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_a2a_tasks_tenant ON a2a_tasks (tenant_id);
"""

_UPSERT = """
INSERT INTO a2a_tasks (task_id, tenant_id, status, data, updated_at)
VALUES (%s, %s, %s, %s::jsonb, %s)
ON CONFLICT (task_id) DO UPDATE SET
    status = EXCLUDED.status,
    data = EXCLUDED.data,
    updated_at = EXCLUDED.updated_at
WHERE a2a_tasks.tenant_id = EXCLUDED.tenant_id
"""

_SELECT = "SELECT data FROM a2a_tasks WHERE task_id = %s AND tenant_id = %s"
_DELETE = "DELETE FROM a2a_tasks WHERE task_id = %s AND tenant_id = %s"


def _tenant() -> str:
    """Ambient tenant, mirroring the checkpoint store's row scoping."""
    return get_current_tenant_id() or "default"


class PostgresTaskStore(TaskStore):
    """Durable A2A task persistence backed by PostgreSQL."""

    async def initialize(self) -> None:
        """Create the task table and index if absent (idempotent)."""
        if skip_runtime_ddl("a2a task store", "a2a_tasks"):
            return
        async with get_async_cursor() as cur:
            await cur.execute(_DDL)
        logger.info("a2a_tasks schema initialized")

    async def get(self, task_id: str) -> Task | None:
        """Get a task by ID (tenant-scoped), or None if absent."""
        async with get_async_cursor(row_factory=dict_row) as cur:  # type: ignore[arg-type]
            await cur.execute(_SELECT, (task_id, _tenant()))
            row = await cur.fetchone()
        if row is None:
            return None
        data = row["data"]
        # psycopg returns JSONB parsed; tolerate a raw string too.
        if isinstance(data, str):
            data = orjson.loads(data)
        return Task.from_dict(data)

    async def save(self, task: Task) -> None:
        """Upsert the task by id, scoped to the ambient tenant."""
        payload = orjson.dumps(task.to_dict()).decode()
        async with get_async_cursor() as cur:
            await cur.execute(
                _UPSERT,
                (
                    task.id,
                    _tenant(),
                    task.status.state.value,
                    payload,
                    time.time(),
                ),
            )

    async def delete(self, task_id: str) -> bool:
        """Delete a task (tenant-scoped). Returns True if a row was removed."""
        async with get_async_cursor() as cur:
            await cur.execute(_DELETE, (task_id, _tenant()))
            return bool(cur.rowcount)
