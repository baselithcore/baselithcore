"""
Tests for sync core.db refactoring to async.
"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.db import connection as db_connection
from core.db import documents, feedback, schema


@pytest.mark.asyncio
async def test_insert_feedback_async():
    """Test insert_feedback is async and calls db correctly."""
    mock_conn = AsyncMock()
    mock_cursor = AsyncMock()
    mock_cursor.execute = AsyncMock()

    @asynccontextmanager
    async def cursor_gen(*args, **kwargs):
        yield mock_cursor

    # Force cursor() to be synchronous method returning the CM
    mock_conn.cursor = MagicMock(side_effect=cursor_gen)

    @asynccontextmanager
    async def get_conn_gen():
        yield mock_conn

    with patch("core.db.feedback.get_async_connection", side_effect=get_conn_gen):
        await feedback.insert_feedback(
            query="test query",
            answer="test answer",
            feedback="positive",
            conversation_id="conv-123",
        )

        mock_cursor.execute.assert_awaited()
        call_args = mock_cursor.execute.call_args
        assert "INSERT INTO chat_feedback" in call_args[0][0]


@pytest.mark.asyncio
async def test_get_feedbacks_async():
    """Test get_feedbacks is async."""
    mock_conn = AsyncMock()
    mock_cursor = AsyncMock()
    mock_cursor.execute = AsyncMock()
    mock_cursor.fetchall = AsyncMock()

    @asynccontextmanager
    async def cursor_gen(*args, **kwargs):
        yield mock_cursor

    mock_conn.cursor = MagicMock(side_effect=cursor_gen)

    mock_cursor.fetchall.return_value = [
        {
            "id": 1,
            "query": "q",
            "answer": "a",
            "feedback": "positive",
            "conversation_id": "c1",
            "sources": None,
            "comment": None,
            "timestamp": None,
        }
    ]

    @asynccontextmanager
    async def get_conn_gen():
        yield mock_conn

    with patch("core.db.feedback.get_async_connection", side_effect=get_conn_gen):
        results = await feedback.get_feedbacks(limit=10)

        mock_cursor.execute.assert_awaited()
        assert len(results) == 1
        assert results[0]["query"] == "q"


@pytest.mark.asyncio
async def test_get_feedback_analytics_always_applies_time_bound():
    """Analytics must apply a time window even when ``days`` is None."""
    import datetime

    captured_params: list = []

    @asynccontextmanager
    async def get_conn_gen():
        mock_conn = AsyncMock()
        mock_cursor = AsyncMock()
        mock_cursor.execute = AsyncMock()
        mock_cursor.fetchall = AsyncMock(return_value=[])

        async def _execute(query, params=None):
            # Skip the "SET statement_timeout" calls (params is None there).
            if params is not None:
                captured_params.append(params)

        mock_cursor.execute.side_effect = _execute

        @asynccontextmanager
        async def cursor_gen(*args, **kwargs):
            yield mock_cursor

        mock_conn.cursor = MagicMock(side_effect=cursor_gen)
        yield mock_conn

    with patch("core.db.feedback.get_async_connection", side_effect=get_conn_gen):
        result = await feedback.get_feedback_analytics(days=None)

    # Every analytics query must carry a datetime lower bound (the window).
    assert captured_params, "expected analytics queries to run"
    for params in captured_params:
        assert any(isinstance(p, datetime.datetime) for p in params), params

    # The reported window keeps days=None but exposes the effective 'since'.
    assert result["window"]["days"] is None
    assert result["window"]["since"] is not None


@pytest.mark.asyncio
async def test_get_document_feedback_summary_async():
    """Test get_document_feedback_summary is async."""
    mock_conn = AsyncMock()
    mock_cursor = AsyncMock()
    mock_cursor.execute = AsyncMock()
    mock_cursor.fetchall = AsyncMock(return_value=[])

    @asynccontextmanager
    async def cursor_gen(*args, **kwargs):
        yield mock_cursor

    mock_conn.cursor = MagicMock(side_effect=cursor_gen)

    @asynccontextmanager
    async def get_conn_gen():
        yield mock_conn

    with patch("core.db.documents.POSTGRES_ENABLED", True):
        with patch("core.db.documents.get_async_connection", side_effect=get_conn_gen):
            summary = await documents.get_document_feedback_summary()

            mock_cursor.execute.assert_awaited()
            assert summary == {}


@pytest.mark.asyncio
async def test_ensure_schema_async():
    """Test ensure_schema is async."""
    # Mock alembic module to avoid ModuleNotFoundError
    mock_alembic_config = MagicMock()
    mock_alembic_command = MagicMock()
    mock_alembic = MagicMock()
    mock_alembic.config = mock_alembic_config
    mock_alembic.command = mock_alembic_command

    import sys

    sys.modules["alembic"] = mock_alembic
    sys.modules["alembic.config"] = mock_alembic_config
    sys.modules["alembic.command"] = mock_alembic_command

    try:
        with patch("asyncio.get_running_loop") as mock_loop:
            # Make run_in_executor return a completed future
            from asyncio import Future

            future = Future()
            future.set_result(None)
            mock_loop.return_value.run_in_executor = MagicMock(return_value=future)

            await schema.ensure_schema()
            mock_loop.return_value.run_in_executor.assert_called_once()
    finally:
        # Clean up mocked modules
        sys.modules.pop("alembic", None)
        sys.modules.pop("alembic.config", None)
        sys.modules.pop("alembic.command", None)


@pytest.mark.asyncio
async def test_init_db_async():
    """Test init_db calls ensure_schema."""
    with patch("core.db.schema.ensure_schema", new_callable=AsyncMock) as mock_ensure:
        with patch("core.db.schema.POSTGRES_ENABLED", True):
            await schema.init_db()
            mock_ensure.assert_called_once()


def test_sync_pool_open_failure_does_not_mark_pool_opened():
    """A failed pool.open() must remain retryable."""
    mock_pool = MagicMock()
    mock_pool.open.side_effect = RuntimeError("db unavailable")
    mock_pool.closed = True

    with patch("core.db.connection._get_pool", return_value=mock_pool):
        original = db_connection._POOL_OPENED
        db_connection._POOL_OPENED = False
        try:
            with pytest.raises(RuntimeError, match="db unavailable"):
                with db_connection.get_connection():
                    pass
            assert db_connection._POOL_OPENED is False
        finally:
            db_connection._POOL_OPENED = original


@pytest.mark.asyncio
async def test_async_pool_open_failure_does_not_mark_pool_opened():
    """A failed async pool.open() must remain retryable."""
    mock_pool = MagicMock()
    mock_pool.open = AsyncMock(side_effect=RuntimeError("db unavailable"))
    mock_pool.closed = True

    with patch("core.db.connection._get_async_pool", return_value=mock_pool):
        original = db_connection._ASYNC_POOL_OPENED
        db_connection._ASYNC_POOL_OPENED = False
        try:
            with pytest.raises(RuntimeError, match="db unavailable"):
                async with db_connection.get_async_connection():
                    pass
            assert db_connection._ASYNC_POOL_OPENED is False
        finally:
            db_connection._ASYNC_POOL_OPENED = original


class TestWarmAsyncPool:
    """Startup warmup: the first request must not pay TCP+TLS+auth for
    min_size connections inline — the pool opens during lifespan instead."""

    async def test_opens_pool_waiting_for_min_size(self, monkeypatch):
        from unittest.mock import AsyncMock

        from core.db import connection as conn_mod

        pool = AsyncMock()
        monkeypatch.setattr(conn_mod, "POSTGRES_ENABLED", True)
        monkeypatch.setattr(conn_mod, "_ASYNC_POOL_OPENED", False)
        monkeypatch.setattr(conn_mod, "_get_async_pool", lambda: pool)

        assert await conn_mod.warm_async_pool() is True
        pool.open.assert_awaited_once()
        assert pool.open.await_args.kwargs.get("wait") is True
        assert conn_mod._ASYNC_POOL_OPENED is True

        # Second call: already warm, no second open.
        assert await conn_mod.warm_async_pool() is True
        assert pool.open.await_count == 1

    async def test_failure_is_soft(self, monkeypatch):
        from unittest.mock import AsyncMock

        from core.db import connection as conn_mod

        pool = AsyncMock()
        pool.open = AsyncMock(side_effect=OSError("db down"))
        monkeypatch.setattr(conn_mod, "POSTGRES_ENABLED", True)
        monkeypatch.setattr(conn_mod, "_ASYNC_POOL_OPENED", False)
        monkeypatch.setattr(conn_mod, "_get_async_pool", lambda: pool)

        # Warmup must never take startup down: lazy open still covers requests.
        assert await conn_mod.warm_async_pool() is False
        assert conn_mod._ASYNC_POOL_OPENED is False

    async def test_disabled_postgres_is_a_noop(self, monkeypatch):
        from core.db import connection as conn_mod

        monkeypatch.setattr(conn_mod, "POSTGRES_ENABLED", False)
        assert await conn_mod.warm_async_pool() is False


class TestTenantBindingMemo:
    """With DB_RLS_ENABLED the tenant GUC used to be set on EVERY checkout —
    one extra full round-trip per query even when the same tenant reuses the
    same pooled connection. The binding is memoized per connection and only
    re-applied when the tenant actually changes (pooled connections serve
    different tenants across requests, so the change path must stay exact)."""

    def _connection(self, executed: list):
        from contextlib import asynccontextmanager
        from unittest.mock import AsyncMock, MagicMock

        conn = MagicMock()
        cursor = MagicMock()
        cursor.execute = AsyncMock(side_effect=lambda *a: executed.append(a))

        @asynccontextmanager
        async def _cursor():
            yield cursor

        conn.cursor = _cursor
        # MagicMock would fabricate the memo attribute on first getattr.
        del conn._app_tenant_id
        return conn

    async def test_same_tenant_reapply_is_skipped(self, monkeypatch):
        from core.db import connection as conn_mod

        executed: list = []
        conn = self._connection(executed)
        monkeypatch.setattr(conn_mod, "_current_tenant_for_session", lambda: "tenant-a")
        await conn_mod._async_apply_tenant(conn)
        await conn_mod._async_apply_tenant(conn)
        assert len(executed) == 1  # second checkout: no extra round-trip

    async def test_tenant_change_reapplies(self, monkeypatch):
        from core.db import connection as conn_mod

        executed: list = []
        conn = self._connection(executed)
        tenants = iter(["tenant-a", "tenant-b"])
        monkeypatch.setattr(
            conn_mod, "_current_tenant_for_session", lambda: next(tenants)
        )
        await conn_mod._async_apply_tenant(conn)
        await conn_mod._async_apply_tenant(conn)
        assert len(executed) == 2
        assert executed[1][1] == ("tenant-b",)
