"""Postgres-backed :class:`CheckpointStore` for durable agent-loop resume.

Persists checkpoints across process restarts in an ``agent_checkpoints`` table.
Follows the same conventions as ``core.storage.postgres``: the shared async
connection pool from ``core.db.connection``, tenant-scoped rows, and idempotent
``CREATE TABLE IF NOT EXISTS`` self-initialization (no separate migration
required).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import orjson
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

# Incremental step write: only the new step entry + trajectory element cross
# the wire; the scalar bookkeeping fields inside `data` are patched in place so
# a later load() sees exactly what a full save() would have produced. The
# nested jsonb_set calls apply innermost-first to the OLD row value.
_STEP_UPDATE = """
UPDATE agent_checkpoints SET
    data = jsonb_set(
        jsonb_set(
            jsonb_set(
                jsonb_set(
                    jsonb_set(
                        jsonb_set(
                            data,
                            ARRAY['steps', %s], %s::jsonb, true
                        ),
                        '{trajectory}',
                        COALESCE(data->'trajectory', '[]'::jsonb) || %s::jsonb,
                        true
                    ),
                    '{step}', %s::jsonb, true
                ),
                '{status}', %s::jsonb, true
            ),
            '{version}', %s::jsonb, true
        ),
        '{updated_at}', %s::jsonb, true
    ),
    status = %s,
    version = %s,
    updated_at = NOW()
WHERE run_id = %s
"""


# Above this size the JSON dump is pushed off the event loop: the checkpoint
# re-serializes the whole accumulated steps map on every tool step, so long
# runs with large tool outputs would otherwise stall the loop progressively.
_OFFLOAD_THRESHOLD_BYTES = 256 * 1024


class PostgresCheckpointStore:
    """Durable checkpoint persistence backed by PostgreSQL."""

    def __init__(self) -> None:
        self._last_payload_bytes = 0

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
        # orjson, strict mode (no ``default``): a non-JSON-serializable step
        # result fails loudly here exactly as stdlib json did. The previous
        # payload size decides whether this dump runs inline or in a thread —
        # checkpoints only grow within a run, so it is an accurate predictor.
        if self._last_payload_bytes > _OFFLOAD_THRESHOLD_BYTES:
            raw = await asyncio.to_thread(orjson.dumps, checkpoint.to_dict())
        else:
            raw = orjson.dumps(checkpoint.to_dict())
        self._last_payload_bytes = len(raw)
        payload = raw.decode()
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

    async def save_step(
        self,
        checkpoint: Checkpoint,
        key: str,
        entry: dict[str, Any],
        trajectory_entry: dict[str, Any],
    ) -> None:
        """Persist ONE new step without re-serializing the whole checkpoint.

        ``CheckpointManager.run_step`` calls this after mutating the in-memory
        checkpoint (steps/trajectory/step/status). Cumulative bytes written
        over an n-step run drop from O(n²) to O(n). Version/updated_at
        bookkeeping stays in lock-step with :meth:`save`; when the row does
        not exist yet (first step of a fresh run) it falls back to the full
        upsert.
        """
        new_updated_at = time.time()
        new_version = checkpoint.version + 1
        params = (
            key,
            orjson.dumps(entry).decode(),
            orjson.dumps([trajectory_entry]).decode(),
            orjson.dumps(checkpoint.step).decode(),
            orjson.dumps(checkpoint.status).decode(),
            orjson.dumps(new_version).decode(),
            orjson.dumps(new_updated_at).decode(),
            checkpoint.status,
            new_version,
            checkpoint.run_id,
        )
        async with get_async_cursor() as cur:
            await cur.execute(_STEP_UPDATE, params)
            updated = cur.rowcount
        if updated:
            checkpoint.version = new_version
            checkpoint.updated_at = new_updated_at
            return
        # No row yet — first persist of this run goes through the full path
        # (which does its own version/updated_at bookkeeping).
        await self.save(checkpoint)

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
            data = orjson.loads(data)
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
