"""
Tests for core/services/vectorstore/providers/qdrant_provider.py

Tests QdrantProvider with mocked AsyncQdrant client.
"""

import pytest
from unittest.mock import MagicMock, patch, AsyncMock


class TestQdrantProviderInit:
    """Tests for QdrantProvider initialization."""

    @patch("core.services.vectorstore.providers.qdrant_provider.AsyncQdrantClient")
    def test_init_with_defaults(self, mock_qdrant_client):
        """Verify provider initializes with default settings."""
        mock_client = AsyncMock()
        mock_qdrant_client.return_value = mock_client

        from core.services.vectorstore.providers.qdrant_provider import QdrantProvider

        provider = QdrantProvider()

        mock_qdrant_client.assert_called_once_with(
            host="localhost",
            port=6333,
            grpc_port=None,
            prefer_grpc=False,
        )
        assert provider.client is mock_client

    @patch("core.services.vectorstore.providers.qdrant_provider.AsyncQdrantClient")
    def test_init_with_custom_settings(self, mock_qdrant_client):
        """Verify provider initializes with custom settings."""
        mock_client = AsyncMock()
        mock_qdrant_client.return_value = mock_client

        from core.services.vectorstore.providers.qdrant_provider import QdrantProvider

        provider = QdrantProvider(
            host="qdrant.example.com",
            port=6334,
            grpc_port=6335,
            prefer_grpc=True,
        )

        assert provider.host == "qdrant.example.com"
        assert provider.port == 6334
        assert provider.grpc_port == 6335
        assert provider.prefer_grpc is True

    @patch("core.services.vectorstore.providers.qdrant_provider.AsyncQdrantClient")
    def test_init_raises_on_connection_error(self, mock_qdrant_client):
        """Verify provider raises VectorStoreError on connection failure."""
        mock_qdrant_client.side_effect = Exception("Connection refused")

        from core.services.vectorstore.providers.qdrant_provider import QdrantProvider
        from core.services.vectorstore.exceptions import VectorStoreError

        with pytest.raises(VectorStoreError) as exc_info:
            QdrantProvider()

        assert "initialization failed" in str(exc_info.value)


@pytest.mark.asyncio
class TestQdrantProviderCreateCollection:
    """Tests for QdrantProvider.create_collection method."""

    @patch("core.services.vectorstore.providers.qdrant_provider.AsyncQdrantClient")
    async def test_create_collection_basic(self, mock_qdrant_client):
        """Verify create_collection creates collection with defaults."""
        mock_client = AsyncMock()
        mock_qdrant_client.return_value = mock_client

        from core.services.vectorstore.providers.qdrant_provider import QdrantProvider

        provider = QdrantProvider()
        await provider.create_collection("test_collection", vector_size=384)

        mock_client.create_collection.assert_called_once()
        call_kwargs = mock_client.create_collection.call_args[1]
        assert call_kwargs["collection_name"] == "test_collection"

    @patch("core.services.vectorstore.providers.qdrant_provider.AsyncQdrantClient")
    async def test_create_collection_raises_on_error(self, mock_qdrant_client):
        """Verify create_collection raises VectorStoreError on generic failure."""
        mock_client = AsyncMock()
        mock_client.create_collection.side_effect = Exception("Connection error")
        mock_qdrant_client.return_value = mock_client
        mock_client.get_collections.return_value = MagicMock(collections=[])

        from core.services.vectorstore.providers.qdrant_provider import QdrantProvider
        from core.services.vectorstore.exceptions import VectorStoreError

        provider = QdrantProvider()

        with pytest.raises(VectorStoreError) as exc_info:
            await provider.create_collection("test_collection", vector_size=384)

        assert "Collection creation failed" in str(exc_info.value)

    @patch("core.services.vectorstore.providers.qdrant_provider.AsyncQdrantClient")
    async def test_create_collection_idempotent(self, mock_qdrant_client):
        """Verify create_collection ignores 'already exists' errors."""
        mock_client = AsyncMock()
        mock_client.create_collection.side_effect = Exception(
            "Collection already exists"
        )
        mock_qdrant_client.return_value = mock_client
        mock_client.get_collections.return_value = MagicMock(collections=[])

        from core.services.vectorstore.providers.qdrant_provider import QdrantProvider

        provider = QdrantProvider()

        # Should NOT raise exception
        await provider.create_collection("test_collection", vector_size=384)

        mock_client.create_collection.assert_called_once()


