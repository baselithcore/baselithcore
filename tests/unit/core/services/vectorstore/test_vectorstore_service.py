"""
Unit tests for VectorStore service.

Tests the VectorStore service with mocked providers using strict Domain Models.
"""

from unittest.mock import Mock, patch, AsyncMock
import pytest

from core.services.vectorstore import VectorStoreService
from core.models.domain import Document, SearchResult
from core.services.vectorstore.exceptions import VectorStoreError


@pytest.fixture(autouse=True)
def mock_redis_cache():
    """Mock RedisCache to avoid connection issues during tests."""
    with patch("core.services.vectorstore.service.RedisCache") as mock:
        mock.return_value.get = AsyncMock(return_value=None)
        mock.return_value.set = AsyncMock()
        mock.return_value.delete = AsyncMock()
        yield mock


class TestVectorStoreService:
    """Tests for VectorStore service."""

    @patch("core.services.vectorstore.service.get_vectorstore_config")
    @patch("core.services.vectorstore.service.QdrantProvider")
    def test_initialization(self, mock_provider_class, mock_config):
        """Test service initialization."""
        mock_config.return_value = Mock(
            provider="qdrant",
            collection_name="test",
            host="localhost",
            port=6333,
            grpc_port=6334,
            embedding_dim=384,
            search_limit=10,
        )

        service = VectorStoreService()

        assert service.config.provider == "qdrant"
        mock_provider_class.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_collection(self):
        """Test collection creation."""
        mock_config = Mock(
            provider="qdrant",
            collection_name="test",
            embedding_dim=384,
            search_limit=10,
        )
        mock_provider = AsyncMock()

        service = VectorStoreService(config=mock_config, provider=mock_provider)
        await service.create_collection()

        mock_provider.create_collection.assert_called_once_with(
            collection_name="test",
            vector_size=384,
        )

    @pytest.mark.asyncio
    async def test_search(self):
        """Test vector search returning SearchResult objects."""
        mock_config = Mock(
            provider="qdrant",
            collection_name="test",
            search_limit=10,
        )
        mock_provider = AsyncMock()

        # Provider returns raw objects (simulating Qdrant hit)
        mock_hit = Mock()
        mock_hit.id = "1"
        mock_hit.score = 0.9
        # Code implementation looks for 'text' or 'chunk_body' in payload
        mock_hit.payload = {"text": "found", "source": "src"}
        mock_hit.vector = None

        mock_provider.search.return_value = [mock_hit]

        service = VectorStoreService(config=mock_config, provider=mock_provider)
        # RedisCache is already mocked by fixture
        service.search_cache.get.return_value = None

        results = await service.search([0.1, 0.2, 0.3])

        assert len(results) == 1
        assert isinstance(results[0], SearchResult)
        assert results[0].score == 0.9
        assert isinstance(results[0].document, Document)
        assert results[0].document.content == "found"
        assert results[0].document.metadata["source"] == "src"

        mock_provider.search.assert_called_once()

    @pytest.mark.asyncio
    async def test_retrieve(self):
        """Test point retrieval by ID."""
        mock_config = Mock(
            provider="qdrant",
            collection_name="test",
        )
        mock_provider = AsyncMock()
        # Retrieve returns raw objects
        mock_record = Mock()
        mock_record.id = "1"
        mock_record.payload = {
            "text": "found"
        }  # Note: legacy points used 'text' in payload often

        mock_provider.retrieve.return_value = [mock_record]

        service = VectorStoreService(config=mock_config, provider=mock_provider)
        results = await service.retrieve(["1"])

        assert len(results) == 1
        assert results[0].payload["text"] == "found"
        mock_provider.retrieve.assert_called_once_with(
            collection_name="test", point_ids=["1"], tenant_id="default"
        )

    @pytest.mark.asyncio
    @patch("core.services.vectorstore.service.get_embeddings_cached")
    async def test_index_documents(self, mock_get_embeddings):
        """Test document indexing with Document objects."""
        mock_config = Mock(
            provider="qdrant",
            collection_name="test",
            embedding_dim=384,
            embedding_model="test-model",
            search_limit=10,
        )
        mock_provider = AsyncMock()

        service = VectorStoreService(config=mock_config, provider=mock_provider)

        # Mock embedder
        mock_embedder = Mock()

        # Mock cached embeddings return
        mock_get_embeddings.return_value = [[0.1, 0.2]]

        documents = [
            Document(
                id="doc1",
                content="This is test content. " * 50,
                metadata={"source": "test"},
            )
        ]

        count = await service.index(documents, embedder=mock_embedder)

        # Expect upsert to be called with dicts that include embeddings
        assert count == 1
        mock_provider.upsert.assert_called_once()

        # Verify passed arguments (Service uses kwargs for upsert)
        call_kwargs = mock_provider.upsert.call_args.kwargs
        assert call_kwargs["collection_name"] == "test"
        points = call_kwargs["points"]
        points = call_kwargs["points"]
        assert len(points) >= 1  # Should be at least one chunk

    @pytest.mark.asyncio
    async def test_delete_collection(self):
        """Test collection deletion across all branches."""
        mock_config = Mock(collection_name="test")

        # Branch 1: provider.delete_collection exists
        mock_provider1 = AsyncMock()
        service1 = VectorStoreService(config=mock_config, provider=mock_provider1)
        await service1.delete_collection()
        mock_provider1.delete_collection.assert_called_once()

        # Branch 2: provider.client.delete_collection exists
        mock_provider2 = (
            Mock()
        )  # Not AsyncMock for the direct check if not using it as coroutine in hasattr
        mock_provider2.client = AsyncMock()
        del mock_provider2.delete_collection  # Ensure it doesn't have it
        service2 = VectorStoreService(config=mock_config, provider=mock_provider2)
        await service2.delete_collection()
        mock_provider2.client.delete_collection.assert_called_once()

        # Branch 3: No support
        mock_provider3 = Mock()
        del mock_provider3.delete_collection
        del mock_provider3.client
        service3 = VectorStoreService(config=mock_config, provider=mock_provider3)
        await service3.delete_collection()  # Should just log warning

    @pytest.mark.asyncio
    async def test_search_cache_hit(self):
        """Test search cache hit."""
        mock_config = Mock(collection_name="test", search_limit=10)
        mock_provider = AsyncMock()
        service = VectorStoreService(config=mock_config, provider=mock_provider)

        cached_data = [{"document": {"id": "c1", "content": "cached"}, "score": 1.0}]
        service.search_cache.get.return_value = cached_data

        results = await service.search([0.1] * 384)
        assert len(results) == 1
        assert results[0].document.content == "cached"
        mock_provider.search.assert_not_called()

    @pytest.mark.asyncio
    async def test_search_rerank(self):
        """Test search with reranking."""
        mock_config = Mock(collection_name="test", search_limit=10)
        mock_provider = AsyncMock()
        service = VectorStoreService(config=mock_config, provider=mock_provider)

        mock_hit = Mock(id="1", score=0.5, payload={"text": "found"})
        mock_hit.vector = None
        mock_provider.search.return_value = [mock_hit]

        with patch("core.services.retrieval.reranker.get_reranker") as mock_get:
            mock_reranker = Mock()
            mock_get.return_value = mock_reranker
            mock_reranker.rerank.return_value = [
                SearchResult(document=Document(id="1", content="found"), score=0.9)
            ]

            results = await service.search([0.1] * 384, query_text="test", rerank=True)
            assert results[0].score == 0.9
            mock_reranker.rerank.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_document(self):
        """Test document deletion."""
        mock_provider = AsyncMock()
        service = VectorStoreService(provider=mock_provider)
        await service.delete_document("doc1")
        mock_provider.delete_by_filter.assert_called_once_with(
            collection_name=service.config.collection_name,
            key="document_id",
            value="doc1",
            tenant_id="default",
        )

    @pytest.mark.asyncio
    async def test_error_paths(self):
        """Test various error paths."""
        service = VectorStoreService(provider=AsyncMock())

        # Missing embedder for index
        with pytest.raises(VectorStoreError, match="Embedder is required"):
            await service.index([Document(id="1", content="c")])

        # Index with no chunks
        mock_embedder = Mock()
        with patch("core.services.vectorstore.service.chunk_text", return_value=[]):
            count = await service.index(
                [Document(id="1", content="c")], embedder=mock_embedder
            )
            assert count == 0

    @patch("core.services.vectorstore.service.get_vectorstore_config")
    def test_create_provider_unsupported(self, mock_config):
        mock_config.return_value = Mock(provider="unsupported")
        with pytest.raises(VectorStoreError, match="Unsupported provider"):
            VectorStoreService()

    def test_get_vectorstore_service(self):
        from core.services.vectorstore.service import get_vectorstore_service
        import core.services.vectorstore.service as vs_module

        # Reset global to ensure instantiation is tested
        old_service = vs_module._vectorstore_service
        vs_module._vectorstore_service = None

        try:
            with patch(
                "core.services.vectorstore.service.VectorStoreService"
            ) as mock_cls:
                service = get_vectorstore_service()
                assert service == mock_cls.return_value
                # Second call should return same
                service2 = get_vectorstore_service()
                assert service2 == service
                assert mock_cls.call_count == 1
        finally:
            vs_module._vectorstore_service = old_service

    @pytest.mark.asyncio
    async def test_scroll_and_query(self):
        mock_provider = AsyncMock()
        service = VectorStoreService(provider=mock_provider)

        await service.scroll(limit=5)
        mock_provider.scroll.assert_called_with(
            collection_name=service.config.collection_name,
            limit=5,
            offset=None,
            tenant_id="default",
        )

        # query_points
        mock_provider.client = Mock()
        mock_provider.client.query_points = AsyncMock()
        await service.query_points([0.1] * 384)
        mock_provider.client.query_points.assert_called_once()
