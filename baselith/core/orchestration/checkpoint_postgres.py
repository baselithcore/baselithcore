"""Postgres-backed :class:`CheckpointStore` for durable agent-loop resume.

Persists checkpoints across process restarts in an ``agent_checkpoints`` table.
Follows the same conventions as ``baselith.core.storage.postgres``: the shared async
connection pool from ``baselith.core.db.connection``, tenant-scoped rows, and idempotent
``CREATE TABLE IF NOT EXISTS`` self-initialization (no separate migration
required).

Writes are compare-and-swap on the ``version`` column and the per-run lease
lives in the ``owner`` / ``lease_expires_at`` columns (migration 013); the SQL
is in :mod:`baselith.core.orchestration.checkpoint_postgres_sql`.

Lease expiries are kept on the **database clock**, so replicas whose clocks
disagree still agree on whether a lease is live. ``Checkpoint.lease_expires_at``
stays on the local clock in process; this store converts at the boundary by
way of durations, which no clock offset affects: a write sends the seconds left
(``lease_expires_at - time.time()``) and the server adds its own ``now()``; a
read returns the seconds left by the server clock and the store adds
``time.time()``.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import orjson
from psycopg.rows import dict_row

from baselith.core.db.connection import get_async_cursor, system_tenant_scope
from baselith.core.db.ddl import skip_runtime_ddl
from baselith.core.observability.logging import get_logger
from baselith.core.orchestration.checkpoint import (
    DEFAULT_RESUMABLE_LIMIT,
    MAX_RESUMABLE_LIMIT,
    RESUMABLE_STATUSES,
    STATUS_RUNNING,
    Checkpoint,
)
from baselith.core.orchestration.checkpoint_errors import (
    CheckpointConflictError,
    CheckpointLeaseHeldError,
)
from baselith.core.orchestration.checkpoint_memory import summarize_run
from baselith.core.orchestration.checkpoint_postgres_sql import (
    _CLAIM,
    _HISTORY_INSERT,
    _HISTORY_LIST,
    _HISTORY_LOAD,
    _HISTORY_SNAPSHOT_FROM_LIVE,
    _HISTORY_TRIM,
    _LOAD,
    _RELEASE,
    _RENEW,
    _STEP_UPDATE,
    _UPSERT,
    _run_list_query,
)

logger = get_logger(__name__)

_DDL = """
CREATE TABLE IF NOT EXISTS agent_checkpoints (
    run_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL DEFAULT 'default',
    status TEXT NOT NULL DEFAULT 'running',
    data JSONB NOT NULL,
    version INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    owner TEXT,
    lease_expires_at DOUBLE PRECISION
);
ALTER TABLE agent_checkpoints ADD COLUMN IF NOT EXISTS owner TEXT;
ALTER TABLE agent_checkpoints
    ADD COLUMN IF NOT EXISTS lease_expires_at DOUBLE PRECISION;
CREATE INDEX IF NOT EXISTS idx_agent_checkpoints_resumable
    ON agent_checkpoints(tenant_id, status);