@pytest.mark.asyncio
class TestQdrantProviderUpsert:
    """Tests for QdrantProvider.upsert method."""

    @patch("core.services.vectorstore.providers.qdrant_provider.AsyncQdrantClient")
    async def test_upsert_points(self, mock_qdrant_client):
        """Verify upsert inserts points correctly."""
        mock_client = AsyncMock()
        mock_qdrant_client.return_value = mock_client

        from core.services.vectorstore.providers.qdrant_provider import QdrantProvider

        provider = QdrantProvider()
        points = [
            {"id": 1, "vector": [0.1, 0.2, 0.3], "payload": {"text": "hello"}},
            {"id": 2, "vector": [0.4, 0.5, 0.6], "payload": {"text": "world"}},
        ]

        await provider.upsert("test_collection", points)

        mock_client.upsert.assert_called_once()
        call_kwargs = mock_client.upsert.call_args[1]
        assert call_kwargs["collection_name"] == "test_collection"
        assert len(call_kwargs["points"]) == 2

    @patch("core.services.vectorstore.providers.qdrant_provider.AsyncQdrantClient")
    async def test_upsert_with_wait_false(self, mock_qdrant_client):
        """Verify upsert respects wait parameter."""
        mock_client = AsyncMock()
        mock_qdrant_client.return_value = mock_client

        from core.services.vectorstore.providers.qdrant_provider import QdrantProvider

        provider = QdrantProvider()
        points = [{"id": 1, "vector": [0.1, 0.2, 0.3]}]

        await provider.upsert("test_collection", points, wait=False)

        call_kwargs = mock_client.upsert.call_args[1]
        assert call_kwargs["wait"] is False

    @patch("core.services.vectorstore.providers.qdrant_provider.AsyncQdrantClient")
    async def test_upsert_raises_on_error(self, mock_qdrant_client):
        """Verify upsert raises VectorStoreError on failure."""
        mock_client = AsyncMock()
        mock_client.upsert.side_effect = Exception("Insert failed")
        mock_qdrant_client.return_value = mock_client

        from core.services.vectorstore.providers.qdrant_provider import QdrantProvider
        from core.services.vectorstore.exceptions import VectorStoreError

        provider = QdrantProvider()

        with pytest.raises(VectorStoreError) as exc_info:
            await provider.upsert("test_collection", [{"id": 1, "vector": [0.1]}])

        assert "Upsert failed" in str(exc_info.value)


@pytest.mark.asyncio
class TestQdrantProviderSearch:
    """Tests for QdrantProvider.search method."""

    @patch("core.services.vectorstore.providers.qdrant_provider.AsyncQdrantClient")
    async def test_search_returns_results(self, mock_qdrant_client):
        """Verify search returns query results."""
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.points = [MagicMock(), MagicMock()]
        mock_client.query_points.return_value = mock_response
        mock_qdrant_client.return_value = mock_client

        from core.services.vectorstore.providers.qdrant_provider import QdrantProvider

        provider = QdrantProvider()
        results = await provider.search("test_collection", [0.1, 0.2, 0.3], limit=5)

        assert len(results) == 2
        mock_client.query_points.assert_called_once()

    @patch("core.services.vectorstore.providers.qdrant_provider.AsyncQdrantClient")
    async def test_search_raises_on_error(self, mock_qdrant_client):
        """Verify search raises VectorStoreError on failure."""
        mock_client = AsyncMock()
        mock_client.query_points.side_effect = Exception("Search failed")
        mock_qdrant_client.return_value = mock_client

        from core.services.vectorstore.providers.qdrant_provider import QdrantProvider
        from core.services.vectorstore.exceptions import VectorStoreError

        provider = QdrantProvider()

        with pytest.raises(VectorStoreError) as exc_info:
            await provider.search("test_collection", [0.1, 0.2, 0.3])

        assert "Search failed" in str(exc_info.value)


@pytest.mark.asyncio
class TestQdrantProviderRetrieve:
    """Tests for QdrantProvider.retrieve method."""

    @patch("core.services.vectorstore.providers.qdrant_provider.AsyncQdrantClient")
    async def test_retrieve_points(self, mock_qdrant_client):
        """Verify retrieve returns specific points by ID."""
        mock_client = AsyncMock()
        mock_points = [MagicMock(), MagicMock()]
        mock_client.retrieve.return_value = mock_points
        mock_qdrant_client.return_value = mock_client

        from core.services.vectorstore.providers.qdrant_provider import QdrantProvider

        provider = QdrantProvider()
        results = await provider.retrieve("test_collection", [1, 2])

        assert results == mock_points
        mock_client.retrieve.assert_called_once_with(
            collection_name="test_collection",
            ids=[1, 2],
        )

    @patch("core.services.vectorstore.providers.qdrant_provider.AsyncQdrantClient")
    async def test_retrieve_raises_on_error(self, mock_qdrant_client):
        """Verify retrieve raises VectorStoreError on failure."""
        mock_client = AsyncMock()
        mock_client.retrieve.side_effect = Exception("Retrieve failed")
        mock_qdrant_client.return_value = mock_client

        from core.services.vectorstore.providers.qdrant_provider import QdrantProvider
        from core.services.vectorstore.exceptions import VectorStoreError

        provider = QdrantProvider()

        with pytest.raises(VectorStoreError) as exc_info:
            await provider.retrieve("test_collection", [1])

        assert "Retrieve failed" in str(exc_info.value)


