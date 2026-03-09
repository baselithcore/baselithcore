"""
Unit tests for PostgresStorage.
"""

import os
import json
import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from uuid import uuid4
from datetime import datetime
from contextlib import contextmanager, asynccontextmanager

from core.storage.postgres import PostgresStorage
from core.storage.models import Interaction, Feedback
from core.config import StorageConfig


@pytest.fixture
def config():
    cfg = StorageConfig(
        database_url="postgresql://user:pass@localhost:5432/db",
        db_pool_min_size=1,
        db_pool_max_size=5,
    )
    cfg.postgres_enabled = True
    return cfg


@pytest.fixture
def mock_async_cursor():
    """Mock for async cursor context manager."""
    cursor = AsyncMock()
    cursor.execute = AsyncMock()
    cursor.fetchone = AsyncMock(return_value=None)
    cursor.fetchall = AsyncMock(return_value=[])

    @asynccontextmanager
    async def AsyncCM(*args, **kwargs):
        yield cursor

    return cursor, AsyncCM


@pytest.fixture
def mock_sync_cursor():
    """Mock for sync cursor context manager."""
    cursor = MagicMock()

    @contextmanager
    def SyncCM():
        yield cursor

    return cursor, SyncCM()


@pytest.fixture
def storage(config):
    return PostgresStorage(config)


@pytest.mark.asyncio
async def test_initialize_schema(storage, mock_async_cursor):
    """Test schema initialization."""
    cursor, cm = mock_async_cursor

    with patch.dict(os.environ, {}, clear=True):
        with patch("core.storage.postgres.get_async_cursor", side_effect=cm):
            await storage.initialize()

            cursor.execute.assert_awaited()
            call_args = cursor.execute.call_args[0][0]
            assert "CREATE TABLE IF NOT EXISTS interactions" in call_args
            assert "CREATE TABLE IF NOT EXISTS feedback" in call_args


@pytest.mark.asyncio
async def test_initialize_disabled(config):
    """Test initialization when disabled."""
    config.postgres_enabled = False
    storage = PostgresStorage(config)

    with patch.dict(os.environ, {}, clear=True):
        with patch("core.storage.postgres.get_async_cursor") as mock_get_async_cursor:
            await storage.initialize()
            mock_get_async_cursor.assert_not_called()


@pytest.mark.asyncio
async def test_health_check(storage, mock_async_cursor):
    """Test health check."""
    cursor, cm = mock_async_cursor

    with patch.dict(os.environ, {}, clear=True):
        with patch("core.storage.postgres.get_async_cursor", side_effect=cm):
            result = await storage.health_check()
            assert result is True
            cursor.execute.assert_awaited()
            assert cursor.execute.call_args[0][0] == "SELECT 1"


@pytest.mark.asyncio
async def test_store_interaction(storage, mock_async_cursor):
    """Test storing interaction."""
    cursor, cm = mock_async_cursor

    interaction = Interaction(
        session_id="sess-1",
        input_transcription="hello",
        output_transcription="hi",
        metadata={"foo": "bar"},
    )

    with patch("core.storage.postgres.get_async_cursor", side_effect=cm):
        await storage.store_interaction(interaction)

        cursor.execute.assert_awaited()
        sql, params = cursor.execute.call_args[0]

        assert "INSERT INTO interactions" in sql
        assert str(interaction.id) == str(params[0])
        assert interaction.session_id == params[1]
        assert json.loads(params[6]) == {"foo": "bar"}


@pytest.mark.asyncio
async def test_get_interaction(storage, mock_async_cursor):
    """Test getting interaction."""
    cursor, cm = mock_async_cursor

    inter_id = uuid4()
    mock_data = {
        "id": inter_id,
        "session_id": "sess-1",
        "input_transcription": "hello",
        "output_transcription": "hi",
        "metadata": {},
        "timestamp": datetime.now(),
    }
    cursor.fetchone.return_value = mock_data

    with patch("core.storage.postgres.get_async_cursor", side_effect=cm):
        result = await storage.get_interaction(inter_id)

        assert result is not None
        assert result.id == inter_id
        assert result.session_id == "sess-1"


@pytest.mark.asyncio
async def test_store_feedback(storage, mock_async_cursor):
    """Test storing feedback."""
    cursor, cm = mock_async_cursor

    feedback = Feedback(interaction_id=uuid4(), score=0.9, label="positive")

    with patch("core.storage.postgres.get_async_cursor", side_effect=cm):
        await storage.store_feedback(feedback)

        cursor.execute.assert_awaited()
        sql, params = cursor.execute.call_args[0]

        assert "INSERT INTO feedback" in sql
        assert feedback.score == params[2]
        assert feedback.label == params[3]