"""


def _lease_remaining(checkpoint: Checkpoint) -> float | None:
    """Seconds left on the in-process (local-clock) lease; None if unleased."""
    expires = checkpoint.lease_expires_at
    return None if expires is None else float(expires) - time.time()


def _local_expiry(remaining: Any) -> float | None:
    """A ``lease_remaining`` read from the server, as a local-clock expiry."""
    return None if remaining is None else time.time() + float(remaining)


def _clock_shift(now: float | None) -> float:
    """Offset of a caller-supplied ``now`` from the local clock (0 when unset).

    The lease methods keep their injectable ``now`` (tests drive expiry with
    it); it is applied to the database clock as a shift, never as an absolute
    time, so production calls (``now=None``) use the server clock unaltered.
    """
    return 0.0 if now is None else now - time.time()


def _row_to_checkpoint(row: dict[str, Any]) -> Checkpoint:
    """The JSONB document with the authoritative columns laid over it.

    Lease statements update only the columns, so the document's copies of
    ``version`` / ``owner`` / ``lease_expires_at`` may lag behind. The lease
    column arrives as ``lease_remaining`` (server-clock seconds left) and is
    converted to a local-clock expiry; a row carrying the absolute
    ``lease_expires_at`` instead is taken as is.
    """
    data = row["data"]
    # psycopg returns JSONB as a parsed object; tolerate a raw string too.
    if isinstance(data, str):
        data = orjson.loads(data)
    for column in ("version", "owner", "lease_expires_at"):
        if column in row:
            data[column] = row[column]
    if "lease_remaining" in row:
        data["lease_expires_at"] = _local_expiry(row["lease_remaining"])
    return Checkpoint.from_dict(data)


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

# Above this size the JSON dump is pushed off the event loop: the checkpoint
# re-serializes the whole accumulated steps map on every tool step, so long
# runs with large tool outputs would otherwise stall the loop progressively.
_OFFLOAD_THRESHOLD_BYTES = 256 * 1024


class PostgresCheckpointStore:
    """Durable checkpoint persistence backed by PostgreSQL.

    With ``history_enabled`` every save also appends an immutable snapshot row
    to ``agent_checkpoint_history`` keyed ``(run_id, version)`` — the substrate
    for state history / time-travel (see
    :mod:`baselith.core.orchestration.checkpoint_history`). Per run, only the newest
    ``history_limit`` snapshots are retained (0 = unlimited).
    """

    def __init__(self, history_enabled: bool = False, history_limit: int = 200) -> None:
        self._last_payload_bytes = 0
        self._history_enabled = history_enabled
        self._history_limit = history_limit

    @property
    def history_enabled(self) -> bool:
        """Whether every save also records an immutable per-version snapshot.

        Every store exposes ``list_snapshots``; only a store built with
        ``history_enabled=True`` ever fills it, so readers ask this instead of
        inferring history from the method's presence.
        """
        return self._history_enabled

    async def initialize(self) -> None:
        """Create the checkpoint tables and index if absent (idempotent).

        Runs inside :func:`baselith.core.db.connection.system_tenant_scope`: the first
        touch of this store is often out of request (a worker, a script, an
        import-time bootstrap), and under ``DB_RLS_ENABLED`` the pool refuses to
        bind a tenant nobody declared. Schema work belongs to the deployment,
        not to a tenant.
        """
        if skip_runtime_ddl(
            "checkpoint store", "agent_checkpoints, agent_checkpoint_history"
        ):
            return
        with system_tenant_scope():
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
        """Insert, or update while the stored version is still ours (CAS).

        Bumps ``version``/``updated_at`` in lock-step with
        :class:`~baselith.core.orchestration.checkpoint.InMemoryCheckpointStore` so the
        stores are behaviourally interchangeable.

        Raises:
            CheckpointConflictError: The stored version moved on.
        """
        expected = checkpoint.version
        checkpoint.updated_at = time.time()
        checkpoint.version = expected + 1
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
                    checkpoint.owner,
                    _lease_remaining(checkpoint),
                    expected,
                    checkpoint.owner,
                ),
            )
            if not cur.rowcount:
                checkpoint.version = expected
                raise CheckpointConflictError(checkpoint.run_id, expected)
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
        """Persist ONE new step without re-serializing the accumulated steps.

        ``CheckpointManager.run_step`` calls this after mutating the in-memory
        checkpoint. Only the new step and trajectory line are appended; every
        other field (budget, plugin data, inputs, lease, …) is merged in, so
        a later load() equals one after a full save. Cumulative bytes written
        over an n-step run stay O(n) in steps. Version/updated_at bookkeeping
        stays in lock-step with :meth:`save`. When nothing matched (no row
        yet, or a version conflict) it falls back to the full upsert, which
        inserts the first row or raises :class:`CheckpointConflictError`.
        """
        expected = checkpoint.version
        new_updated_at = time.time()
        new_version = expected + 1
        head = checkpoint.head_dict()
        head["version"] = new_version
        head["updated_at"] = new_updated_at
        params = (
            orjson.dumps(head).decode(),
            key,
            orjson.dumps(entry).decode(),
            orjson.dumps([trajectory_entry]).decode(),
            checkpoint.status,
            new_version,
            checkpoint.owner,
            _lease_remaining(checkpoint),
            checkpoint.run_id,
            expected,
            checkpoint.owner,
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
        # No row yet (first persist of this run) or a stale version: the full
        # path inserts the former and raises on the latter.
        await self.save(checkpoint)

    async def load(self, run_id: str) -> Checkpoint | None:
        """Load a checkpoint by ``run_id``, or None if absent."""
        async with get_async_cursor(row_factory=dict_row) as cur:
            await cur.execute(_LOAD, (run_id,))
            row = await cur.fetchone()
        if not row or not isinstance(row, dict):
            return None
        return _row_to_checkpoint(row)

    async def claim(
        self,
        run_id: str,
        owner: str,
        lease_seconds: float,
        *,
        now: float | None = None,
    ) -> Checkpoint | None:
        """Take the run's lease (see ``checkpoint_lease``); None when absent.

        One conditional ``UPDATE ... RETURNING``: exactly one of two racing
        claimants matches. Only a refused claim pays the second round trip
        that tells "held" from "absent".

        Expiry and the "has it expired" test both use the database clock, so
        a replica with a skewed clock cannot take over a live lease.

        Raises:
            CheckpointLeaseHeldError: Another owner's lease is still live.
        """
        shift = _clock_shift(now)
        async with get_async_cursor(row_factory=dict_row) as cur:
            await cur.execute(
                _CLAIM,
                (owner, owner, shift + lease_seconds, run_id, owner, shift),
            )
            row = await cur.fetchone()
            if not row or not isinstance(row, dict):
                await cur.execute(_LOAD, (run_id,))
                held = await cur.fetchone()
                if not held or not isinstance(held, dict):
                    return None
                raise CheckpointLeaseHeldError(
                    run_id,
                    held.get("owner"),
                    _local_expiry(held.get("lease_remaining")),
                )
        return _row_to_checkpoint(row)

    async def renew_lease(
        self,
        run_id: str,
        owner: str,
        lease_seconds: float,
        *,
        now: float | None = None,
    ) -> bool:
        """Extend ``owner``'s lease; False when the run is no longer theirs.

        The new expiry is the database clock plus ``lease_seconds``.
        """
        async with get_async_cursor() as cur:
            await cur.execute(
                _RENEW, (_clock_shift(now) + lease_seconds, run_id, owner)
            )
            return bool(cur.rowcount)

    async def release_lease(self, run_id: str, owner: str) -> None:
        """Clear the lease if ``owner`` still holds it."""
        async with get_async_cursor() as cur:
            await cur.execute(_RELEASE, (run_id, owner))

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
        async with get_async_cursor(row_factory=dict_row) as cur:
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
        async with get_async_cursor(row_factory=dict_row) as cur:
            await cur.execute(_HISTORY_LOAD, (run_id, version))
            row = await cur.fetchone()
        if not row or not isinstance(row, dict):
            return None
        return _row_to_checkpoint(row)

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
        sql, params = _run_list_query(tenant_id, status, max(1, min(limit, 500)))
        async with get_async_cursor(row_factory=dict_row) as cur:
            await cur.execute(sql, params)
            rows = await cur.fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            data = row["data"]
            if isinstance(data, str):
                data = orjson.loads(data)
            summary = summarize_run(data)
            # The trajectory itself was stripped server-side; its length wasn't.
            summary["trajectory_length"] = int(row.get("trajectory_length") or 0)
            out.append(summary)
        return out

    async def list_resumable(
        self, tenant_id: str | None = None, *, limit: int | None = None
    ) -> list[str]:
        """Return resumable ``run_id``s (crash recovery + paused approvals).

        Always bounded: a crash that leaves a large backlog of ``running`` rows
        would otherwise stream the whole table into one list at startup.

        The page is ordered ``running`` first, then oldest first. Both halves
        matter: ``awaiting_approval`` runs can sit resumable indefinitely
        (nobody has decided yet), so without the status preference a long queue
        of pending approvals would fill every page and starve crash recovery,
        which only re-enters ``running`` runs. Within a status the oldest runs
        go first, so a backlog drains in arrival order across sweeps.

        Args:
            tenant_id: Optional tenant scope.
            limit: Page size; ``None`` uses
                :data:`~baselith.core.orchestration.checkpoint.DEFAULT_RESUMABLE_LIMIT`
                and any value is clamped to ``[1, MAX_RESUMABLE_LIMIT]``.
        """
        page_size = DEFAULT_RESUMABLE_LIMIT if limit is None else limit
        page_size = max(1, min(page_size, MAX_RESUMABLE_LIMIT))
        sql = "SELECT run_id FROM agent_checkpoints WHERE status = ANY(%s)"
        params: list[Any] = [list(RESUMABLE_STATUSES)]
        if tenant_id is not None:
            sql += " AND tenant_id = %s"
            params.append(tenant_id)
        # ``status <> 'running'`` is false (sorts first) for the runs recovery
        # can actually re-enter.
        sql += " ORDER BY (status <> %s), updated_at ASC LIMIT %s"
        params.extend([STATUS_RUNNING, page_size])
        async with get_async_cursor(row_factory=dict_row) as cur:
            await cur.execute(sql, params)
            rows = await cur.fetchall()
        return [r["run_id"] for r in rows if isinstance(r, dict)]


__all__ = ["PostgresCheckpointStore"]
