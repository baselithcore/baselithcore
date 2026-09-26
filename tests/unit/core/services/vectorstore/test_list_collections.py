"""Both vector providers can enumerate their collections (used by tenant purge)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from core.services.vectorstore.providers.pgvector_provider import PgVectorProvider


async def test_pgvector_lists_vs_tables_without_prefix() -> None:
    cursor = MagicMock()
    cursor.execute = AsyncMock()
    cursor.fetchall = AsyncMock(return_value=[("vs_memories",), ("vs_documents",)])

    @asynccontextmanager
    async def factory(*_a, **_k):
        yield cursor

    with patch(
        "core.services.vectorstore.providers.pgvector_provider.get_async_cursor",
        factory,
    ):
        assert await PgVectorProvider().list_collections() == [
            "documents",
            "memories",
        ]
    sql = cursor.execute.await_args.args[0]
    assert "information_schema.tables" in sql and "'vs_'" in sql


@patch("core.services.vectorstore.providers.qdrant_provider.AsyncQdrantClient")
async def test_qdrant_lists_collection_names(mock_client_cls) -> None:
    client = AsyncMock()
    client.get_collections.return_value = SimpleNamespace(
        collections=[SimpleNamespace(name="a"), SimpleNamespace(name="b")]
    )
    mock_client_cls.return_value = client
    from core.services.vectorstore.providers.qdrant_provider import QdrantProvider

    assert await QdrantProvider().list_collections() == ["a", "b"]


async def test_service_aclose_closes_provider_client() -> None:
    from core.services.vectorstore.service import VectorStoreService

    client = MagicMock()
    client.close = AsyncMock()
    provider = SimpleNamespace(client=client)
    service = VectorStoreService(config=MagicMock(), provider=provider)
    await service.aclose()
    client.close.assert_awaited_once()


async def test_service_aclose_without_client_is_noop() -> None:
    from core.services.vectorstore.service import VectorStoreService

    service = VectorStoreService(config=MagicMock(), provider=SimpleNamespace())
    await service.aclose()


async def test_close_vectorstore_service_drops_singleton(monkeypatch) -> None:
    from core.services.vectorstore import service as vs_module

    fake = MagicMock()
    fake.aclose = AsyncMock()
    monkeypatch.setattr(vs_module, "_vectorstore_service", fake)
    await vs_module.close_vectorstore_service()
    fake.aclose.assert_awaited_once()
    assert vs_module._vectorstore_service is None
    await vs_module.close_vectorstore_service()  # idempotent
