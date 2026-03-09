"""
Tests for sync core.db refactoring to async.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from core.db import feedback, documents, schema


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
    mock_cursor = AsyncMock()
    mock_cursor.execute = AsyncMock()

    await schema.ensure_schema(mock_cursor)

    assert mock_cursor.execute.call_count >= 5


@pytest.mark.asyncio
async def test_init_db_async():
    """Test init_db calls ensure_schema."""
    mock_conn = AsyncMock()
    mock_cursor = AsyncMock()
    mock_cursor.execute = AsyncMock()

    @asynccontextmanager
    async def cursor_gen(*args, **kwargs):
        yield mock_cursor

    mock_conn.cursor = MagicMock(side_effect=cursor_gen)

    @asynccontextmanager
    async def get_conn_gen():
        yield mock_conn

    with patch("core.db.schema.POSTGRES_ENABLED", True):
        with patch("core.db.schema.get_async_connection", side_effect=get_conn_gen):
            await schema.init_db()

            mock_cursor.execute.assert_awaited()
