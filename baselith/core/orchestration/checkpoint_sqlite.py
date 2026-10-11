"""SQLite checkpoint store — durable runs without a Postgres instance.

Fills the gap between the two existing backends: ``memory`` loses every run
on restart and ``postgres`` needs a running server. Development laptops,
air-gapped deployments and single-node installs get crash-durable resume
from a single file instead.

Storage is stdlib :mod:`sqlite3`, following the
:class:`baselith.core.observability.audit_chain.SQLiteAuditSink` conventions: WAL
journal, one shared connection guarded by an ``RLock``, blocking statements
hopped off the event loop via ``run_in_executor``. The full checkpoint is
persisted as one JSON document per run (the checkpoint is a JSON snapshot
by contract); the filterable columns are duplicated for the listing paths.

``version``, ``owner`` and ``lease_expires_at`` are real columns: writes are a
compare-and-swap on ``version`` and the lease is claimed/renewed/released with
conditional updates, exactly like the Postgres backend, so several processes
sharing one file still exclude each other. Files created before those columns
existed are upgraded in place on open.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from sqlite3 import Connection, IntegrityError, Row, connect
from threading import RLock
from typing import Any

from baselith.core.orchestration.checkpoint import (
    DEFAULT_RESUMABLE_LIMIT,
    MAX_RESUMABLE_LIMIT,
    RESUMABLE_STATUSES,
    Checkpoint,
)
from baselith.core.orchestration.checkpoint_errors import (
    CheckpointConflictError,
    CheckpointLeaseHeldError,
)
from baselith.core.orchestration.checkpoint_memory import summarize_run

_SCHEMA = """
CREATE TABLE IF NOT EXISTS checkpoints (
    run_id     TEXT PRIMARY KEY,
    tenant_id  TEXT,
    status     TEXT NOT NULL,
    updated_at REAL NOT NULL,
    data       TEXT NOT NULL,
    version    INTEGER NOT NULL DEFAULT 0,
    owner      TEXT,
    lease_expires_at REAL
);
CREATE INDEX IF NOT EXISTS idx_checkpoints_status
    ON checkpoints (status, updated_at);
