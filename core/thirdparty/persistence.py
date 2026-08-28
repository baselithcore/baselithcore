"""Durable SQLite backend for the DORA Register of Information.

The in-memory :class:`~core.thirdparty.register.InMemoryRegisterStore` survives
only for the process lifetime, so the register of ICT providers, functions, and
contractual arrangements is lost on restart — unacceptable for a record DORA
Art. 28(3) requires to be kept up to date. This module adds an opt-in,
file-based SQLite store that persists each record as a JSON blob keyed by its
id (providers/functions by ``id``, arrangements by ``reference_number``), so a
cold start rehydrates the full register.

SQLite (stdlib :mod:`sqlite3`) is chosen deliberately — the same rationale as
:mod:`plugins.baselithmed.persistence`: it is in the standard library (zero new
dependencies, no infra), register writes are low-volume, and the same
``RegisterStore`` protocol can later be implemented against Postgres without
touching service code. ``check_same_thread=False`` plus an internal
:class:`~threading.RLock` makes the single connection safe to share across the
event loop and worker threads; ``PRAGMA journal_mode=WAL`` keeps reads
non-blocking. Selected only when ``THIRDPARTY_REGISTER_DB_PATH`` is set; unset
keeps the in-memory default.

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

from core.thirdparty.types import (
    ContractualArrangement,
    ICTFunction,
    ICTProvider,
)

_PROVIDERS = "register_providers"
_FUNCTIONS = "register_functions"
_ARRANGEMENTS = "register_arrangements"


class SQLiteRegisterStore:
    """Durable SQLite implementation of the ``RegisterStore`` protocol.

    Holds the three register collections in one SQLite file, each as its own
    ``(id TEXT PRIMARY KEY, data TEXT)`` JSON table sharing a single connection
    and lock.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(self._path), check_same_thread=False, isolation_level=None
        )
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        for table in (_PROVIDERS, _FUNCTIONS, _ARRANGEMENTS):
            self._conn.execute(
                f"CREATE TABLE IF NOT EXISTS {table} "
                "(id TEXT PRIMARY KEY, data TEXT NOT NULL);"
            )
        self._lock = RLock()

    # -- Blocking units of work (run on a worker thread) -------------------

    def _upsert(self, table: str, key: str, payload: dict[str, Any]) -> None:
        blob = json.dumps(payload, sort_keys=True)
        with self._lock:
            self._conn.execute(
                f"INSERT INTO {table} (id, data) VALUES (?, ?) "  # nosec B608
                "ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                (key, blob),
            )

    def _fetch[T](
        self, table: str, key: str, factory: Callable[[dict[str, Any]], T]
    ) -> T | None:
        with self._lock:
            cur = self._conn.execute(
                f"SELECT data FROM {table} WHERE id = ?",  # nosec B608
                (key,),
            )
            row = cur.fetchone()
        return factory(json.loads(row[0])) if row is not None else None

    def _fetch_all[T](
        self, table: str, factory: Callable[[dict[str, Any]], T]
    ) -> list[T]:
        with self._lock:
            cur = self._conn.execute(
                f"SELECT data FROM {table} ORDER BY id ASC"  # nosec B608
            )
            rows = cur.fetchall()
        return [factory(json.loads(r[0])) for r in rows]

    # -- Async surface: exactly one ``to_thread`` hop per operation ---------

    async def _save(self, table: str, key: str, payload: dict[str, Any]) -> None:
        await asyncio.to_thread(self._upsert, table, key, payload)

    async def _load[T](
        self, table: str, key: str, factory: Callable[[dict[str, Any]], T]
    ) -> T | None:
        return await asyncio.to_thread(self._fetch, table, key, factory)

    async def _load_all[T](
        self, table: str, factory: Callable[[dict[str, Any]], T]
    ) -> list[T]:
        return await asyncio.to_thread(self._fetch_all, table, factory)

    # -- Providers ---------------------------------------------------------

    async def save_provider(self, provider: ICTProvider) -> None:
        await self._save(_PROVIDERS, provider.id, provider.to_dict())

    async def get_provider(self, provider_id: str) -> ICTProvider | None:
        return await self._load(_PROVIDERS, provider_id, ICTProvider.from_dict)

    async def list_providers(self) -> list[ICTProvider]:
        return await self._load_all(_PROVIDERS, ICTProvider.from_dict)

    # -- Functions ---------------------------------------------------------

    async def save_function(self, function: ICTFunction) -> None:
        await self._save(_FUNCTIONS, function.id, function.to_dict())

    async def get_function(self, function_id: str) -> ICTFunction | None:
        return await self._load(_FUNCTIONS, function_id, ICTFunction.from_dict)

    async def list_functions(self) -> list[ICTFunction]:
        return await self._load_all(_FUNCTIONS, ICTFunction.from_dict)

    # -- Arrangements ------------------------------------------------------

    async def save_arrangement(self, arrangement: ContractualArrangement) -> None:
        await self._save(
            _ARRANGEMENTS, arrangement.reference_number, arrangement.to_dict()
        )

    async def get_arrangement(
        self, reference_number: str
    ) -> ContractualArrangement | None:
        return await self._load(
            _ARRANGEMENTS, reference_number, ContractualArrangement.from_dict
        )

    async def list_arrangements(self) -> list[ContractualArrangement]:
        return await self._load_all(_ARRANGEMENTS, ContractualArrangement.from_dict)

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:  # close must never raise
                pass


__all__ = ["SQLiteRegisterStore"]
