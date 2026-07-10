from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from qdrant_client.models import PointStruct

from core.chat.agent_state import AgentState
from core.chat.service import ChatService
from core.chat.workflow_retrieval import RetrievalPipeline
from core.models.chat import ChatRequest


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
    """Test fallback recall using one grouped query (best chunk per doc)."""
    pipeline = RetrievalPipeline(mock_chat_service)
    state = AgentState(request=ChatRequest(query="test"))
    state.query_vector = [0.1, 0.2]

    # Search returns 1 hit, but we want FINAL_TOP_K=2
    hit1 = MagicMock()
    hit1.payload = {"document_id": "doc1"}
    mock_vectorstore_service.search.return_value = [hit1]

    # Mock the grouped fallback query: one group per unseen document
    fallback_point = MagicMock()
    fallback_point.payload = {"document_id": "doc2"}
    fallback_point.score = 0.8
    group = MagicMock()
    group.hits = [fallback_point]
    response = MagicMock()
    response.groups = [group]
    mock_vectorstore_service.query_points_groups.return_value = response

    await pipeline.retrieve_documents(state)

    # ONE grouped call fills the gap (previously one query per unseen doc)
    mock_vectorstore_service.query_points_groups.assert_called_once()
    call_kwargs = mock_vectorstore_service.query_points_groups.call_args.kwargs
    assert call_kwargs["group_by"] == "document_id"
    assert len(state.hits) >= 2  # Original + Fallback


@pytest.fixture
def mock_scoring_services():
    """Patch the SCORING mixin's service accessors (module-local imports)."""
    with (
        patch("core.chat.mixins.retrieval_scoring.get_vectorstore_service") as vs,
        patch("core.chat.mixins.retrieval_scoring.get_indexing_service") as idx,
    ):
        vector_service = AsyncMock()
        vs.return_value = vector_service
        indexing = MagicMock()
        indexing.indexed_documents = {"doc1": {}, "doc2": {}, "doc3": {}}
        idx.return_value = indexing
        yield vector_service


@pytest.mark.asyncio
async def test_score_documents_fallback_uses_one_grouped_query(
    mock_chat_service, mock_scoring_services
):
    """The rerank fallback fills missing docs with ONE grouped query — the
    per-document fan-out scaled O(corpus size) per chat request."""

    async def fake_rerank(query, normalized, candidates, **kwargs):
        hit = MagicMock()
        hit.payload = {"document_id": "doc1"}
        return [(hit, 0.9)]

    pipeline = RetrievalPipeline(mock_chat_service, rerank_fn=fake_rerank)
    state = AgentState(request=ChatRequest(query="test"))
    state.user_query = "test"
    state.query_vector = [0.1, 0.2]
    state.hits = [MagicMock()]

    fallback_point = MagicMock()
    fallback_point.payload = {"document_id": "doc2"}
    fallback_point.score = 0.7
    group = MagicMock()
    group.hits = [fallback_point]
    response = MagicMock()
    response.groups = [group]
    mock_scoring_services.query_points_groups.return_value = response

    await pipeline.score_documents(state)

    mock_scoring_services.query_points_groups.assert_called_once()
    kwargs = mock_scoring_services.query_points_groups.call_args.kwargs
    assert kwargs["group_by"] == "document_id"
    assert kwargs["group_size"] == 1
    assert kwargs["limit"] == 1  # FINAL_TOP_K(2) - already ranked(1)
    # No per-document query fan-out.
    mock_scoring_services.query_points.assert_not_called()
    # The fallback hit landed with its score.
    assert any(
        getattr(h, "payload", {}) == {"document_id": "doc2"} and s == 0.7
        for h, s in state.ranked_hits
    )


@pytest.mark.asyncio
async def test_score_documents_fallback_excludes_seen_documents(
    mock_chat_service, mock_scoring_services
):
    """Documents already ranked must be excluded via a must_not filter."""

    async def fake_rerank(query, normalized, candidates, **kwargs):
        hit = MagicMock()
        hit.payload = {"document_id": "doc1"}
        return [(hit, 0.9)]

    pipeline = RetrievalPipeline(mock_chat_service, rerank_fn=fake_rerank)
    state = AgentState(request=ChatRequest(query="test"))
    state.user_query = "test"
    state.query_vector = [0.1, 0.2]
    state.hits = [MagicMock()]

    response = MagicMock()
    response.groups = []
    mock_scoring_services.query_points_groups.return_value = response

    await pipeline.score_documents(state)

    group_filter = mock_scoring_services.query_points_groups.call_args.kwargs[
        "query_filter"
    ]
    assert group_filter is not None
    assert group_filter.must_not[0].match.any == ["doc1"]


def test_doc_match_fields_memoized_and_invalidated():
    """Derived match fields are computed once per doc and recomputed only
    when the raw metadata triple changes."""
    from core.chat.mixins import retrieval_search as rs

    rs._doc_match_cache.clear()
    md = {"title": "My Doc", "filename": "My_Doc.PDF", "relative_path": "a/My_Doc.pdf"}

    first = rs._doc_match_fields("d1", md)
    assert first == ("my doc", "my_doc.pdf", "a/my_doc.pdf", first[3])
    assert "my_doc" in first[3]

    # Same raw metadata: served from the memo (identical object).
    again = rs._doc_match_fields("d1", dict(md))
    assert again == first

    # Changed metadata invalidates just this entry.
    changed = rs._doc_match_fields("d1", {**md, "title": "Renamed"})
    assert changed[0] == "renamed"
    rs._doc_match_cache.clear()
