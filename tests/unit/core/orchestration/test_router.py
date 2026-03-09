import pytest
from unittest.mock import AsyncMock, MagicMock
from core.orchestration.router import Router, RouteRequest
from core.config import RouterConfig


class TestRouter:
    @pytest.fixture
    def mock_llm_service(self):
        return AsyncMock()

    @pytest.fixture
    def mock_vector_store(self):
        return AsyncMock()

    @pytest.fixture
    def mock_embedder(self):
        mock = AsyncMock()
        mock.encode.return_value = [[0.1, 0.2, 0.3]]
        return mock

    @pytest.fixture
    def config(self):
        return RouterConfig(retrieval_limit=5, score_threshold=0.5, max_candidates=2)

    @pytest.fixture
    def router(self, config, mock_llm_service, mock_vector_store, mock_embedder):
        return Router(
            config=config,
            llm_service=mock_llm_service,
            vector_store=mock_vector_store,
            embedder=mock_embedder,
        )

    @pytest.mark.asyncio
    async def test_route_calls_semantic_route(self, router):
        router._semantic_route = AsyncMock(return_value=[])
        request = RouteRequest(query="test query")
        await router.route(request)
        router._semantic_route.assert_called_once_with(request)

    @pytest.mark.asyncio
    async def test_semantic_route_sorting_and_limiting(self, router, mock_vector_store):
        # Mock search results with duplicate agents and different scores
        res1 = MagicMock()
        res1.document.metadata = {"agent_id": "agent_a"}
        res1.score = 0.8

        res2 = MagicMock()
        res2.document.metadata = {"agent_id": "agent_b"}
        res2.score = 0.9

        res3 = MagicMock()
        res3.document.metadata = {"agent_id": "agent_a"}
        res3.score = 0.85  # Higher than res1

        mock_vector_store.search.return_value = [res1, res2, res3]

        request = RouteRequest(query="find something")
        results = await router.route(request)

        # Should be sorted: agent_b (0.9), agent_a (0.85)
        assert len(results) == 2
        assert results[0].agent_id == "agent_b"
        assert results[0].confidence == 0.9
        assert results[1].agent_id == "agent_a"
        assert results[1].confidence == 0.85

    @pytest.mark.asyncio
    async def test_semantic_route_no_agent_id(self, router, mock_vector_store):
        res = MagicMock()
        res.document.metadata = {}  # Missing agent_id
        res.score = 0.9

        mock_vector_store.search.return_value = [res]

        results = await router.route(RouteRequest(query="test"))
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_semantic_route_threshold(self, router, mock_vector_store):
        # Vector store handles threshold internally usually, but let's verify router handles search kwargs
        router.config.score_threshold = 0.7
        mock_vector_store.search.return_value = []

        await router.route(RouteRequest(query="test"))

        mock_vector_store.search.assert_called_once()
        args, kwargs = mock_vector_store.search.call_args
        assert kwargs["score_threshold"] == 0.7