@pytest.mark.asyncio
class TestQdrantProviderDelete:
    """Tests for QdrantProvider.delete method."""

    @patch("core.services.vectorstore.providers.qdrant_provider.AsyncQdrantClient")
    async def test_delete_points(self, mock_qdrant_client):
        """Verify delete removes points correctly."""
        mock_client = AsyncMock()
        mock_qdrant_client.return_value = mock_client

        from core.services.vectorstore.providers.qdrant_provider import QdrantProvider

        provider = QdrantProvider()
        await provider.delete("test_collection", [1, 2, 3])

        mock_client.delete.assert_called_once()
        call_kwargs = mock_client.delete.call_args[1]
        assert call_kwargs["collection_name"] == "test_collection"
        assert call_kwargs["points_selector"] == [1, 2, 3]

    @patch("core.services.vectorstore.providers.qdrant_provider.AsyncQdrantClient")
    async def test_delete_raises_on_error(self, mock_qdrant_client):
        """Verify delete raises VectorStoreError on failure."""
        mock_client = AsyncMock()
        mock_client.delete.side_effect = Exception("Delete failed")
        mock_qdrant_client.return_value = mock_client

        from core.services.vectorstore.providers.qdrant_provider import QdrantProvider
        from core.services.vectorstore.exceptions import VectorStoreError

        provider = QdrantProvider()

        with pytest.raises(VectorStoreError) as exc_info:
            await provider.delete("test_collection", [1])

        assert "Delete failed" in str(exc_info.value)


@pytest.mark.asyncio
class TestQdrantProviderCollectionExists:
    """Tests for QdrantProvider.collection_exists method."""

    @patch("core.services.vectorstore.providers.qdrant_provider.AsyncQdrantClient")
    async def test_collection_exists_returns_true(self, mock_qdrant_client):
        """Verify collection_exists returns True when collection exists."""
        mock_client = AsyncMock()
        mock_collection = MagicMock()
        mock_collection.name = "test_collection"
        mock_collections = MagicMock()
        mock_collections.collections = [mock_collection]
        mock_client.get_collections.return_value = mock_collections
        mock_qdrant_client.return_value = mock_client

        from core.services.vectorstore.providers.qdrant_provider import QdrantProvider

        provider = QdrantProvider()
        result = await provider.collection_exists("test_collection")

        assert result is True

    @patch("core.services.vectorstore.providers.qdrant_provider.AsyncQdrantClient")
    async def test_collection_exists_returns_false(self, mock_qdrant_client):
        """Verify collection_exists returns False when collection doesn't exist."""
        mock_client = AsyncMock()
        mock_collections = MagicMock()
        mock_collections.collections = []
        mock_client.get_collections.return_value = mock_collections
        mock_qdrant_client.return_value = mock_client

        from core.services.vectorstore.providers.qdrant_provider import QdrantProvider

        provider = QdrantProvider()
        result = await provider.collection_exists("nonexistent")

        assert result is False

    @patch("core.services.vectorstore.providers.qdrant_provider.AsyncQdrantClient")
    async def test_collection_exists_returns_false_on_error(self, mock_qdrant_client):
        """Verify collection_exists returns False on error."""
        mock_client = AsyncMock()
        mock_client.get_collections.side_effect = Exception("Error")
        mock_qdrant_client.return_value = mock_client

        from core.services.vectorstore.providers.qdrant_provider import QdrantProvider

        provider = QdrantProvider()
        result = await provider.collection_exists("test_collection")

        assert result is False


@pytest.mark.asyncio
class TestQdrantProviderScroll:
    """Tests for QdrantProvider.scroll method."""

    @patch("core.services.vectorstore.providers.qdrant_provider.AsyncQdrantClient")
    async def test_scroll_returns_results(self, mock_qdrant_client):
        """Verify scroll returns scroll response."""
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_client.scroll.return_value = mock_response
        mock_qdrant_client.return_value = mock_client

        from core.services.vectorstore.providers.qdrant_provider import QdrantProvider

        provider = QdrantProvider()
        result = await provider.scroll("test_collection", limit=50, offset=None)

        assert result is mock_response
        mock_client.scroll.assert_called_once()

    @patch("core.services.vectorstore.providers.qdrant_provider.AsyncQdrantClient")
    async def test_scroll_raises_on_error(self, mock_qdrant_client):
        """Verify scroll raises VectorStoreError on failure."""
        mock_client = AsyncMock()
        mock_client.scroll.side_effect = Exception("Scroll failed")
        mock_qdrant_client.return_value = mock_client

        from core.services.vectorstore.providers.qdrant_provider import QdrantProvider
        from core.services.vectorstore.exceptions import VectorStoreError

        provider = QdrantProvider()

        with pytest.raises(VectorStoreError) as exc_info:
            await provider.scroll("test_collection")

        assert "Scroll failed" in str(exc_info.value)
