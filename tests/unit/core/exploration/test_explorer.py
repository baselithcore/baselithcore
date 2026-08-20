from unittest.mock import AsyncMock, Mock, PropertyMock, patch

import pytest

from core.exploration.explorer import (
    ExplorationResult,
    KnowledgeSource,
    ProactiveExplorer,
)


@pytest.fixture
def mock_llm_service():
    """Mock LLM service."""
    service = Mock()
    service.generate_response = AsyncMock(return_value="query1\nquery2\nquery3")
    return service


@pytest.fixture
def mock_knowledge_source():
    """Mock knowledge source."""
    source = Mock(spec=KnowledgeSource)
    source.search = AsyncMock(
        return_value=[
            {"content": "result1", "source": "source1"},
            {"content": "result2", "source": "source2"},
        ]
    )
    source.get_related = AsyncMock(return_value=["related1", "related2"])
    return source


@pytest.mark.asyncio
async def test_explorer_initialization(mock_llm_service, mock_knowledge_source):
    """Test explorer initialization."""
    explorer = ProactiveExplorer(
        sources=[mock_knowledge_source], llm_service=mock_llm_service
    )
    assert explorer.sources == [mock_knowledge_source]
    assert explorer.llm_service == mock_llm_service


@pytest.mark.asyncio
async def test_explore_basic(mock_llm_service, mock_knowledge_source):
    """Test data exploration flow."""
    explorer = ProactiveExplorer(
        sources=[mock_knowledge_source], llm_service=mock_llm_service
    )

    result = await explorer.explore("test_topic", depth=1, max_results=5)

    assert isinstance(result, ExplorationResult)
    assert result.query == "test_topic"
    assert len(result.findings) > 0, "Should have found some results"
    assert len(result.sources) > 0

    # Verify method calls
    assert mock_llm_service.generate_response.called
    assert mock_knowledge_source.search.called
    assert mock_knowledge_source.get_related.called


@pytest.mark.asyncio
async def test_explore_no_llm(mock_knowledge_source):
    """Test exploration without LLM (should fallback to simple query expansion)."""
    # We mock the property to return None, simulating that LLM service is unavailable
    with patch(
        "core.exploration.explorer.ProactiveExplorer.llm_service",
        new_callable=PropertyMock,
    ) as mock_service_prop:
        mock_service_prop.return_value = None

        explorer = ProactiveExplorer(sources=[mock_knowledge_source])
        result = await explorer.explore("test_topic")

    assert isinstance(result, ExplorationResult)
    assert len(result.findings) > 0
    # Search should still happen with simple expanded queries
    assert mock_knowledge_source.search.called


@pytest.mark.asyncio
async def test_explore_source_failure(mock_llm_service):
    """Test resilience against source failures."""
    failing_source = Mock(spec=KnowledgeSource)
    failing_source.search = AsyncMock(side_effect=Exception("Search failed"))
    failing_source.get_related = AsyncMock(side_effect=Exception("Related failed"))

    explorer = ProactiveExplorer(sources=[failing_source], llm_service=mock_llm_service)

    # Should not raise exception
    result = await explorer.explore("test_topic")

    assert isinstance(result, ExplorationResult)
    assert len(result.findings) == 0
    assert "No information found" in str(result.gaps_identified)


@pytest.mark.asyncio
async def test_expand_query(mock_llm_service):
    """Test query expansion logic."""
    explorer = ProactiveExplorer(llm_service=mock_llm_service)

    queries = await explorer._expand_query("test")

    assert "test" in queries
    assert "what is test" in queries
    # LLM queries should be added
    assert len(queries) >= 3

    # Verify LLM was called
    mock_llm_service.generate_response.assert_called_once()


async def test_explore_queries_sources_concurrently():
    """The source × query cross-product is independent network I/O: it must
    overlap instead of paying the sum of every call's latency serially."""
    import asyncio

    in_flight = 0
    max_in_flight = 0

    class SlowSource:
        async def search(self, query):
            nonlocal in_flight, max_in_flight
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
            await asyncio.sleep(0.05)
            in_flight -= 1
            return [{"content": f"finding for {query}", "source": "s"}]

        async def get_related(self, query):
            return {f"related-{query}"}

    explorer = ProactiveExplorer(
        sources=[SlowSource(), SlowSource(), SlowSource()], llm_service=Mock()
    )

    async def _queries(topic):
        return [topic, topic + " details"]

    explorer._expand_query = _queries  # 3 sources × 2 queries = 6 calls
    result = await explorer.explore("caching", max_results=10)

    assert max_in_flight > 1  # overlapped, not serial
    assert len(result.findings) == 6
    assert result.sources == ["s"]


async def test_explore_findings_order_deterministic():
    """Concurrency must not scramble aggregation: findings keep the
    source-major, query-minor order of the serial implementation."""

    class NamedSource:
        def __init__(self, name):
            self.name = name

        async def search(self, query):
            return [{"content": f"{self.name}:{query}", "source": self.name}]

        async def get_related(self, query):
            return set()

    explorer = ProactiveExplorer(
        sources=[NamedSource("s1"), NamedSource("s2")], llm_service=Mock()
    )

    async def _queries(topic):
        return ["q1", "q2"]

    explorer._expand_query = _queries
    result = await explorer.explore("t", max_results=10)
    assert result.findings == ["s1:q1", "s1:q2", "s2:q1", "s2:q2"]
