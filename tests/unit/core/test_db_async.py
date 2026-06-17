"""
Tests for sync core.db refactoring to async.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from core.db import feedback, documents, schema
from core.db import connection as db_connection


from contextlib import asynccontextmanager


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
