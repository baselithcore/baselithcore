"""SQLite checkpoint store — durable runs without a Postgres instance.

Fills the gap between the two existing backends: ``memory`` loses every run
on restart and ``postgres`` needs a running server. Development laptops,
air-gapped deployments and single-node installs get crash-durable resume
from a single file instead.

Storage is stdlib :mod:`sqlite3`, following the
:class:`core.observability.audit_chain.SQLiteAuditSink` conventions: WAL
journal, one shared connection guarded by an ``RLock``, blocking statements
hopped off the event loop via ``run_in_executor``. The full checkpoint is
persisted as one JSON document per run (the checkpoint is a JSON snapshot
by contract); the filterable columns are duplicated for the listing paths.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from sqlite3 import Connection, Row, connect
from threading import RLock
from typing import Any

from core.orchestration.checkpoint import (
    DEFAULT_RESUMABLE_LIMIT,
    MAX_RESUMABLE_LIMIT,
    RESUMABLE_STATUSES,
    Checkpoint,
)
from core.orchestration.checkpoint_memory import summarize_run

_SCHEMA = """
CREATE TABLE IF NOT EXISTS checkpoints (
    run_id     TEXT PRIMARY KEY,
    tenant_id  TEXT,
    status     TEXT NOT NULL,
    updated_at REAL NOT NULL,
    data       TEXT NOT NULL
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


class SQLiteCheckpointStore:
    """File-backed :class:`~core.orchestration.checkpoint.CheckpointStore`.

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
        self._lock = RLock()

    def close(self) -> None:
        """Close the underlying connection (tests / shutdown)."""
        with self._lock:
            self._conn.close()

    async def _run(self, fn: Any, *args: Any) -> Any:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, fn, *args)

    # ------------------------------------------------------------------ write

    async def save(self, checkpoint: Checkpoint) -> None:
        checkpoint.updated_at = time.time()
        checkpoint.version += 1
        await self._run(self._upsert, checkpoint.to_dict())

    async def save_step(
        self,
        checkpoint: Checkpoint,
        key: str,
        entry: dict[str, Any],
        trajectory_entry: dict[str, Any],
    ) -> None:
        """Persist the run after one new step.

        Same version/updated_at bookkeeping as :meth:`save`. The whole
        document is rewritten — a single-file backend has no partial-update
        fast path worth the complexity; the arguments are already reflected
        in ``checkpoint`` by the replay manager.
        """
        del key, entry, trajectory_entry
        await self.save(checkpoint)

    def _upsert(self, data: dict[str, Any]) -> None:
        document = json.dumps(data, default=str)
        with self._lock:
            self._conn.execute(
                "INSERT INTO checkpoints (run_id, tenant_id, status, updated_at, data)"
                " VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(run_id) DO UPDATE SET tenant_id=excluded.tenant_id,"
                " status=excluded.status, updated_at=excluded.updated_at,"
                " data=excluded.data",
                (
                    data["run_id"],
                    data.get("tenant_id"),
                    data["status"],
                    data["updated_at"],
                    document,
                ),
            )
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
        def _load() -> str | None:
            with self._lock:
                row = self._conn.execute(
                    "SELECT data FROM checkpoints WHERE run_id = ?", (run_id,)
                ).fetchone()
            return row["data"] if row is not None else None

        document = await self._run(_load)
        return Checkpoint.from_dict(json.loads(document)) if document else None

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
