"""An existing Qdrant collection's width must match VECTORSTORE_EMBEDDING_DIM.

``create_collection`` returned early for an existing collection, so a changed
embedding model or dimension only surfaced later as per-batch upsert failures
(and silently meaningless searches). Setup now fails once, with an error that
names both sizes and the way out — and never drops the collection.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.services.vectorstore._dimension import (
    EmbeddingDimensionMismatchError,
    qdrant_vector_size,
)
from core.services.vectorstore.exceptions import VectorStoreError


def _info(vectors):
    return SimpleNamespace(
        config=SimpleNamespace(params=SimpleNamespace(vectors=vectors))
    )


def test_vector_size_of_an_unnamed_vector():
    assert qdrant_vector_size(_info(SimpleNamespace(size=768))) == 768


def test_vector_size_of_a_single_named_vector():
    assert qdrant_vector_size(_info({"dense": SimpleNamespace(size=384)})) == 384


def test_vector_size_unknown_shape_is_none():
    assert qdrant_vector_size(_info(None)) is None
    assert qdrant_vector_size(SimpleNamespace()) is None


def _client(existing_size: int) -> AsyncMock:
    client = AsyncMock()
    client.get_collections.return_value = MagicMock(
        collections=[SimpleNamespace(name="documents")]
    )
    client.get_collection.return_value = _info(SimpleNamespace(size=existing_size))
    return client


@pytest.mark.asyncio
@patch("core.services.vectorstore.providers.qdrant_provider.AsyncQdrantClient")
async def test_mismatched_existing_collection_fails_with_an_actionable_error(
    mock_qdrant_client,
):
    client = _client(existing_size=768)
    mock_qdrant_client.return_value = client
    from core.services.vectorstore.providers.qdrant_provider import QdrantProvider

    with pytest.raises(EmbeddingDimensionMismatchError) as excinfo:
        await QdrantProvider().create_collection("documents", vector_size=384)

    message = str(excinfo.value)
    assert "documents" in message and "768" in message and "384" in message
    assert "VECTORSTORE_EMBEDDING_DIM" in message
    assert isinstance(excinfo.value, VectorStoreError)
    client.delete_collection.assert_not_called()
    client.create_collection.assert_not_called()


@pytest.mark.asyncio
@patch("core.services.vectorstore.providers.qdrant_provider.AsyncQdrantClient")
async def test_matching_existing_collection_proceeds(mock_qdrant_client):
    client = _client(existing_size=384)
    mock_qdrant_client.return_value = client
    from core.services.vectorstore.providers.qdrant_provider import QdrantProvider

    await QdrantProvider().create_collection("documents", vector_size=384)

    client.create_collection.assert_not_called()
    client.create_payload_index.assert_called()


@pytest.mark.asyncio
async def test_pgvector_mismatch_raises_the_same_error_type():
    from core.services.vectorstore.providers import pgvector_provider

    with patch.object(
        pgvector_provider.PgVectorProvider,
        "_existing_dimension",
        AsyncMock(return_value=768),
    ):
        with pytest.raises(EmbeddingDimensionMismatchError):
            await pgvector_provider.PgVectorProvider()._assert_dimension("vs_d", 384)


@pytest.mark.asyncio
async def test_service_keeps_the_mismatch_type():
    from core.services.vectorstore.service import VectorStoreService

    provider = AsyncMock()
    provider.create_collection.side_effect = EmbeddingDimensionMismatchError("x")
    service = VectorStoreService(provider=provider)
    with pytest.raises(EmbeddingDimensionMismatchError):
        await service.create_collection()
