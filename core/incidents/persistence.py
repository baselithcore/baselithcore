"""Durable SQLite backends for the regulatory incident subsystems.

The in-memory reference stores (:class:`~core.incidents.service.InMemoryIncidentStore`
and :class:`~core.incidents.dora_service.InMemoryDoraIncidentStore`) survive only
for the process lifetime, so NIS2/DORA incident records are lost on restart.
This module adds opt-in, file-based SQLite stores that persist each incident as
a JSON blob keyed by its id, so a cold start rehydrates the full record set.

SQLite (stdlib :mod:`sqlite3`) is chosen deliberately — the same rationale as
:mod:`plugins.baselithmed.persistence`:

    * it is in the Python standard library — zero new dependencies, no infra;
    * incident writes are low-volume (a handful per incident lifecycle), well
      within SQLite's single-writer model;
    * the same ``IncidentStore`` / ``DoraIncidentStore`` protocol can later be
      implemented against Postgres without touching service code.

``check_same_thread=False`` together with an internal :class:`~threading.RLock`
makes each store safe to share across the asyncio event loop and any worker
threads FastAPI may spawn; ``PRAGMA journal_mode=WAL`` keeps concurrent reads
non-blocking. Stores are opt-in and selected only when a DB path is configured
(``INCIDENT_DB_PATH`` / ``DORA_DB_PATH``); unset keeps the in-memory default.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from threading import RLock
from typing import Any

from core.incidents.dora import DoraIncident
from core.incidents.types import SecurityIncident


class _SQLiteJsonStore:
    """Single-table ``(id TEXT PRIMARY KEY, data TEXT)`` JSON store over SQLite.

    Subclasses set :attr:`_TABLE` and wrap the private helpers in the async
    persistence-protocol methods. The domain object is serialized to its
    ``to_dict()`` JSON and rehydrated via ``from_dict`` by the subclass.
    """

    _TABLE = "records"

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # ``check_same_thread=False`` + the internal RLock makes the connection
        # safe to share across the event loop and any worker threads.
        self._conn = sqlite3.connect(
            str(self._path), check_same_thread=False, isolation_level=None
        )
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        self._conn.execute(
            f"CREATE TABLE IF NOT EXISTS {self._TABLE} "
            "(id TEXT PRIMARY KEY, data TEXT NOT NULL);"
        )
        self._lock = RLock()

    def _upsert(self, key: str, payload: dict[str, Any]) -> None:
        blob = json.dumps(payload, sort_keys=True)
        with self._lock:
            self._conn.execute(
                f"INSERT INTO {self._TABLE} (id, data) VALUES (?, ?) "  # nosec B608
                "ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                (key, blob),
            )

    def _fetch(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            cur = self._conn.execute(
                f"SELECT data FROM {self._TABLE} WHERE id = ?",  # nosec B608
                (key,),
            )
            row = cur.fetchone()
        return json.loads(row[0]) if row is not None else None

    def _fetch_all(self) -> list[dict[str, Any]]:
        with self._lock:
            cur = self._conn.execute(
                f"SELECT data FROM {self._TABLE} ORDER BY id ASC"  # nosec B608
            )
            rows = cur.fetchall()
        return [json.loads(r[0]) for r in rows]

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass


class SQLiteIncidentStore(_SQLiteJsonStore):
    """Durable SQLite implementation of the NIS2 ``IncidentStore`` protocol."""

    _TABLE = "security_incidents"

    async def save(self, incident: SecurityIncident) -> None:
        self._upsert(incident.id, incident.to_dict())

    async def get(self, incident_id: str) -> SecurityIncident | None:
        data = self._fetch(incident_id)
        return SecurityIncident.from_dict(data) if data is not None else None

    async def list_all(self) -> list[SecurityIncident]:
        return [SecurityIncident.from_dict(d) for d in self._fetch_all()]


class SQLiteDoraIncidentStore(_SQLiteJsonStore):
    """Durable SQLite implementation of the DORA ``DoraIncidentStore`` protocol."""

    _TABLE = "dora_incidents"

    async def save(self, incident: DoraIncident) -> None:
        self._upsert(incident.id, incident.to_dict())

    async def get(self, incident_id: str) -> DoraIncident | None:
        data = self._fetch(incident_id)
        return DoraIncident.from_dict(data) if data is not None else None

    async def list_all(self) -> list[DoraIncident]:
        return [DoraIncident.from_dict(d) for d in self._fetch_all()]


__all__ = ["SQLiteDoraIncidentStore", "SQLiteIncidentStore"]
