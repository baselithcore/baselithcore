"""
Tests for baselith/core/services/vectorstore/providers/qdrant_provider.py

Tests QdrantProvider with mocked AsyncQdrant client.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
class TestQdrantProviderSearch:
    """Tests for QdrantProvider.search method."""

    @patch(
        "baselith.core.services.vectorstore.providers.qdrant_provider.AsyncQdrantClient"
    )
    async def test_search_returns_results(self, mock_qdrant_client):
        """Verify search returns query results."""
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.points = [MagicMock(), MagicMock()]
        mock_client.query_points.return_value = mock_response
        mock_qdrant_client.return_value = mock_client

        from baselith.core.services.vectorstore.providers.qdrant_provider import (
            QdrantProvider,
        )

        provider = QdrantProvider()
        results = await provider.search("test_collection", [0.1, 0.2, 0.3], limit=5)

        assert len(results) == 2
        mock_client.query_points.assert_called_once()

    @patch(
        "baselith.core.services.vectorstore.providers.qdrant_provider.AsyncQdrantClient"
    )
    async def test_search_raises_on_error(self, mock_qdrant_client):
        """Verify search raises VectorStoreError on failure."""
        mock_client = AsyncMock()
        mock_client.query_points.side_effect = Exception("Search failed")
        mock_qdrant_client.return_value = mock_client

        from baselith.core.services.vectorstore.exceptions import VectorStoreError
        from baselith.core.services.vectorstore.providers.qdrant_provider import (
            QdrantProvider,
        )

        provider = QdrantProvider()

        with pytest.raises(VectorStoreError) as exc_info:
            await provider.search("test_collection", [0.1, 0.2, 0.3])

        assert "Search failed" in str(exc_info.value)


@pytest.mark.asyncio
class TestQdrantProviderRetrieve:
    """Tests for QdrantProvider.retrieve method."""

    @patch(
        "baselith.core.services.vectorstore.providers.qdrant_provider.AsyncQdrantClient"
    )
    async def test_retrieve_points(self, mock_qdrant_client):
        """Verify retrieve returns specific points by ID."""
        mock_client = AsyncMock()
        mock_points = [MagicMock(), MagicMock()]
        mock_client.retrieve.return_value = mock_points
        mock_qdrant_client.return_value = mock_client

        from baselith.core.services.vectorstore.providers.qdrant_provider import (
            QdrantProvider,
        )

        provider = QdrantProvider()
        results = await provider.retrieve("test_collection", [1, 2])

        assert results == mock_points
        mock_client.retrieve.assert_called_once_with(
            collection_name="test_collection",
            ids=[1, 2],
        )

    @patch(
        "baselith.core.services.vectorstore.providers.qdrant_provider.AsyncQdrantClient"
    )
    async def test_retrieve_raises_on_error(self, mock_qdrant_client):
        """Verify retrieve raises VectorStoreError on failure."""
        mock_client = AsyncMock()
        mock_client.retrieve.side_effect = Exception("Retrieve failed")
        mock_qdrant_client.return_value = mock_client

        from baselith.core.services.vectorstore.exceptions import VectorStoreError
        from baselith.core.services.vectorstore.providers.qdrant_provider import (
            QdrantProvider,
        )

        provider = QdrantProvider()

        with pytest.raises(VectorStoreError) as exc_info:
            await provider.retrieve("test_collection", [1])

        assert "Retrieve failed" in str(exc_info.value)


@pytest.mark.asyncio
class TestQdrantProviderDelete:
    """Tests for QdrantProvider.delete method."""

    @patch(
        "baselith.core.services.vectorstore.providers.qdrant_provider.AsyncQdrantClient"
    )
    async def test_delete_points(self, mock_qdrant_client):
        """Verify delete removes points correctly."""
        mock_client = AsyncMock()
        mock_qdrant_client.return_value = mock_client

        from baselith.core.services.vectorstore.providers.qdrant_provider import (
            QdrantProvider,
        )

        provider = QdrantProvider()
        await provider.delete("test_collection", [1, 2, 3])

        mock_client.delete.assert_called_once()
        call_kwargs = mock_client.delete.call_args[1]
        assert call_kwargs["collection_name"] == "test_collection"
        assert call_kwargs["points_selector"] == [1, 2, 3]

    @patch(
        "baselith.core.services.vectorstore.providers.qdrant_provider.AsyncQdrantClient"
    )
    async def test_delete_raises_on_error(self, mock_qdrant_client):
        """Verify delete raises VectorStoreError on failure."""
        mock_client = AsyncMock()
        mock_client.delete.side_effect = Exception("Delete failed")
        mock_qdrant_client.return_value = mock_client

        from baselith.core.services.vectorstore.exceptions import VectorStoreError
        from baselith.core.services.vectorstore.providers.qdrant_provider import (
            QdrantProvider,
        )

        provider = QdrantProvider()

        with pytest.raises(VectorStoreError) as exc_info:
            await provider.delete("test_collection", [1])

        assert "Delete failed" in str(exc_info.value)


@pytest.mark.asyncio
class TestQdrantProviderCollectionExists:
    """Tests for QdrantProvider.collection_exists method."""

    @patch(
        "baselith.core.services.vectorstore.providers.qdrant_provider.AsyncQdrantClient"
    )
    async def test_collection_exists_returns_true(self, mock_qdrant_client):
        """Verify collection_exists returns True when collection exists."""
        mock_client = AsyncMock()
        mock_collection = MagicMock()
        mock_collection.name = "test_collection"
        mock_collections = MagicMock()
        mock_collections.collections = [mock_collection]
        mock_client.get_collections.return_value = mock_collections
        mock_qdrant_client.return_value = mock_client

        from baselith.core.services.vectorstore.providers.qdrant_provider import (
            QdrantProvider,
        )

        provider = QdrantProvider()
        result = await provider.collection_exists("test_collection")

        assert result is True

    @patch(
        "baselith.core.services.vectorstore.providers.qdrant_provider.AsyncQdrantClient"
    )
    async def test_collection_exists_returns_false(self, mock_qdrant_client):
        """Verify collection_exists returns False when collection doesn't exist."""
        mock_client = AsyncMock()
        mock_collections = MagicMock()
        mock_collections.collections = []
        mock_client.get_collections.return_value = mock_collections
        mock_qdrant_client.return_value = mock_client

        from baselith.core.services.vectorstore.providers.qdrant_provider import (
            QdrantProvider,
        )

        provider = QdrantProvider()
        result = await provider.collection_exists("nonexistent")

        assert result is False

    @patch(
        "baselith.core.services.vectorstore.providers.qdrant_provider.AsyncQdrantClient"
    )
    async def test_collection_exists_raises_when_unreachable(self, mock_qdrant_client):
        """An unreachable store must not be reported as a missing collection."""
        mock_client = AsyncMock()
        mock_client.get_collections.side_effect = Exception("connection refused")
        mock_qdrant_client.return_value = mock_client

        from baselith.core.services.vectorstore.exceptions import VectorStoreError
        from baselith.core.services.vectorstore.providers.qdrant_provider import (
            QdrantProvider,
        )

        provider = QdrantProvider()
        with pytest.raises(VectorStoreError, match="connection refused"):
            await provider.collection_exists("test_collection")


