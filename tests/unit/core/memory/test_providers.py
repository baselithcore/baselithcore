import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from core.memory.providers import VectorMemoryProvider, InMemoryProvider
from core.memory.types import MemoryItem, MemoryType
from core.models.domain import Document


class TestInMemoryProvider:
    @pytest.fixture
    def provider(self):
        return InMemoryProvider()

    @pytest.mark.asyncio
    async def test_add_get_delete(self, provider):
        item = MemoryItem(
            content="test memory",
            memory_type=MemoryType.LONG_TERM,
            metadata={"source": "test"},
        )
        await provider.add(item)

        # Get
        retrieved = await provider.get(str(item.id))
        assert retrieved == item

        # Search (simple keyword)
        results = await provider.search("memory")
        assert len(results) == 1
        assert results[0].content == "test memory"

        # Delete
        success = await provider.delete(str(item.id))
        assert success
        assert await provider.get(str(item.id)) is None

    @pytest.mark.asyncio
    async def test_clear(self, provider):
        await provider.add(MemoryItem(content="m1", memory_type=MemoryType.LONG_TERM))
        await provider.add(MemoryItem(content="m2", memory_type=MemoryType.EPISODIC))

        await provider.clear(MemoryType.LONG_TERM)
        assert len(provider._checkpoints) == 1

        await provider.clear()
        assert len(provider._checkpoints) == 0


class TestVectorMemoryProvider:
    @pytest.fixture
    def mock_vector_service(self):
        with patch("core.memory.providers.get_vectorstore_service") as mock:
            service = AsyncMock()
            mock.return_value = service
            yield service

    @pytest.fixture
    def provider(self, mock_vector_service):
        return VectorMemoryProvider(collection_name="test_collection")

    @pytest.mark.asyncio
    async def test_add(self, provider, mock_vector_service):
        item = MemoryItem(content="vector test", memory_type=MemoryType.LONG_TERM)
        await provider.add(item)

        mock_vector_service.index.assert_called_once()
        args, kwargs = mock_vector_service.index.call_args
        documents = kwargs["documents"]
        assert len(documents) == 1
        assert documents[0].content == "vector test"
        assert kwargs["collection_name"] == "test_collection"

    @pytest.mark.asyncio
    async def test_get(self, provider, mock_vector_service):
        # Mock qdrant-like Record
        record = MagicMock()
        record.payload = {
            "text": "retrieved text",
            "type": "long_term",
            "source": "unit_test",
        }
        record.score = 0.95
        mock_vector_service.retrieve.return_value = [record]

        item = await provider.get("some-id")
        assert item is not None
        assert item.content == "retrieved text"
        assert item.memory_type == MemoryType.LONG_TERM
        assert item.score == 0.95

    @pytest.mark.asyncio
    async def test_search(self, provider, mock_vector_service):
        provider.embedder = MagicMock()
        provider.embedder.encode.return_value = [0.1, 0.2]

        # Mock SearchResult
        res = MagicMock()
        res.document = Document(
            id="doc1", content="search result", metadata={"type": "episodic"}
        )
        res.score = 0.88
        mock_vector_service.search.return_value = [res]

        results = await provider.search("query", memory_type=MemoryType.EPISODIC)
        assert len(results) == 1
        assert results[0].content == "search result"
        assert results[0].memory_type == MemoryType.EPISODIC

    @pytest.mark.asyncio
    async def test_delete_clear(self, provider, mock_vector_service):
        await provider.delete("id123")
        mock_vector_service.delete_document.assert_called_with(
            "id123", collection_name="test_collection"
        )

        await provider.clear()
        mock_vector_service.delete_collection.assert_called_with("test_collection")
