from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.memory.providers import InMemoryProvider, VectorMemoryProvider
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


class TestBatchedProviderWrites:
    """Consolidation and compression write whole batches; one add per item cost
    a separate embedding call and a separate durability-acked upsert each."""

    async def test_vector_provider_indexes_a_batch_in_one_call(self):
        from unittest.mock import AsyncMock, patch

        from core.memory.providers import VectorMemoryProvider
        from core.memory.types import MemoryItem, MemoryType

        with patch("core.memory.providers.get_vectorstore_service") as factory:
            service = AsyncMock()
            factory.return_value = service
            provider = VectorMemoryProvider(collection_name="c")

            items = [
                MemoryItem(content=f"m{i}", memory_type=MemoryType.EPISODIC)
                for i in range(5)
            ]
            await provider.add_many(items)

        service.index.assert_awaited_once()
        documents = service.index.await_args.kwargs["documents"]
        assert len(documents) == 5

    async def test_add_many_on_empty_batch_is_a_noop(self):
        from unittest.mock import AsyncMock, patch

        from core.memory.providers import VectorMemoryProvider

        with patch("core.memory.providers.get_vectorstore_service") as factory:
            service = AsyncMock()
            factory.return_value = service
            provider = VectorMemoryProvider(collection_name="c")
            await provider.add_many([])

        service.index.assert_not_awaited()

    async def test_helper_falls_back_for_item_at_a_time_providers(self):
        from core.memory.optimization_batch import add_items
        from core.memory.types import MemoryItem, MemoryType

        class _LegacyProvider:
            """Only implements the item-at-a-time protocol."""

            def __init__(self):
                self.added = []

            async def add(self, item):
                self.added.append(item)

        legacy = _LegacyProvider()
        items = [
            MemoryItem(content=f"m{i}", memory_type=MemoryType.EPISODIC)
            for i in range(3)
        ]

        await add_items(legacy, items, fanout_limit=8)

        assert len(legacy.added) == 3
