"""Postgres-backed :class:`CheckpointStore` for durable agent-loop resume.

Persists checkpoints across process restarts in an ``agent_checkpoints`` table.
Follows the same conventions as ``core.storage.postgres``: the shared async
connection pool from ``core.db.connection``, tenant-scoped rows, and idempotent
``CREATE TABLE IF NOT EXISTS`` self-initialization (no separate migration
required).
"""

from __future__ import annotations

import json
import time
from typing import Any

from psycopg.rows import dict_row

from core.db.connection import get_async_cursor
from core.observability.logging import get_logger
from core.orchestration.checkpoint import STATUS_RUNNING, Checkpoint

logger = get_logger(__name__)

_DDL = """
CREATE TABLE IF NOT EXISTS agent_checkpoints (
    run_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL DEFAULT 'default',
    status TEXT NOT NULL DEFAULT 'running',
    data JSONB NOT NULL,
    version INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_agent_checkpoints_resumable
    ON agent_checkpoints(tenant_id, status);
"""

_UPSERT = """
INSERT INTO agent_checkpoints (run_id, tenant_id, status, data, version, updated_at)
VALUES (%s, %s, %s, %s, %s, NOW())
ON CONFLICT (run_id) DO UPDATE SET
    tenant_id = EXCLUDED.tenant_id,
    status = EXCLUDED.status,
    data = EXCLUDED.data,
    version = EXCLUDED.version,
    updated_at = NOW()
"""


class PostgresCheckpointStore:
    """Durable checkpoint persistence backed by PostgreSQL."""

    async def initialize(self) -> None:
        """Create the checkpoint table and index if absent (idempotent)."""
        async with get_async_cursor() as cur:
            await cur.execute(_DDL)
        logger.info("agent_checkpoints schema initialized")

    async def save(self, checkpoint: Checkpoint) -> None:
        """Upsert the checkpoint by ``run_id``.

        Bumps ``version``/``updated_at`` in lock-step with
        :class:`~core.orchestration.checkpoint.InMemoryCheckpointStore` so the
        two stores are behaviourally interchangeable.
        """
        checkpoint.updated_at = time.time()
        checkpoint.version += 1
        payload = json.dumps(checkpoint.to_dict())
        async with get_async_cursor() as cur:
            await cur.execute(
                _UPSERT,
                (
                    checkpoint.run_id,
                    checkpoint.tenant_id or "default",
                    checkpoint.status,
                    payload,
                    checkpoint.version,
                ),
            )

    async def load(self, run_id: str) -> Checkpoint | None:
        """Load a checkpoint by ``run_id``, or None if absent."""
        async with get_async_cursor(row_factory=dict_row) as cur:  # type: ignore
            await cur.execute(
                "SELECT data FROM agent_checkpoints WHERE run_id = %s", (run_id,)
            )
            row = await cur.fetchone()
        if not row or not isinstance(row, dict):
            return None
        data = row["data"]
        # psycopg returns JSONB as a parsed object; tolerate a raw string too.
        if isinstance(data, str):
            data = json.loads(data)
        return Checkpoint.from_dict(data)

    async def delete(self, run_id: str) -> None:
        """Remove a checkpoint (e.g. after successful completion)."""
        async with get_async_cursor() as cur:
            await cur.execute(
                "DELETE FROM agent_checkpoints WHERE run_id = %s", (run_id,)
            )

    async def list_resumable(self, tenant_id: str | None = None) -> list[str]:
        """Return ``run_id``s still ``running`` (crash-recovery candidates)."""
        sql = "SELECT run_id FROM agent_checkpoints WHERE status = %s"
        params: list[Any] = [STATUS_RUNNING]
        if tenant_id is not None:
            sql += " AND tenant_id = %s"
            params.append(tenant_id)
        async with get_async_cursor(row_factory=dict_row) as cur:  # type: ignore
            await cur.execute(sql, params)
            rows = await cur.fetchall()
        return [r["run_id"] for r in rows if isinstance(r, dict)]


__all__ = ["PostgresCheckpointStore"]
