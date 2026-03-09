"""
Tests for core.storage modules.
"""

from uuid import UUID


class TestStorageModels:
    """Tests for storage models."""

    def test_interaction_model_import(self):
        """Interaction model can be imported."""
        from core.storage.models import Interaction

        assert Interaction is not None

    def test_feedback_model_import(self):
        """Feedback model can be imported."""
        from core.storage.models import Feedback

        assert Feedback is not None

    def test_interaction_creation(self):
        """Interaction can be created."""
        from core.storage.models import Interaction

        interaction = Interaction(
            session_id="session-123",
            input_transcription="test query",
            output_transcription="test response",
        )
        assert isinstance(interaction.id, UUID)
        assert interaction.session_id == "session-123"

    def test_feedback_creation(self):
        """Feedback can be created."""
        from core.storage.models import Feedback
        from uuid import uuid4

        feedback = Feedback(interaction_id=uuid4(), score=0.8, label="positive")
        assert feedback.score == 0.8


class TestPostgresStorage:
    """Tests for PostgresStorage."""

    def test_postgres_storage_import(self):
        """PostgresStorage can be imported."""
        from core.storage.postgres import PostgresStorage

        assert PostgresStorage is not None


class TestStorageInit:
    """Tests for storage module init."""

    def test_get_storage_import(self):
        """get_storage can be imported."""
        from core.storage import get_storage

        assert callable(get_storage)
