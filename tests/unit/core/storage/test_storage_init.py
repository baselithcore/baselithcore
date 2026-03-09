"""
Tests for core.storage package.
"""

import pytest
from unittest.mock import patch, Mock, AsyncMock

from core.storage.interfaces import InteractionRepository, FeedbackRepository
from core.storage.models import Interaction, Feedback


class TestStorageInterfaces:
    """Tests for storage interface classes."""

    def test_interaction_repository_is_abstract(self):
        """Test InteractionRepository is an abstract class."""
        import inspect

        assert inspect.isabstract(InteractionRepository)

    def test_feedback_repository_is_abstract(self):
        """Test FeedbackRepository is an abstract class."""
        import inspect

        assert inspect.isabstract(FeedbackRepository)


class TestInteractionModel:
    """Tests for Interaction model."""

    def test_interaction_model_exists(self):
        """Test Interaction model can be imported."""
        assert Interaction is not None


class TestFeedbackModel:
    """Tests for Feedback model."""

    def test_feedback_model_exists(self):
        """Test Feedback model can be imported."""
        assert Feedback is not None


class TestGetStorage:
    """Tests for get_storage function."""

    @pytest.mark.asyncio
    @patch("core.storage.get_storage_config")
    @patch("core.storage.PostgresStorage")
    async def test_get_storage_creates_instance(self, mock_postgres, mock_config):
        """Test get_storage creates PostgresStorage instance."""
        mock_config.return_value = Mock(database_url="postgresql://test")
        mock_instance = Mock()
        mock_instance.initialize = AsyncMock()
        mock_postgres.return_value = mock_instance

        # Reset the singleton
        import core.storage

        core.storage._storage_instance = None

        from core.storage import get_storage

        await get_storage()

        mock_postgres.assert_called_once()
        mock_instance.initialize.assert_awaited_once()
