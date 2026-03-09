import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from core.chat.workflow_retrieval import RetrievalPipeline
from core.chat.agent_state import AgentState
from core.models.chat import ChatRequest
from core.chat.service import ChatService
from qdrant_client.models import PointStruct


@pytest.fixture
def mock_chat_service():
    service = MagicMock(spec=ChatService)
    service.INITIAL_SEARCH_K = 3
    service.FINAL_TOP_K = 2
    service.newline = "\n"
    service.double_newline = "\n\n"
    service.section_separator = "---"
    service.reranker = MagicMock()
    service.rerank_cache = MagicMock()
    service.response_cache = AsyncMock()
    service.history_manager = AsyncMock()
    service.embedder = AsyncMock()
    service.embedder.encode.return_value = [[0.1, 0.2]]
    return service


@pytest.fixture
def mock_vectorstore_service():
    with (
        patch("core.chat.mixins.retrieval_search.get_vectorstore_service") as mock1,
        patch("core.chat.workflow_retrieval.get_vectorstore_service") as mock2,
    ):
        service = AsyncMock()
        mock1.return_value = service
        mock2.return_value = service
        yield service


@pytest.fixture
def mock_indexing_service():
    with patch("core.chat.mixins.retrieval_search.get_indexing_service") as mock:
        service = MagicMock()
        service.indexed_documents = {
            "doc1": {
                "metadata": {
                    "title": "Doc 1",
                    "filename": "doc1.txt",
                    "relative_path": "path/to/doc1.txt",
                }
            },
            "doc2": {
                "metadata": {
                    "title": "Doc 2",
                    "filename": "doc2.txt",
                    "relative_path": "path/to/doc2.txt",
                }
            },
        }
        mock.return_value = service
        yield service


@pytest.mark.asyncio
async def test_retrieve_documents_basic_search(
    mock_chat_service, mock_vectorstore_service, mock_indexing_service
):
    """Test basic retrieval flow using vectorstore search."""
    pipeline = RetrievalPipeline(mock_chat_service)
    state = AgentState(request=ChatRequest(query="test query"))
    state.query_vector = [0.1, 0.2]

    # Mock search results
    hit1 = MagicMock()
    hit1.payload = {"document_id": "doc1", "text": "content 1"}
    hit1.score = 0.9

    # Use side_effect to return list for search_fn call
    mock_vectorstore_service.search.return_value = [hit1]

    # Run retrieval
    await pipeline.retrieve_documents(state)

    # Verify search called
    mock_vectorstore_service.search.assert_called_once()
    assert len(state.hits) >= 1
    assert state.next_action == "score_documents"


@pytest.mark.asyncio
async def test_retrieve_documents_fallback_explicit_match(
    mock_chat_service, mock_vectorstore_service, mock_indexing_service
):
    """Test explicit document matching using scroll."""
    pipeline = RetrievalPipeline(mock_chat_service)
    state = AgentState(request=ChatRequest(query="read doc2.txt"))
    state.user_query = "read doc2.txt"
    state.query_vector = [0.1, 0.2]

    # Mock search returning nothing initially
    mock_vectorstore_service.search.return_value = []

    # Mock scroll for explicit match
    point = PointStruct(
        id=1, vector=[0.1], payload={"document_id": "doc2", "text": "explicit content"}
    )
    mock_vectorstore_service.scroll.return_value = ([point], None)

    await pipeline.retrieve_documents(state)

    # Verify scroll was called to look up doc2
    mock_vectorstore_service.scroll.assert_called()
    assert any(h.payload["document_id"] == "doc2" for h in state.hits)


@pytest.mark.asyncio
async def test_retrieve_documents_fallback_recall(
    mock_chat_service, mock_vectorstore_service, mock_indexing_service
):
    """Test fallback recall using query_points."""
    pipeline = RetrievalPipeline(mock_chat_service)
    state = AgentState(request=ChatRequest(query="test"))
    state.query_vector = [0.1, 0.2]

    # Search returns 1 hit, but we want FINAL_TOP_K=2
    hit1 = MagicMock()
    hit1.payload = {"document_id": "doc1"}
    mock_vectorstore_service.search.return_value = [hit1]

    # Mock query_points for fallback
    fallback_point = MagicMock()
    fallback_point.payload = {"document_id": "doc2"}
    fallback_point.score = 0.8
    response = MagicMock()
    response.points = [fallback_point]
    mock_vectorstore_service.query_points.return_value = response

    await pipeline.retrieve_documents(state)

    # Verify query_points called to fill gaps
    mock_vectorstore_service.query_points.assert_called()
    assert len(state.hits) >= 2  # Original + Fallback
