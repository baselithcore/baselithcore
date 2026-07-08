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
"""

from __future__ import annotations

import json
import sqlite3
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

    def _upsert(self, table: str, key: str, payload: dict[str, Any]) -> None:
        blob = json.dumps(payload, sort_keys=True)
        with self._lock:
            self._conn.execute(
                f"INSERT INTO {table} (id, data) VALUES (?, ?) "  # nosec B608
                "ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                (key, blob),
            )

    def _fetch(self, table: str, key: str) -> dict[str, Any] | None:
        with self._lock:
            cur = self._conn.execute(
                f"SELECT data FROM {table} WHERE id = ?",  # nosec B608
                (key,),
            )
            row = cur.fetchone()
        return json.loads(row[0]) if row is not None else None

    def _fetch_all(self, table: str) -> list[dict[str, Any]]:
        with self._lock:
            cur = self._conn.execute(
                f"SELECT data FROM {table} ORDER BY id ASC"  # nosec B608
            )
            rows = cur.fetchall()
        return [json.loads(r[0]) for r in rows]

    # -- Providers ---------------------------------------------------------

    async def save_provider(self, provider: ICTProvider) -> None:
        self._upsert(_PROVIDERS, provider.id, provider.to_dict())

    async def get_provider(self, provider_id: str) -> ICTProvider | None:
        data = self._fetch(_PROVIDERS, provider_id)
        return ICTProvider.from_dict(data) if data is not None else None

    async def list_providers(self) -> list[ICTProvider]:
        return [ICTProvider.from_dict(d) for d in self._fetch_all(_PROVIDERS)]

    # -- Functions ---------------------------------------------------------

    async def save_function(self, function: ICTFunction) -> None:
        self._upsert(_FUNCTIONS, function.id, function.to_dict())

    async def get_function(self, function_id: str) -> ICTFunction | None:
        data = self._fetch(_FUNCTIONS, function_id)
        return ICTFunction.from_dict(data) if data is not None else None

    async def list_functions(self) -> list[ICTFunction]:
        return [ICTFunction.from_dict(d) for d in self._fetch_all(_FUNCTIONS)]

    # -- Arrangements ------------------------------------------------------

    async def save_arrangement(self, arrangement: ContractualArrangement) -> None:
        self._upsert(_ARRANGEMENTS, arrangement.reference_number, arrangement.to_dict())

    async def get_arrangement(
        self, reference_number: str
    ) -> ContractualArrangement | None:
        data = self._fetch(_ARRANGEMENTS, reference_number)
        return ContractualArrangement.from_dict(data) if data is not None else None

    async def list_arrangements(self) -> list[ContractualArrangement]:
        return [
            ContractualArrangement.from_dict(d) for d in self._fetch_all(_ARRANGEMENTS)
        ]

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass


__all__ = ["SQLiteRegisterStore"]
