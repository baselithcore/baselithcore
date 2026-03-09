import pytest
from unittest.mock import AsyncMock, patch
from uuid import uuid4
from datetime import datetime, timezone
from core.storage.postgres import PostgresStorage
from core.storage.models import Interaction, Feedback
from core.config import StorageConfig


@pytest.fixture
def storage_config():
    return StorageConfig(url="postgresql://user:pass@host/db")


@pytest.fixture
def storage(storage_config):
    return PostgresStorage(storage_config)


@pytest.fixture
def mock_interaction():
    return Interaction(
        id=uuid4(),
        session_id="session-1",
        input_transcription="hello",
        output_transcription="hi",
        agent_id="agent-1",
    )


class TestPostgresStorage:
    @pytest.mark.asyncio
    async def test_initialize_schema(self, storage):
        with patch("core.storage.postgres.get_async_cursor") as mock_cursor:
            mock_cur = AsyncMock()
            mock_cursor.return_value.__aenter__.return_value = mock_cur

            await storage._initialize_schema()
            assert mock_cur.execute.called

    @pytest.mark.asyncio
    async def test_health_check_success(self, storage):
        with patch("core.storage.postgres.get_async_cursor") as mock_cursor:
            mock_cur = AsyncMock()
            mock_cursor.return_value.__aenter__.return_value = mock_cur

            assert await storage.health_check() is True

    @pytest.mark.asyncio
    async def test_health_check_failure(self, storage):
        with patch(
            "core.storage.postgres.get_async_cursor", side_effect=Exception("DB Down")
        ):
            assert await storage.health_check() is False

    @pytest.mark.asyncio
    async def test_store_and_get_interaction(self, storage, mock_interaction):
        with patch("core.storage.postgres.get_async_cursor") as mock_cursor:
            mock_cur = AsyncMock()
            mock_cursor.return_value.__aenter__.return_value = mock_cur

            # Mock get_interaction to return a dict as dict_row is used
            mock_cur.fetchone.return_value = {
                "id": mock_interaction.id,
                "session_id": "session-1",
                "input_transcription": "hello",
                "output_transcription": "hi",
                "agent_id": "agent-1",
                "metadata": {},
                "timestamp": datetime.now(timezone.utc),
            }

            await storage.store_interaction(mock_interaction)
            assert mock_cur.execute.called

            result = await storage.get_interaction(mock_interaction.id)
            assert result is not None
            assert result.id == mock_interaction.id

    @pytest.mark.asyncio
    async def test_get_interactions_by_session(self, storage):
        with patch("core.storage.postgres.get_async_cursor") as mock_cursor:
            mock_cur = AsyncMock()
            mock_cursor.return_value.__aenter__.return_value = mock_cur

            mock_cur.fetchall.return_value = [
                {
                    "id": uuid4(),
                    "session_id": "s1",
                    "input_transcription": "i1",
                    "output_transcription": "o1",
                    "agent_id": "a1",
                    "metadata": {},
                    "timestamp": datetime.now(timezone.utc),
                },
                {
                    "id": uuid4(),
                    "session_id": "s1",
                    "input_transcription": "i2",
                    "output_transcription": "o2",
                    "agent_id": "a1",
                    "metadata": {},
                    "timestamp": datetime.now(timezone.utc),
                },
            ]

            results = await storage.get_interactions_by_session("s1")
            assert len(results) == 2

    @pytest.mark.asyncio
    async def test_store_and_get_feedback(self, storage):
        with patch("core.storage.postgres.get_async_cursor") as mock_cursor:
            mock_cur = AsyncMock()
            mock_cursor.return_value.__aenter__.return_value = mock_cur

            interaction_id = uuid4()
            feedback = Feedback(interaction_id=interaction_id, score=5, comment="Great")

            await storage.store_feedback(feedback)
            assert mock_cur.execute.called

            # Mock get_feedback_for_interaction
            mock_cur.fetchall.return_value = [
                {
                    "id": uuid4(),
                    "interaction_id": interaction_id,
                    "score": 5,
                    "label": "pos",
                    "comment": "Great",
                    "metadata": {},
                    "timestamp": datetime.now(timezone.utc),
                }
            ]

            results = await storage.get_feedback_for_interaction(interaction_id)
            assert len(results) == 1
            assert results[0].score == 5

    @pytest.mark.asyncio
    async def test_get_feedback_summary(self, storage):
        with patch("core.storage.postgres.get_async_cursor") as mock_cursor:
            mock_cur = AsyncMock()
            mock_cursor.return_value.__aenter__.return_value = mock_cur

            # Select query for average and count
            mock_cur.fetchone.return_value = {
                "average_score": 4.5,
                "total_feedback": 10,
                "positive_count": 8,
                "negative_count": 2,
            }

            summary = await storage.get_feedback_summary("agent-1")
            assert summary["average_score"] == 4.5
            assert summary["total_feedback"] == 10
