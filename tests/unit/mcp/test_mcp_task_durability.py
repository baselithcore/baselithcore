"""MCP task handles survive a restart when a durable backend is configured.

Two halves:

* the :class:`~baselith.core.mcp.task_persistence.TaskBackend` contract, run
  against the in-memory reference backend and against PostgreSQL. The Postgres
  case needs a real server and the real-database opt-in::

      BASELITH_TEST_REAL_DB=1 python -m pytest tests/unit/mcp/test_mcp_task_durability.py

  It applies migration 015 to a throwaway schema, so the migration and the
  backend are tested together;
* end to end through ``MCPServer``: a "restart" is a new server whose fresh
  ``TaskStore`` shares only the backend with the old one.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from baselith.core.mcp.errors import InvalidParams
from baselith.core.mcp.server import MCPServer
from baselith.core.mcp.task_persistence import (
    ORPHAN_MESSAGE,
    InMemoryTaskBackend,
    TaskRow,
    orphaned_row,
    resolve_task_backend,
)
from baselith.core.mcp.tasks import FAILED, WORKING, TaskStore
from tests.unit.mcp.test_mcp_tasks import _message, _poll_until, _result, _server

MIGRATION_015 = (
    Path(__file__).resolve().parents[3]
    / "baselith"
    / "core"
    / "db"
    / "migrations"
    / "versions"
    / "015_mcp_tasks.py"
)


class _Op:
    def __init__(self, cur: Any) -> None:
        self._cur = cur

    def execute(self, sql: str) -> None:
        self._cur.execute(sql)


@contextlib.contextmanager
def _postgres_backend(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    from tests.integration.test_tool_ledger_postgres import (
        _conninfo,
        _pg_available,
        _real_db_enabled,
    )

    if not _real_db_enabled():
        pytest.skip("set BASELITH_TEST_REAL_DB=1 to run against a real Postgres")
    if not _pg_available():
        pytest.skip("PostgreSQL not reachable (docker compose up -d postgres)")

    import psycopg

    schema = f"itest_mcp_{uuid.uuid4().hex[:8]}"
    spec = importlib.util.spec_from_file_location("mig_015", MIGRATION_015)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with psycopg.connect(_conninfo(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(f"CREATE SCHEMA {schema}")
        cur.execute(f"SET search_path TO {schema}")
        migration.op = _Op(cur)
        migration.upgrade()

    @contextlib.asynccontextmanager
    async def _cursor(*, row_factory: Any = None) -> AsyncIterator[Any]:
        conn = await psycopg.AsyncConnection.connect(_conninfo(), autocommit=True)
        try:
            await conn.execute(f"SET search_path TO {schema}")
            kwargs = {"row_factory": row_factory} if row_factory is not None else {}
            async with conn.cursor(**kwargs) as cur:
                yield cur
        finally:
            await conn.close()

    from baselith.core.mcp.task_store_postgres import PostgresTaskBackend

    monkeypatch.setattr(
        "baselith.core.mcp.task_store_postgres.get_async_cursor", _cursor
    )
    try:
        yield PostgresTaskBackend()
    finally:
        with (
            psycopg.connect(_conninfo(), autocommit=True) as conn,
            conn.cursor() as cur,
        ):
            cur.execute(f"DROP SCHEMA {schema} CASCADE")


@pytest.fixture(params=["memory", "postgres"])
def backend(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Any]:
    if request.param == "memory":
        yield InMemoryTaskBackend()
    else:
        with _postgres_backend(monkeypatch) as pg:
            yield pg


def _row(task_id: str, **overrides: Any) -> TaskRow:
    now = time.time()
    base = TaskRow(
        task_id=task_id,
        status=WORKING,
        data={"ttl_ms": 60_000, "poll_interval_ms": 10, "result": None},
        expires_at=now + 60,
        owner="owner-a",
        lease_expires_at=now + 30,
        updated_at=now,
    )
    return replace(base, **overrides)


class TestBackendContract:
    @pytest.mark.asyncio
    async def test_saved_row_round_trips(self, backend: Any) -> None:
        row = _row(
            f"t-{uuid.uuid4().hex}",
            status="completed",
            data={"result": {"content": [{"type": "text", "text": "done"}]}},
        )
        await backend.save(row)

        loaded = await backend.load(row.task_id)

        assert loaded is not None
        assert loaded.status == "completed"
        assert loaded.data["result"]["content"][0]["text"] == "done"
        assert loaded.owner == "owner-a"

    @pytest.mark.asyncio
    async def test_expired_and_unknown_rows_do_not_load(self, backend: Any) -> None:
        row = _row(f"t-{uuid.uuid4().hex}", expires_at=time.time() - 1)
        await backend.save(row)

        assert await backend.load(row.task_id) is None
        assert await backend.load("never-issued") is None
        assert await backend.prune() >= 1

    @pytest.mark.asyncio
    async def test_live_lease_is_never_failed(self, backend: Any) -> None:
        row = _row(f"t-{uuid.uuid4().hex}")
        await backend.save(row)

        assert await backend.fail_orphaned(row, orphaned_row(row)) is False
        loaded = await backend.load(row.task_id)
        assert loaded is not None and loaded.status == WORKING

    @pytest.mark.asyncio
    async def test_lapsed_lease_is_failed_once(self, backend: Any) -> None:
        row = _row(f"t-{uuid.uuid4().hex}", lease_expires_at=time.time() - 1)
        await backend.save(row)

        assert await backend.fail_orphaned(row, orphaned_row(row)) is True
        # A second reader racing the first loses: the row is no longer live.
        assert await backend.fail_orphaned(row, orphaned_row(row)) is False
        loaded = await backend.load(row.task_id)
        assert loaded is not None
        assert loaded.status == FAILED
        assert loaded.data["error"]["message"] == ORPHAN_MESSAGE

    @pytest.mark.asyncio
    async def test_only_the_owner_renews(self, backend: Any) -> None:
        row = _row(f"t-{uuid.uuid4().hex}", lease_expires_at=time.time() - 1)
        await backend.save(row)

        await backend.renew([row.task_id], "someone-else", time.time() + 60)
        loaded = await backend.load(row.task_id)
        assert loaded is not None and not loaded.lease_live()

        await backend.renew([row.task_id], "owner-a", time.time() + 60)
        loaded = await backend.load(row.task_id)
        assert loaded is not None and loaded.lease_live()


def _restarted(old: MCPServer, backend: Any) -> MCPServer:
    """A new process: same tools, nothing in memory, the same backend."""
    del old
    server = _server()
    server._tasks_store = TaskStore(backend=backend)
    return server


class TestRestart:
    @pytest.mark.asyncio
    async def test_a_finished_task_is_readable_after_a_restart(self) -> None:
        backend = InMemoryTaskBackend()
        first = _server()
        first._tasks_store = TaskStore(backend=backend)
        created = await _result(first, "tools/call", {"name": "slow", "arguments": {}})
        first._release.set()
        await _poll_until(first, created["taskId"], "completed")
        await asyncio.sleep(0.01)  # the terminal write-through is scheduled

        second = _restarted(first, backend)
        done = await _result(second, "tasks/get", {"taskId": created["taskId"]})

        assert done["status"] == "completed"
        assert done["result"]["content"][0]["text"] == "done"
        # Already finished: cancel and update have nothing to do.
        await _result(second, "tasks/cancel", {"taskId": created["taskId"]})

    @pytest.mark.asyncio
    async def test_a_task_whose_process_died_is_failed_as_orphaned(self) -> None:
        backend = InMemoryTaskBackend()
        first = _server()
        first._tasks_store = TaskStore(backend=backend, lease_seconds=0.05)
        created = await _result(first, "tools/call", {"name": "slow", "arguments": {}})
        # The process dies: driver and heartbeat stop and nothing is written
        # (a crash runs no cleanup, so the row stays ``working``).
        first._tasks_store.backend = None
        first._tasks_store.cancel_all()
        first._tasks_store._tasks.clear()
        await asyncio.sleep(0.1)  # the lease lapses

        second = _restarted(first, backend)
        task = await _result(second, "tasks/get", {"taskId": created["taskId"]})

        assert task["status"] == FAILED
        assert task["error"]["message"] == ORPHAN_MESSAGE

    @pytest.mark.asyncio
    async def test_a_task_live_elsewhere_is_readable_but_not_steerable(self) -> None:
        backend = InMemoryTaskBackend()
        first = _server()
        first._tasks_store = TaskStore(backend=backend)
        created = await _result(first, "tools/call", {"name": "slow", "arguments": {}})

        replica = _restarted(first, backend)
        seen = await _result(replica, "tasks/get", {"taskId": created["taskId"]})
        assert seen["status"] == WORKING

        response = await replica.handle_message(
            _message("tasks/cancel", {"taskId": created["taskId"]})
        )
        assert response is not None
        assert "another server instance" in response["error"]["message"]

        first._release.set()
        await _poll_until(first, created["taskId"], "completed")
        await asyncio.sleep(0.01)
        done = await _result(replica, "tasks/get", {"taskId": created["taskId"]})
        assert done["status"] == "completed"

    @pytest.mark.asyncio
    async def test_the_heartbeat_keeps_a_long_task_owned(self) -> None:
        backend = InMemoryTaskBackend()
        first = _server()
        first._tasks_store = TaskStore(backend=backend, lease_seconds=0.06)
        created = await _result(first, "tools/call", {"name": "slow", "arguments": {}})
        await asyncio.sleep(0.2)  # several lease lengths

        replica = _restarted(first, backend)
        seen = await _result(replica, "tasks/get", {"taskId": created["taskId"]})

        assert seen["status"] == WORKING
        first._release.set()
        await _poll_until(first, created["taskId"], "completed")

    @pytest.mark.asyncio
    async def test_without_a_backend_a_restart_forgets_the_handle(self) -> None:
        first = _server()
        created = await _result(first, "tools/call", {"name": "slow", "arguments": {}})

        second = _server()
        with pytest.raises(InvalidParams, match="Unknown task"):
            await second._tasks_store.aget(created["taskId"])
        first._tasks_store.cancel_all()


def test_backend_selection() -> None:
    assert resolve_task_backend("memory") is None
    from baselith.core.mcp.task_store_postgres import PostgresTaskBackend

    assert isinstance(resolve_task_backend("postgres"), PostgresTaskBackend)


def test_a_failed_write_does_not_fail_the_task() -> None:
    class _Broken(InMemoryTaskBackend):
        async def save(self, row: TaskRow) -> None:
            raise RuntimeError("database down")

    async def scenario() -> str:
        store = TaskStore(backend=_Broken())
        task = store.create(60_000, 10)
        await store.persist(task)
        return store.get(task.task_id).task_id

    assert asyncio.run(scenario())
