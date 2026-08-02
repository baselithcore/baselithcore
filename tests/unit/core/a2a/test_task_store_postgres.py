"""Unit tests for the durable Postgres A2A task store."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

from core.a2a.task_store_postgres import PostgresTaskStore
from core.a2a.types import Task, TaskState, TaskStatus


def _task(task_id: str = "t1") -> Task:
    return Task(id=task_id, status=TaskStatus(state=TaskState.SUBMITTED))


def _cursor_ctx(cursor):
    @asynccontextmanager
    async def ctx(*args, **kwargs):
        yield cursor

    return ctx


class TestPostgresTaskStore:
    async def test_initialize_creates_schema(self):
        cursor = MagicMock()
        cursor.execute = AsyncMock()
        with patch(
            "core.a2a.task_store_postgres.get_async_cursor", _cursor_ctx(cursor)
        ):
            await PostgresTaskStore().initialize()
        sql = cursor.execute.call_args.args[0]
        assert "CREATE TABLE IF NOT EXISTS a2a_tasks" in sql

    async def test_save_upserts_by_task_id(self):
        cursor = MagicMock()
        cursor.execute = AsyncMock()
        with patch(
            "core.a2a.task_store_postgres.get_async_cursor", _cursor_ctx(cursor)
        ):
            await PostgresTaskStore().save(_task("t1"))
        sql = cursor.execute.call_args.args[0]
        assert "INSERT INTO a2a_tasks" in sql
        assert "ON CONFLICT (task_id) DO UPDATE" in sql
        params = cursor.execute.call_args.args[1]
        assert params[0] == "t1"

    async def test_get_roundtrips_task(self):
        cursor = MagicMock()
        cursor.execute = AsyncMock()
        cursor.fetchone = AsyncMock(return_value={"data": _task("t1").to_dict()})
        with patch(
            "core.a2a.task_store_postgres.get_async_cursor", _cursor_ctx(cursor)
        ):
            task = await PostgresTaskStore().get("t1")
        assert task is not None
        assert task.id == "t1"
        assert task.status.state == TaskState.SUBMITTED

    async def test_get_missing_returns_none(self):
        cursor = MagicMock()
        cursor.execute = AsyncMock()
        cursor.fetchone = AsyncMock(return_value=None)
        with patch(
            "core.a2a.task_store_postgres.get_async_cursor", _cursor_ctx(cursor)
        ):
            assert await PostgresTaskStore().get("missing") is None

    async def test_delete_reports_rowcount(self):
        cursor = MagicMock()
        cursor.execute = AsyncMock()
        cursor.rowcount = 1
        with patch(
            "core.a2a.task_store_postgres.get_async_cursor", _cursor_ctx(cursor)
        ):
            assert await PostgresTaskStore().delete("t1") is True
        cursor.rowcount = 0
        with patch(
            "core.a2a.task_store_postgres.get_async_cursor", _cursor_ctx(cursor)
        ):
            assert await PostgresTaskStore().delete("t1") is False

    async def test_tenant_scoping_in_where_clause(self):
        cursor = MagicMock()
        cursor.execute = AsyncMock()
        cursor.fetchone = AsyncMock(return_value=None)
        with (
            patch("core.a2a.task_store_postgres.get_async_cursor", _cursor_ctx(cursor)),
            patch(
                "core.a2a.task_store_postgres.get_current_tenant_id",
                return_value="acme",
            ),
        ):
            await PostgresTaskStore().get("t1")
        sql = cursor.execute.call_args.args[0]
        params = cursor.execute.call_args.args[1]
        assert "tenant_id" in sql
        assert "acme" in params

    async def test_is_a_task_store(self):
        from core.a2a.server import TaskStore

        assert issubclass(PostgresTaskStore, TaskStore)
