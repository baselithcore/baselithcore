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
from core.orchestration.checkpoint import RESUMABLE_STATUSES, Checkpoint
from core.orchestration.checkpoint_memory import summarize_run

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

# Immutable per-version snapshots (state history / time-travel). One row per
# (run_id, version); the live agent_checkpoints row keeps being overwritten as
# before, history rows are append-only.
_HISTORY_DDL = """
CREATE TABLE IF NOT EXISTS agent_checkpoint_history (
    run_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    step INTEGER NOT NULL DEFAULT 0,
    data JSONB NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (run_id, version)
);
"""

_HISTORY_INSERT = """
INSERT INTO agent_checkpoint_history (run_id, version, status, step, data)
VALUES (%s, %s, %s, %s, %s)
ON CONFLICT (run_id, version) DO NOTHING
"""

# Snapshot for the save_step fast-path: copy the just-patched live row
# server-side, so no full-data payload crosses the wire and the O(n) write
# property of save_step is preserved.
_HISTORY_SNAPSHOT_FROM_LIVE = """
INSERT INTO agent_checkpoint_history (run_id, version, status, step, data)
SELECT run_id, version, status, COALESCE((data->>'step')::int, 0), data
FROM agent_checkpoints WHERE run_id = %s
ON CONFLICT (run_id, version) DO NOTHING
"""

# Keep only the newest N snapshots per run.
_HISTORY_TRIM = """
DELETE FROM agent_checkpoint_history
WHERE run_id = %s AND version < (
    SELECT COALESCE(MIN(version), 0) FROM (
        SELECT version FROM agent_checkpoint_history
        WHERE run_id = %s ORDER BY version DESC LIMIT %s
    ) AS newest
)
"""

_HISTORY_LIST = """
SELECT version, status, step,
       COALESCE((data->>'updated_at')::float8, EXTRACT(EPOCH FROM created_at))
           AS updated_at
FROM agent_checkpoint_history WHERE run_id = %s ORDER BY version ASC
"""

# Run listing for operator/read surfaces: any status, newest first. The live
# row is the source (history rows are per-version copies of the same run).
_RUN_LIST = """
SELECT data FROM agent_checkpoints
WHERE (%(tenant_id)s IS NULL OR tenant_id = %(tenant_id)s)
  AND (%(status)s IS NULL OR status = %(status)s)
ORDER BY updated_at DESC
LIMIT %(limit)s
"""

_HISTORY_LOAD = """
SELECT data FROM agent_checkpoint_history WHERE run_id = %s AND version = %s
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
    """Durable checkpoint persistence backed by PostgreSQL.

    With ``history_enabled`` every save also appends an immutable snapshot row
    to ``agent_checkpoint_history`` keyed ``(run_id, version)`` — the substrate
    for state history / time-travel (see
    :mod:`core.orchestration.checkpoint_history`). Per run, only the newest
    ``history_limit`` snapshots are retained (0 = unlimited).
    """

    def __init__(self, history_enabled: bool = False, history_limit: int = 200) -> None:
        self._last_payload_bytes = 0
        self._history_enabled = history_enabled
        self._history_limit = history_limit

    async def initialize(self) -> None:
        """Create the checkpoint tables and index if absent (idempotent)."""
        async with get_async_cursor() as cur:
            await cur.execute(_DDL)
            if self._history_enabled:
                await cur.execute(_HISTORY_DDL)
        logger.info("agent_checkpoints schema initialized")

    async def _trim_history(self, cur: Any, run_id: str) -> None:
        """Trim history to the retention limit after a snapshot insert."""
        if self._history_limit > 0:
            await cur.execute(_HISTORY_TRIM, (run_id, run_id, self._history_limit))

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
            if self._history_enabled:
                await cur.execute(
                    _HISTORY_INSERT,
                    (
                        checkpoint.run_id,
                        checkpoint.version,
                        checkpoint.status,
                        checkpoint.step,
                        payload,
                    ),
                )
                await self._trim_history(cur, checkpoint.run_id)

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
            if updated and self._history_enabled:
                # Live row already patched: snapshot it server-side.
                await cur.execute(_HISTORY_SNAPSHOT_FROM_LIVE, (checkpoint.run_id,))
                await self._trim_history(cur, checkpoint.run_id)
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
            if self._history_enabled:
                await cur.execute(
                    "DELETE FROM agent_checkpoint_history WHERE run_id = %s",
                    (run_id,),
                )

    async def list_snapshots(self, run_id: str) -> list[dict[str, Any]]:
        """Version-ascending summaries of the run's recorded snapshots."""
        async with get_async_cursor(row_factory=dict_row) as cur:  # type: ignore
            await cur.execute(_HISTORY_LIST, (run_id,))
            rows = await cur.fetchall()
        return [
            {
                "version": r["version"],
                "status": r["status"],
                "step": r["step"],
                "updated_at": float(r["updated_at"]),
            }
            for r in rows
            if isinstance(r, dict)
        ]

    async def load_snapshot(self, run_id: str, version: int) -> Checkpoint | None:
        """Full checkpoint state as recorded at ``version``, or None."""
        async with get_async_cursor(row_factory=dict_row) as cur:  # type: ignore
            await cur.execute(_HISTORY_LOAD, (run_id, version))
            row = await cur.fetchone()
        if not row or not isinstance(row, dict):
            return None
        data = row["data"]
        if isinstance(data, str):
            data = orjson.loads(data)
        return Checkpoint.from_dict(data)

    async def list_runs(
        self,
        *,
        tenant_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Recent run summaries in any state, newest first.

        Complements :meth:`list_resumable` (which answers "what must crash
        recovery pick up") with the operator read path: completed and failed
        runs stay inspectable after the fact.
        """
        async with get_async_cursor(row_factory=dict_row) as cur:  # type: ignore
            await cur.execute(
                _RUN_LIST,
                {
                    "tenant_id": tenant_id,
                    "status": status,
                    "limit": max(1, min(limit, 500)),
                },
            )
            rows = await cur.fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            data = row["data"]
            if isinstance(data, str):
                data = orjson.loads(data)
            out.append(summarize_run(data))
        return out

    async def list_resumable(self, tenant_id: str | None = None) -> list[str]:
        """Return resumable ``run_id``s (crash recovery + paused approvals)."""
        sql = "SELECT run_id FROM agent_checkpoints WHERE status = ANY(%s)"
        params: list[Any] = [list(RESUMABLE_STATUSES)]
        if tenant_id is not None:
            sql += " AND tenant_id = %s"
            params.append(tenant_id)
        async with get_async_cursor(row_factory=dict_row) as cur:  # type: ignore
            await cur.execute(sql, params)
            rows = await cur.fetchall()
        return [r["run_id"] for r in rows if isinstance(r, dict)]


__all__ = ["PostgresCheckpointStore"]
