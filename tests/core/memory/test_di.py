import pytest
from unittest.mock import MagicMock, patch
from core.memory.hierarchy import HierarchicalMemory
from core.bootstrap.lazy_init import initialize_hierarchical_memory


@pytest.mark.asyncio
async def test_initialize_hierarchical_memory():
    """Test that the hierarchical memory factory works correctly."""

    with (
        patch("core.services.llm.service.get_llm_service") as mock_get_llm,
        patch("core.nlp.models.get_embedder") as mock_get_embedder,
    ):
        # Setup mocks
        mock_llm = MagicMock()
        mock_embedder = MagicMock()

        mock_get_llm.return_value = mock_llm
        mock_get_embedder.return_value = mock_embedder

        # Call factory
        memory = await initialize_hierarchical_memory()

        # Verify
        assert isinstance(memory, HierarchicalMemory)
        assert memory._llm_service == mock_llm
        assert memory.embedder == mock_embedder
