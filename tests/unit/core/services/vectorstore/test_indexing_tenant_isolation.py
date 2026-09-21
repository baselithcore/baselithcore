"""Caller metadata cannot claim another tenant, or shadow a retrieval key.

The write side used to merge caller metadata *over* the payload the pipeline
builds (``payload.update(metadata)``), so a ``Document`` whose metadata carried
``tenant_id`` named whatever tenant it liked. Every isolation check downstream
reads back this same stored payload — the pgvector ``payload @>`` predicate and
the Qdrant field condition both do — so one poisoned write was readable by the
tenant it named. The query side had the mirror hole: ``kwargs.setdefault`` on
the raw query helpers let a caller-supplied ``tenant_id`` survive.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest

from core.context import reset_tenant_context, set_tenant_context
from core.models.domain import Document
from core.services.vectorstore import VectorStoreService
from core.services.vectorstore._indexing import RESERVED_PAYLOAD_KEYS


def _one_vector_per_text(_embedder, texts, _cache, **_kwargs):
    """One vector per input, as the real cached-embedding helper guarantees."""
    return [[0.1, 0.2] for _ in texts]


@pytest.fixture(autouse=True)
def mock_redis_cache():
    with patch("core.services.vectorstore.service.RedisCache") as mock:
        mock.return_value.get = AsyncMock(return_value=None)
        mock.return_value.set = AsyncMock()
        mock.return_value.delete = AsyncMock()
        yield mock


@pytest.fixture
def tenant() -> Any:
    """Run inside a known tenant context and restore it afterwards."""
    token = set_tenant_context("tenant-a")
    yield "tenant-a"
    reset_tenant_context(token)


def _service(provider: Any) -> VectorStoreService:
    config = Mock(
        provider="qdrant",
        collection_name="test",
        embedding_dim=384,
        embedding_model="test-model",
        search_limit=10,
    )
    return VectorStoreService(config=config, provider=provider)


@pytest.mark.asyncio
@patch("core.services.vectorstore._indexing.get_embeddings_cached")
async def test_metadata_cannot_claim_another_tenant(
    mock_get_embeddings, tenant: str
) -> None:
    mock_get_embeddings.side_effect = _one_vector_per_text
    provider = AsyncMock()
    service = _service(provider)

    await service.index(
        [
            Document(
                id="doc1",
                content="body",
                metadata={"tenant_id": "tenant-b", "source": "spoofed"},
            )
        ],
        embedder=Mock(),
    )

    points = provider.upsert.call_args.kwargs["points"]
    assert points
    for point in points:
        assert point["payload"]["tenant_id"] == tenant
        assert point["payload"]["source"] != "spoofed"


@pytest.mark.asyncio
@patch("core.services.vectorstore._indexing.get_embeddings_cached")
async def test_metadata_cannot_shadow_retrieval_keys(
    mock_get_embeddings, tenant: str
) -> None:
    """``text``/``document_id``/``chunk_index`` drive retrieval and dedup."""
    mock_get_embeddings.side_effect = _one_vector_per_text
    provider = AsyncMock()
    service = _service(provider)

    poisoned = {key: "poisoned" for key in RESERVED_PAYLOAD_KEYS}
    await service.index(
        [Document(id="doc1", content="real body", metadata=poisoned)],
        embedder=Mock(),
    )

    payload = provider.upsert.call_args.kwargs["points"][0]["payload"]
    assert payload["text"] == "real body"
    assert payload["document_id"] == "doc1"
    assert payload["chunk_index"] == 0
    assert "poisoned" not in payload.values()


@pytest.mark.asyncio
@patch("core.services.vectorstore._indexing.get_embeddings_cached")
async def test_unreserved_metadata_still_rides_along(
    mock_get_embeddings, tenant: str
) -> None:
    """The fix narrows what a caller may set, it does not drop metadata."""
    mock_get_embeddings.side_effect = _one_vector_per_text
    provider = AsyncMock()
    service = _service(provider)

    await service.index(
        [Document(id="doc1", content="body", metadata={"author": "gio", "page": 3})],
        embedder=Mock(),
    )

    payload = provider.upsert.call_args.kwargs["points"][0]["payload"]
    assert payload["author"] == "gio"
    assert payload["page"] == 3


@pytest.mark.asyncio
async def test_raw_query_ignores_a_caller_supplied_tenant(tenant: str) -> None:
    provider = AsyncMock()
    service = _service(provider)

    await service.query_points([0.1, 0.2], tenant_id="tenant-b")

    assert provider.query_points.call_args.kwargs["tenant_id"] == tenant


@pytest.mark.asyncio
async def test_grouped_query_ignores_a_caller_supplied_tenant(tenant: str) -> None:
    provider = AsyncMock()
    service = _service(provider)

    await service.query_points_groups(
        [0.1, 0.2], group_by="document_id", tenant_id="tenant-b"
    )

    assert provider.query_points_groups.call_args.kwargs["tenant_id"] == tenant