CREATE TABLE IF NOT EXISTS checkpoint_history (
    run_id     TEXT NOT NULL,
    version    INTEGER NOT NULL,
    status     TEXT NOT NULL,
    step       INTEGER NOT NULL,
    updated_at REAL NOT NULL,
    data       TEXT NOT NULL,
    PRIMARY KEY (run_id, version)
);
"""

#: Columns added after the first release; ``_upgrade_schema`` adds them to an
#: older file (``ALTER TABLE ... ADD COLUMN`` has no IF NOT EXISTS in SQLite).
_LATE_COLUMNS = (
    ("version", "INTEGER NOT NULL DEFAULT 0"),
    ("owner", "TEXT"),
    ("lease_expires_at", "REAL"),
)

_SELECT_ROW = (
    "SELECT data, version, owner, lease_expires_at FROM checkpoints WHERE run_id = ?"
)


def _row_to_checkpoint(row: Row) -> Checkpoint:
    """The JSON document with the authoritative columns laid over it.

    Lease operations update only the columns, so the document's copies of
    ``version`` / ``owner`` / ``lease_expires_at`` may lag behind.
    """
    data = json.loads(row["data"])
    data["version"] = row["version"]
    data["owner"] = row["owner"]
    data["lease_expires_at"] = row["lease_expires_at"]
    return Checkpoint.from_dict(data)


class SQLiteCheckpointStore:
    """File-backed :class:`~baselith.core.orchestration.checkpoint.CheckpointStore`.

    Args:
        path: Database file; parent directories are created.
        history_enabled: Also record an immutable snapshot per version
            (state history / time-travel), as the other backends do.
        history_limit: Per-run cap on retained snapshots (newest kept);
            0 means unlimited.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        history_enabled: bool = False,
        history_limit: int = 200,
    ) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._history_enabled = history_enabled
        self._history_limit = history_limit
        self._conn: Connection = connect(
            str(self._path), check_same_thread=False, isolation_level=None
        )
        self._conn.row_factory = Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        self._conn.executescript(_SCHEMA)
        self._upgrade_schema()
        self._lock = RLock()

    def _upgrade_schema(self) -> None:
        """Add the late columns to a file created by an older release."""
        existing = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(checkpoints)").fetchall()
        }
        for name, ddl in _LATE_COLUMNS:
            if name not in existing:
                self._conn.execute(f"ALTER TABLE checkpoints ADD COLUMN {name} {ddl}")
        if "version" not in existing:
            # The version used to live in the document only.
            self._conn.execute(
                "UPDATE checkpoints SET version ="
                " COALESCE(json_extract(data, '$.version'), 0)"
            )

    @property
    def history_enabled(self) -> bool:
        """Whether every save also records an immutable per-version snapshot.

        Every store exposes ``list_snapshots``; only a store built with
        ``history_enabled=True`` ever fills it, so readers ask this instead of
        inferring history from the method's presence.
        """
        return self._history_enabled

    def close(self) -> None:
        """Close the underlying connection (tests / shutdown)."""
        with self._lock:
            self._conn.close()

    async def _run(self, fn: Any, *args: Any) -> Any:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, fn, *args)

    # ------------------------------------------------------------------ write

    async def save(self, checkpoint: Checkpoint) -> None:
        """Insert, or update while the stored version is still ours (CAS).

        Raises:
            CheckpointConflictError: The stored version moved on.
        """
        expected = checkpoint.version
        data = checkpoint.to_dict()
        data["updated_at"] = time.time()
        data["version"] = expected + 1
        await self._run(self._write, data, expected)
        checkpoint.updated_at = data["updated_at"]
        checkpoint.version = expected + 1

    async def save_step(
        self,
        checkpoint: Checkpoint,
        key: str,
        entry: dict[str, Any],
        trajectory_entry: dict[str, Any],
    ) -> None:
        """Persist the run after one new step.

        Same compare-and-swap and bookkeeping as :meth:`save`. The whole
        document is rewritten — a single-file backend has no partial-update
        fast path worth the complexity; the arguments are already reflected
        in ``checkpoint`` by the replay manager.
        """
        del key, entry, trajectory_entry
        await self.save(checkpoint)

    def _write(self, data: dict[str, Any], expected: int) -> None:
        document = json.dumps(data, default=str)
        row = (
            data.get("tenant_id"),
            data["status"],
            data["updated_at"],
            document,
            data["version"],
            data.get("owner"),
            data.get("lease_expires_at"),
        )
        with self._lock:
            updated = self._conn.execute(
                "UPDATE checkpoints SET tenant_id=?, status=?, updated_at=?,"
                " data=?, version=?, owner=?, lease_expires_at=?"
                " WHERE run_id = ? AND version = ?",
                (*row, data["run_id"], expected),
            ).rowcount
            if not updated:
                try:
                    self._conn.execute(
                        "INSERT INTO checkpoints (tenant_id, status, updated_at,"
                        " data, version, owner, lease_expires_at, run_id)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (*row, data["run_id"]),
                    )
                except IntegrityError as exc:
                    # The row exists at another version: a stale writer.
                    raise CheckpointConflictError(data["run_id"], expected) from exc
            if self._history_enabled:
                self._conn.execute(
                    "INSERT OR REPLACE INTO checkpoint_history"
                    " (run_id, version, status, step, updated_at, data)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        data["run_id"],
                        data["version"],
                        data["status"],
                        data["step"],
                        data["updated_at"],
                        document,
                    ),
                )
                if self._history_limit > 0:
                    self._conn.execute(
                        "DELETE FROM checkpoint_history WHERE run_id = ?"
                        " AND version NOT IN (SELECT version FROM"
                        " checkpoint_history WHERE run_id = ?"
                        " ORDER BY version DESC LIMIT ?)",
                        (data["run_id"], data["run_id"], self._history_limit),
                    )

    async def delete(self, run_id: str) -> None:
        def _delete() -> None:
            with self._lock:
                self._conn.execute(
                    "DELETE FROM checkpoints WHERE run_id = ?", (run_id,)
                )
                self._conn.execute(
                    "DELETE FROM checkpoint_history WHERE run_id = ?", (run_id,)
                )

        await self._run(_delete)

    # ------------------------------------------------------------------- read

    async def load(self, run_id: str) -> Checkpoint | None:
        def _load() -> Checkpoint | None:
            with self._lock:
                row = self._conn.execute(_SELECT_ROW, (run_id,)).fetchone()
            return _row_to_checkpoint(row) if row is not None else None

        result: Checkpoint | None = await self._run(_load)
        return result

    # ------------------------------------------------------------------ lease

    async def claim(
        self,
        run_id: str,
        owner: str,
        lease_seconds: float,
        *,
        now: float | None = None,
    ) -> Checkpoint | None:
        """Take the run's lease (see ``checkpoint_lease``); None when absent.

        Raises:
            CheckpointLeaseHeldError: Another owner's lease is still live.
        """
        current = time.time() if now is None else now

        def _claim() -> Checkpoint | None:
            with self._lock:
                # Taking over from another owner bumps the version (fencing).
                claimed = self._conn.execute(
                    "UPDATE checkpoints SET"
                    " version = version + (CASE WHEN owner IS ? THEN 0 ELSE 1 END),"
                    " owner = ?, lease_expires_at = ?"
                    " WHERE run_id = ? AND (owner IS NULL OR owner = ?"
                    " OR lease_expires_at IS NULL OR lease_expires_at <= ?)",
                    (owner, owner, current + lease_seconds, run_id, owner, current),
                ).rowcount
                row = self._conn.execute(_SELECT_ROW, (run_id,)).fetchone()
            if row is None:
                return None
            if not claimed:
                raise CheckpointLeaseHeldError(
                    run_id, row["owner"], row["lease_expires_at"]
                )
            return _row_to_checkpoint(row)

        result: Checkpoint | None = await self._run(_claim)
        return result

    async def renew_lease(
        self,
        run_id: str,
        owner: str,
        lease_seconds: float,
        *,
        now: float | None = None,
    ) -> bool:
        """Extend ``owner``'s lease; False when the run is no longer theirs."""
        expires = (time.time() if now is None else now) + lease_seconds

        def _renew() -> bool:
            with self._lock:
                return bool(
                    self._conn.execute(
                        "UPDATE checkpoints SET lease_expires_at = ?"
                        " WHERE run_id = ? AND owner = ?",
                        (expires, run_id, owner),
                    ).rowcount
                )

        result: bool = await self._run(_renew)
        return result

    async def release_lease(self, run_id: str, owner: str) -> None:
        """Clear the lease if ``owner`` still holds it."""

        def _release() -> None:
            with self._lock:
                self._conn.execute(
                    "UPDATE checkpoints SET owner = NULL, lease_expires_at = NULL"
                    " WHERE run_id = ? AND owner = ?",
                    (run_id, owner),
                )

        await self._run(_release)

    async def list_resumable(
        self, tenant_id: str | None = None, *, limit: int | None = None
    ) -> list[str]:
        """Resumable ``run_id``s, bounded like the other backends."""
        page_size = DEFAULT_RESUMABLE_LIMIT if limit is None else limit
        page_size = max(1, min(page_size, MAX_RESUMABLE_LIMIT))

        def _query() -> list[str]:
            sql = "SELECT run_id FROM checkpoints WHERE status IN (?, ?)"
            params: list[Any] = list(RESUMABLE_STATUSES)
            if tenant_id is not None:
                sql += " AND tenant_id = ?"
                params.append(tenant_id)
            sql += " ORDER BY updated_at LIMIT ?"
            params.append(page_size)
            with self._lock:
                rows = self._conn.execute(sql, params).fetchall()
            return [row["run_id"] for row in rows]

        result: list[str] = await self._run(_query)
        return result

    async def list_runs(
        self,
        *,
        tenant_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Recent run summaries, newest first (the run-explorer read path)."""

        def _query() -> list[dict[str, Any]]:
            sql = "SELECT data FROM checkpoints"
            clauses: list[str] = []
            params: list[Any] = []
            if tenant_id is not None:
                # An unset tenant belongs to the default one, matching the
                # other backends' filter semantics.
                clauses.append("COALESCE(tenant_id, 'default') = ?")
                params.append(tenant_id)
            if status is not None:
                clauses.append("status = ?")
                params.append(status)
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += " ORDER BY updated_at DESC"
            if limit:
                sql += " LIMIT ?"
                params.append(max(0, limit))
            with self._lock:
                rows = self._conn.execute(sql, params).fetchall()
            return [summarize_run(json.loads(row["data"])) for row in rows]

        result: list[dict[str, Any]] = await self._run(_query)
        return result

    async def list_snapshots(self, run_id: str) -> list[dict[str, Any]]:
        """Version-ascending summaries of the run's recorded snapshots."""

        def _query() -> list[dict[str, Any]]:
            with self._lock:
                rows = self._conn.execute(
                    "SELECT version, status, step, updated_at FROM"
                    " checkpoint_history WHERE run_id = ? ORDER BY version",
                    (run_id,),
                ).fetchall()
            return [dict(row) for row in rows]

        result: list[dict[str, Any]] = await self._run(_query)
        return result

    async def load_snapshot(self, run_id: str, version: int) -> Checkpoint | None:
        """Full checkpoint state as recorded at ``version``, or None."""

        def _query() -> str | None:
            with self._lock:
                row = self._conn.execute(
                    "SELECT data FROM checkpoint_history WHERE run_id = ?"
                    " AND version = ?",
                    (run_id, version),
                ).fetchone()
            return row["data"] if row is not None else None

        document = await self._run(_query)
        return Checkpoint.from_dict(json.loads(document)) if document else None


__all__ = ["SQLiteCheckpointStore"]
