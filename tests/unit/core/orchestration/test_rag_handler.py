import pytest
from unittest.mock import MagicMock, AsyncMock
from core.orchestration.handlers.rag import StandardRagHandler


@pytest.fixture
def mock_deps():
    # Mock get_vectorstore_service
    # Mock get_llm_service
    # Mock get_chat_config
    pass


# We can't mock get_* easily without patching.
# Let's assume we can instantiate StandardRagHandler and then replace valid attributes.


@pytest.mark.asyncio
async def test_standard_rag_handler_flow():
    """Test standard RAG flow."""
    # We patch the class's dependency resolution or use dependency injection via config?
    # StandardRagHandler uses 'from ... import get_...' inside init or calls global Getters.
    # Best way is to patch the module 'core.orchestration.handlers.rag' imports.

    with pytest.MonkeyPatch.context() as m:
        # Mock VectorStore
        mock_vs = MagicMock()
        mock_vs.model.encode.return_value = [0.1, 0.2]
        mock_vs.search = AsyncMock(return_value=[])  # Empty first run

        # Mock LLM
        mock_llm = MagicMock()
        mock_llm.generate_response_async = AsyncMock(return_value="Answer")

        # Mock Config
        mock_config = MagicMock()
        mock_config.enable_reranking = True
        mock_config.final_top_k = 3
        mock_config.initial_search_k = 10
        mock_config.embedder_model = "test-model"

        # Mock Embedder
        mock_embedder = MagicMock()
        mock_embedder.encode = AsyncMock(return_value=[0.1, 0.2])

        m.setattr(
            "core.orchestration.handlers.rag.get_vectorstore_service", lambda: mock_vs
        )
        m.setattr("core.orchestration.handlers.rag.get_llm_service", lambda: mock_llm)
        m.setattr(
            "core.orchestration.handlers.rag.get_chat_config", lambda: mock_config
        )

        # Patching get_embedder where it's imported in the handler module or ensuring it's mockable
        # Since it is imported inside __init__, we need to patch sys.modules or the function in core.nlp

        # We need to make sure core.nlp is importable or already imported
        try:
            import core.nlp

            m.setattr(core.nlp, "get_embedder", lambda x: mock_embedder)
        except ImportError:
            # If core.nlp cannot be imported for some reason, we mock the module in sys.modules
            import sys

            mock_nlp = MagicMock()
            mock_nlp.get_embedder = lambda x: mock_embedder
            m.setattr(sys.modules, "core.nlp", mock_nlp)

        handler = StandardRagHandler()

        # Test handle
        result = await handler.handle("query", {"kb_label": "default"})

        # Should return "No info" if search returns empty
        assert "couldn't find relevant information" in result["response"].lower()

        # Test with results
        from core.models.domain import SearchResult, Document

        mock_vs.search.return_value = [
            SearchResult(document=Document(id="1", content="Info"), score=0.9)
        ]

        result = await handler.handle("query", {})
        assert result["response"] == "Answer"
        assert result["sources"] == ["1"]
        assert result["metadata"]["rerank_used"] is True
