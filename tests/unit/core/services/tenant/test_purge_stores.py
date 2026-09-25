"""GDPR erasure reaches the vector store and the tenant's Redis keyspace."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.services.tenant import purge as purge_module
from core.services.tenant import purge_stores
from core.services.tenant.purge import TenantPurgeBlockedError, purge_tenant_data


def _vector_service(collections: list[str] | None = None) -> SimpleNamespace:
    provider = SimpleNamespace(delete_by_filter=AsyncMock())
    if collections is not None:
        provider.list_collections = AsyncMock(return_value=collections)
    return SimpleNamespace(
        provider=provider, config=SimpleNamespace(collection_name="documents")
    )


class _FakeRedis:
    def __init__(self, keys: list[str]) -> None:
        self.keys = keys
        self.patterns: list[str] = []
        self.deleted: list[str] = []
        self.aclose = AsyncMock()

    async def scan_iter(self, match: str, count: int):
        self.patterns.append(match)
        for key in self.keys:
            yield key

    async def delete(self, *keys: str) -> int:
        self.deleted.extend(keys)
        return len(keys)


@pytest.fixture
def redis_client():
    client = _FakeRedis(["cache:t1:response:a", "cache:t1:search:b"])
    with patch("core.cache.redis_cache.create_redis_client", return_value=client):
        yield client


async def test_vectors_deleted_by_tenant_in_every_collection(redis_client) -> None:
    service = _vector_service(["documents", "memories"])
    with patch(
        "core.services.vectorstore.service.get_vectorstore_service",
        return_value=service,
    ):
        outcome = await purge_stores.purge_tenant_stores("t1")

    assert outcome.vector_collections == ["documents", "memories"]
    calls = service.provider.delete_by_filter.await_args_list
    assert [c.kwargs for c in calls] == [
        {"collection_name": "documents", "key": "tenant_id", "value": "t1"},
        {"collection_name": "memories", "key": "tenant_id", "value": "t1"},
    ]
    assert outcome.errors == {}


async def test_falls_back_to_configured_collection(redis_client) -> None:
    service = _vector_service(None)
    with patch(
        "core.services.vectorstore.service.get_vectorstore_service",
        return_value=service,
    ):
        outcome = await purge_stores.purge_tenant_stores("t1")
    assert outcome.vector_collections == ["documents"]


async def test_no_collections_means_nothing_to_purge(redis_client) -> None:
    # A fresh deployment lists none: probing the configured default would hit
    # "collection not found" and block the whole erasure.
    service = _vector_service([])
    with patch(
        "core.services.vectorstore.service.get_vectorstore_service",
        return_value=service,
    ):
        outcome = await purge_stores.purge_tenant_stores("t1")
    assert outcome.vector_collections == []
    service.provider.delete_by_filter.assert_not_awaited()
    assert outcome.errors == {}


async def test_redis_keyspace_of_the_tenant_is_dropped(redis_client) -> None:
    with patch(
        "core.services.vectorstore.service.get_vectorstore_service",
        return_value=_vector_service([]),
    ):
        outcome = await purge_stores.purge_tenant_stores("t1")
    assert redis_client.patterns and redis_client.patterns[0].endswith(":t1:*")
    assert outcome.cache_keys_deleted == 2
    redis_client.aclose.assert_awaited_once()


async def test_glob_characters_in_tenant_are_escaped(redis_client) -> None:
    with patch(
        "core.services.vectorstore.service.get_vectorstore_service",
        return_value=_vector_service([]),
    ):
        await purge_stores.purge_tenant_stores("a*b")
    assert redis_client.patterns[0].endswith(":a\\*b:*")


async def test_unreachable_vector_store_is_reported(redis_client) -> None:
    service = _vector_service(["documents"])
    service.provider.delete_by_filter.side_effect = ConnectionError("down")
    with patch(
        "core.services.vectorstore.service.get_vectorstore_service",
        return_value=service,
    ):
        outcome = await purge_stores.purge_tenant_stores("t1")
    assert "vectorstore" in outcome.errors
    assert outcome.cache_keys_deleted == 2  # the cache purge still ran


def _empty_db():
    cursor = MagicMock()
    cursor.execute = AsyncMock()
    cursor.fetchall = AsyncMock(return_value=[])
    cursor.fetchone = AsyncMock(return_value=None)

    @asynccontextmanager
    async def factory(*_a, **_k):
        yield cursor

    return patch.object(purge_module, "get_async_cursor", factory)


async def test_purge_tenant_data_blocks_on_vector_failure() -> None:
    failed = purge_stores.TenantStoresPurge(errors={"vectorstore": "down"})
    with (
        _empty_db(),
        patch.object(
            purge_stores, "purge_tenant_stores", AsyncMock(return_value=failed)
        ),
        pytest.raises(TenantPurgeBlockedError) as info,
    ):
        await purge_tenant_data("t1")
    assert info.value.pending == ["vectorstore"]


async def test_purge_tenant_data_tolerates_cache_failure() -> None:
    partial = purge_stores.TenantStoresPurge(errors={"redis_cache": "down"})
    stores = AsyncMock(return_value=partial)
    with _empty_db(), patch.object(purge_stores, "purge_tenant_stores", stores):
        assert await purge_tenant_data("t1") == {}
    stores.assert_awaited_once_with("t1")


async def test_include_stores_false_skips_them() -> None:
    stores = AsyncMock()
    with _empty_db(), patch.object(purge_stores, "purge_tenant_stores", stores):
        await purge_tenant_data("t1", include_stores=False)
    stores.assert_not_awaited()
