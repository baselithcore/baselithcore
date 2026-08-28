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

Every statement runs on a worker thread via :func:`asyncio.to_thread`: SQLite is
blocking disk I/O, and issuing it from a coroutine stalls the whole event loop
(every in-flight request with it) for the duration of the write. One
``to_thread`` hop covers a complete unit of work — lock, statement, fetch, JSON
decode and domain rehydration — so the round-trip cost is paid once, not once
per step.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import Callable
from pathlib import Path
from threading import RLock
from typing import Any

from core.incidents.ai_act import AiActSeriousIncident
from core.incidents.dora import DoraIncident
from core.incidents.gdpr import PersonalDataBreach
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

    # -- Blocking units of work (run on a worker thread) -------------------

    def _upsert(self, key: str, payload: dict[str, Any]) -> None:
        blob = json.dumps(payload, sort_keys=True)
        with self._lock:
            self._conn.execute(
                f"INSERT INTO {self._TABLE} (id, data) VALUES (?, ?) "  # nosec B608
                "ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                (key, blob),
            )

    def _fetch[T](self, key: str, factory: Callable[[dict[str, Any]], T]) -> T | None:
        with self._lock:
            cur = self._conn.execute(
                f"SELECT data FROM {self._TABLE} WHERE id = ?",  # nosec B608
                (key,),
            )
            row = cur.fetchone()
        return factory(json.loads(row[0])) if row is not None else None

    def _fetch_all[T](self, factory: Callable[[dict[str, Any]], T]) -> list[T]:
        with self._lock:
            cur = self._conn.execute(
                f"SELECT data FROM {self._TABLE} ORDER BY id ASC"  # nosec B608
            )
            rows = cur.fetchall()
        return [factory(json.loads(r[0])) for r in rows]

    # -- Async surface: exactly one ``to_thread`` hop per operation ---------

    async def _save(self, key: str, payload: dict[str, Any]) -> None:
        await asyncio.to_thread(self._upsert, key, payload)

    async def _load[T](
        self, key: str, factory: Callable[[dict[str, Any]], T]
    ) -> T | None:
        return await asyncio.to_thread(self._fetch, key, factory)

    async def _load_all[T](self, factory: Callable[[dict[str, Any]], T]) -> list[T]:
        return await asyncio.to_thread(self._fetch_all, factory)

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:  # close must never raise
                pass


class SQLiteIncidentStore(_SQLiteJsonStore):
    """Durable SQLite implementation of the NIS2 ``IncidentStore`` protocol."""

    _TABLE = "security_incidents"

    async def save(self, incident: SecurityIncident) -> None:
        await self._save(incident.id, incident.to_dict())

    async def get(self, incident_id: str) -> SecurityIncident | None:
        return await self._load(incident_id, SecurityIncident.from_dict)

    async def list_all(self) -> list[SecurityIncident]:
        return await self._load_all(SecurityIncident.from_dict)


class SQLiteDoraIncidentStore(_SQLiteJsonStore):
    """Durable SQLite implementation of the DORA ``DoraIncidentStore`` protocol."""

    _TABLE = "dora_incidents"

    async def save(self, incident: DoraIncident) -> None:
        await self._save(incident.id, incident.to_dict())

    async def get(self, incident_id: str) -> DoraIncident | None:
        return await self._load(incident_id, DoraIncident.from_dict)

    async def list_all(self) -> list[DoraIncident]:
        return await self._load_all(DoraIncident.from_dict)


class SQLiteAiActIncidentStore(_SQLiteJsonStore):
    """Durable SQLite implementation of the ``AiActIncidentStore`` protocol."""

    _TABLE = "ai_act_incidents"

    async def save(self, incident: AiActSeriousIncident) -> None:
        await self._save(incident.id, incident.to_dict())

    async def get(self, incident_id: str) -> AiActSeriousIncident | None:
        return await self._load(incident_id, AiActSeriousIncident.from_dict)

    async def list_all(self) -> list[AiActSeriousIncident]:
        return await self._load_all(AiActSeriousIncident.from_dict)


class SQLiteBreachStore(_SQLiteJsonStore):
    """Durable SQLite implementation of the GDPR ``BreachStore`` protocol."""

    _TABLE = "personal_data_breaches"

    async def save(self, breach: PersonalDataBreach) -> None:
        await self._save(breach.id, breach.to_dict())

    async def get(self, breach_id: str) -> PersonalDataBreach | None:
        return await self._load(breach_id, PersonalDataBreach.from_dict)

    async def list_all(self) -> list[PersonalDataBreach]:
        return await self._load_all(PersonalDataBreach.from_dict)


__all__ = [
    "SQLiteAiActIncidentStore",
    "SQLiteBreachStore",
    "SQLiteDoraIncidentStore",
    "SQLiteIncidentStore",
]