@pytest.mark.asyncio
class TestQdrantProviderScroll:
    """Tests for QdrantProvider.scroll method."""

    @patch(
        "baselith.core.services.vectorstore.providers.qdrant_provider.AsyncQdrantClient"
    )
    async def test_scroll_returns_results(self, mock_qdrant_client):
        """Verify scroll returns scroll response."""
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_client.scroll.return_value = mock_response
        mock_qdrant_client.return_value = mock_client

        from baselith.core.services.vectorstore.providers.qdrant_provider import (
            QdrantProvider,
        )

        provider = QdrantProvider()
        result = await provider.scroll("test_collection", limit=50, offset=None)

        assert result is mock_response
        mock_client.scroll.assert_called_once()

    @patch(
        "baselith.core.services.vectorstore.providers.qdrant_provider.AsyncQdrantClient"
    )
    async def test_scroll_raises_on_error(self, mock_qdrant_client):
        """Verify scroll raises VectorStoreError on failure."""
        mock_client = AsyncMock()
        mock_client.scroll.side_effect = Exception("Scroll failed")
        mock_qdrant_client.return_value = mock_client

        from baselith.core.services.vectorstore.exceptions import VectorStoreError
        from baselith.core.services.vectorstore.providers.qdrant_provider import (
            QdrantProvider,
        )

        provider = QdrantProvider()

        with pytest.raises(VectorStoreError) as exc_info:
            await provider.scroll("test_collection")

        assert "Scroll failed" in str(exc_info.value)
