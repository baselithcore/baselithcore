"""Orphaned A2A tasks: a crash must not leave a poller waiting forever."""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from baselith.core.a2a.agent_card import AgentCard
from baselith.core.a2a.server import EchoA2AServer, InMemoryTaskStore, TaskStore
from baselith.core.a2a.task_orphans import (
    ORPHANED_METADATA_KEY,
    is_orphaned,
    sweep_orphaned_tasks,
)
from baselith.core.a2a.task_store_postgres import PostgresTaskStore
from baselith.core.a2a.types import Task, TaskState, TaskStatus


def _task(task_id: str, state: TaskState, age: float) -> Task:
    return Task(
        id=task_id,
        status=TaskStatus(state=state, timestamp=time.time() - age),
    )


def _server(store: TaskStore, timeout: float | None = 60.0) -> EchoA2AServer:
    card = AgentCard(name="echo", description="echo", url="http://localhost")
    return EchoA2AServer(card, task_store=store, orphan_timeout_seconds=timeout)


def _get(task_id: str, method: str = "tasks/get") -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": 1, "method": method, "params": {"id": task_id}}


class TestIsOrphaned:
    def test_only_worker_states_qualify(self) -> None:
        for state in (TaskState.SUBMITTED, TaskState.WORKING):
            assert is_orphaned(_task("t", state, 120), 60)
        for state in (
            TaskState.INPUT_REQUIRED,
            TaskState.AUTH_REQUIRED,
            TaskState.COMPLETED,
            TaskState.FAILED,
        ):
            assert not is_orphaned(_task("t", state, 120), 60)

    def test_fresh_or_disabled_is_not_orphaned(self) -> None:
        assert not is_orphaned(_task("t", TaskState.WORKING, 10), 60)
        assert not is_orphaned(_task("t", TaskState.WORKING, 10_000), None)


class TestLazyCheckOnPoll:
    async def test_tasks_get_fails_a_stale_working_task(self) -> None:
        store = InMemoryTaskStore()
        await store.save(_task("t1", TaskState.WORKING, 120))

        response = await _server(store).dispatch(_get("t1"))

        status = response["result"]["status"]
        assert status["state"] == "failed"
        assert "orphaned" in status["message"]["parts"][0]["text"]
        assert ORPHANED_METADATA_KEY in response["result"]["metadata"]
        stored = await store.get("t1")
        assert stored is not None and stored.status.state == TaskState.FAILED

    async def test_fresh_and_waiting_tasks_are_untouched(self) -> None:
        store = InMemoryTaskStore()
        await store.save(_task("fresh", TaskState.WORKING, 5))
        await store.save(_task("waiting", TaskState.INPUT_REQUIRED, 10_000))
        server = _server(store)

        fresh = await server.dispatch(_get("fresh"))
        waiting = await server.dispatch(_get("waiting"))

        assert fresh["result"]["status"]["state"] == "working"
        assert waiting["result"]["status"]["state"] == "input-required"

    async def test_disabled_timeout_keeps_legacy_behaviour(self) -> None:
        store = InMemoryTaskStore()
        await store.save(_task("t1", TaskState.WORKING, 10_000))

        response = await _server(store, timeout=None).dispatch(_get("t1"))

        assert response["result"]["status"]["state"] == "working"

    async def test_resubscribe_final_event_is_terminal(self) -> None:
        store = InMemoryTaskStore()
        await store.save(_task("t1", TaskState.WORKING, 120))
        server = _server(store)

        unary = await server.dispatch(_get("t1", "tasks/resubscribe"))
        events = [
            e async for e in server.dispatch_stream(_get("t1", "tasks/resubscribe"))
        ]

        assert unary["result"]["status"]["state"] == "failed"
        assert events[-1]["result"]["final"] is True
        assert events[-1]["result"]["status"]["state"] == "failed"

    def test_default_timeout_is_one_hour(self) -> None:
        card = AgentCard(name="e", description="e", url="http://localhost")
        assert EchoA2AServer(card).orphan_timeout_seconds == 3600.0


class TestSweep:
    async def test_in_memory_sweep_fails_only_orphans(self) -> None:
        store = InMemoryTaskStore()
        await store.save(_task("old", TaskState.WORKING, 120))
        await store.save(_task("old-submitted", TaskState.SUBMITTED, 120))
        await store.save(_task("fresh", TaskState.WORKING, 5))
        await store.save(_task("waiting", TaskState.INPUT_REQUIRED, 10_000))

        failed = await sweep_orphaned_tasks(store, max_age_seconds=60)

        assert sorted(failed) == ["old", "old-submitted"]
        fresh = await store.get("fresh")
        assert fresh is not None and fresh.status.state == TaskState.WORKING

    async def test_sweep_respects_limit(self) -> None:
        store = InMemoryTaskStore()
        for i in range(5):
            await store.save(_task(f"t{i}", TaskState.WORKING, 120))

        assert len(await sweep_orphaned_tasks(store, max_age_seconds=60, limit=2)) == 2
        assert len(await sweep_orphaned_tasks(store, max_age_seconds=60)) == 3

    async def test_store_without_capability_is_skipped(self) -> None:
        class Bare(TaskStore):
            async def get(self, task_id: str) -> Task | None:
                return None

            async def save(self, task: Task) -> None:
                return None

            async def delete(self, task_id: str) -> bool:
                return False

        assert await sweep_orphaned_tasks(Bare(), max_age_seconds=1) == []


def _cursors(*cursors: MagicMock) -> Any:
    queue = list(cursors)

    @asynccontextmanager
    async def ctx(*args: Any, **kwargs: Any) -> Any:
        yield queue.pop(0)

    return ctx


class TestPostgresSweep:
    @pytest.mark.parametrize("rowcount, expected", [(1, ["t1"]), (0, [])])
    async def test_fails_stale_rows_conditionally(
        self, rowcount: int, expected: list[str]
    ) -> None:
        now = 10_000.0
        stale = _task("t1", TaskState.WORKING, 0)
        select = MagicMock()
        select.execute = AsyncMock()
        select.fetchall = AsyncMock(
            return_value=[
                {
                    "task_id": "t1",
                    "tenant_id": "acme",
                    "status": "working",
                    "data": stale.to_dict(),
                    "updated_at": now - 7200,
                }
            ]
        )
        update = MagicMock()
        update.execute = AsyncMock()
        update.rowcount = rowcount
        with patch(
            "baselith.core.a2a.task_store_postgres.get_async_cursor",
            _cursors(select, update),
        ):
            failed = await sweep_orphaned_tasks(
                PostgresTaskStore(), max_age_seconds=3600, now=now, limit=7
            )

        assert failed == expected
        select_sql, select_params = select.execute.call_args.args
        assert "status IN ('submitted', 'working')" in select_sql
        assert select_params == (now - 3600, 7)
        update_sql, params = update.execute.call_args.args
        assert "AND status = %s AND updated_at = %s" in update_sql
        assert params[0] == "failed"
        assert '"orphaned"' in params[1]
        # The row keeps its own tenant and is matched on what was read.
        assert params[3:] == ("t1", "acme", "working", now - 7200)
