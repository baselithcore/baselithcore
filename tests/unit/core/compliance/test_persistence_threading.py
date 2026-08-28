"""The compliance SQLite stores must not run their I/O on the event loop.

Every store method is a coroutine, but SQLite is blocking disk I/O: executing a
statement inline would stall the loop — and every request in flight on it — for
the duration of the write. These tests pin the statements to a worker thread.
"""

from __future__ import annotations

import sqlite3
import threading
from typing import Any

from core.compliance.persistence import SQLiteAiSystemStore
from core.compliance.types import AiSystem, RiskCategory


class _ThreadRecordingConnection:
    """Proxy that records which thread executes each statement."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self.threads: list[int] = []

    def execute(self, *args: Any, **kwargs: Any) -> sqlite3.Cursor:
        self.threads.append(threading.get_ident())
        return self._conn.execute(*args, **kwargs)

    def close(self) -> None:
        self._conn.close()


def _system() -> AiSystem:
    return AiSystem(
        name="triage",
        risk_category=RiskCategory.HIGH_RISK,
    )


class TestEventLoopIsNotBlocked:
    async def test_statements_run_on_a_worker_thread(self, tmp_path):
        store = SQLiteAiSystemStore(tmp_path / "systems.db")
        probe = _ThreadRecordingConnection(store._conn)
        store._conn = probe  # type: ignore[assignment]
        loop_thread = threading.get_ident()
        try:
            system = _system()
            await store.save(system)
            assert (await store.get(system.id)) is not None
            assert len(await store.list_all()) == 1
            assert await store.delete(system.id) is True
        finally:
            store.close()

        # One statement per operation, none of them on the loop thread.
        assert len(probe.threads) == 4
        assert loop_thread not in probe.threads
