"""Tenant erasure outside the relational tables — vectors and shared caches.

:func:`core.services.tenant.purge.purge_tenant_data` deletes rows from every
table carrying a ``tenant_id`` column. Two stores hold tenant data without one:

* **Vector store** — every indexed chunk carries ``tenant_id`` in its payload
  (a pgvector ``vs_*`` table's JSONB column, or a Qdrant point payload). They
  are removed with the provider's ``delete_by_filter(key="tenant_id")`` across
  every collection the provider lists.
* **Redis caches** — every tenant-scoped cache writes under
  ``{CACHE_REDIS_PREFIX}:{tenant}:…`` (chat response/pre-check/rerank/history
  caches, the vector search cache, the embedding/learner caches built on
  ``core.optimization.caching.RedisCache``). The whole keyspace is dropped.

In-process caches (the ``local`` cache backend, the semantic LLM cache) live
in each worker's memory; no single process can reach the others, so they are
left to expire on their TTLs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.db.connection import system_tenant_scope
from core.observability.logging import get_logger

logger = get_logger(__name__)

__all__ = ["TenantStoresPurge", "purge_tenant_stores"]

_SCAN_BATCH = 500
_GLOB_SPECIAL = str.maketrans({c: f"\\{c}" for c in "*?[]\\"})


@dataclass
class TenantStoresPurge:
    """Outcome of :func:`purge_tenant_stores`.

    Attributes:
        vector_collections: Collections the tenant's points were deleted from.
        cache_keys_deleted: Redis keys removed from the tenant's keyspace.
        errors: ``{store: message}`` for every store that could not be purged.
            A ``vectorstore`` entry means tenant vectors may remain.
    """

    vector_collections: list[str] = field(default_factory=list)
    cache_keys_deleted: int = 0
    errors: dict[str, str] = field(default_factory=dict)


async def _collections(service: Any) -> list[str]:
    """Collections to purge: every one the provider lists.

    A provider that can list and lists none has nothing to erase — falling
    back to the configured default there would try a collection that does not
    exist and block the erasure on a fresh deployment. Only a provider with no
    ``list_collections`` falls back to the configured default.
    """
    lister = getattr(service.provider, "list_collections", None)
    if lister is None:
        return [service.config.collection_name]
    return list(await lister())


async def _purge_vectors(tenant_id: str, outcome: TenantStoresPurge) -> None:
    try:
        from core.services.vectorstore.service import get_vectorstore_service

        service = get_vectorstore_service()
        delete = getattr(service.provider, "delete_by_filter", None)
        if delete is None:
            outcome.errors["vectorstore"] = "provider lacks delete_by_filter"
            return
        with system_tenant_scope():
            for name in await _collections(service):
                await delete(collection_name=name, key="tenant_id", value=tenant_id)
                outcome.vector_collections.append(name)
    except Exception as exc:
        outcome.errors["vectorstore"] = str(exc)
        logger.error("Tenant %s vector purge failed: %s", tenant_id, exc)


async def _purge_redis(tenant_id: str, outcome: TenantStoresPurge) -> None:
    client = None
    try:
        from core.cache.redis_cache import create_redis_client
        from core.config import get_storage_config

        cfg = get_storage_config()
        client = create_redis_client(cfg.cache_redis_url)
        prefix = cfg.cache_redis_prefix.rstrip(":")
        pattern = f"{prefix}:{tenant_id.translate(_GLOB_SPECIAL)}:*"
        batch: list[Any] = []
        async for key in client.scan_iter(match=pattern, count=_SCAN_BATCH):
            batch.append(key)
            if len(batch) >= _SCAN_BATCH:
                outcome.cache_keys_deleted += int(await client.delete(*batch))
                batch.clear()
        if batch:
            outcome.cache_keys_deleted += int(await client.delete(*batch))
    except Exception as exc:
        outcome.errors["redis_cache"] = str(exc)
        logger.warning("Tenant %s cache purge failed: %s", tenant_id, exc)
    finally:
        if client is not None:
            try:
                await client.aclose()
            except Exception:  # silent-ok: closing a pool must not fail erasure
                pass


async def purge_tenant_stores(tenant_id: str) -> TenantStoresPurge:
    """Delete a tenant's vector points and shared (Redis) cache entries.

    Idempotent. Failures are collected per store rather than raised, so one
    unreachable store never hides what the other did; the caller decides
    whether a failure blocks the erasure (``purge_tenant_data`` does, for the
    vector store).

    Args:
        tenant_id: The tenant being erased.

    Returns:
        What was purged, and the per-store errors.
    """
    outcome = TenantStoresPurge()
    await _purge_vectors(tenant_id, outcome)
    await _purge_redis(tenant_id, outcome)
    return outcome
