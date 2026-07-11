"""Raw and grouped Qdrant queries with tenant-filter enforcement.

Bodies of ``QdrantProvider.query_points`` / ``query_points_groups``,
extracted for the module size cap. The tenant-isolation merge (append the
``tenant_id`` condition into any caller-supplied filter, never replace it)
is shared by both and unchanged in behavior; resilience decorators stay on
the provider's thin delegating methods.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from qdrant_client.models import FieldCondition, Filter, MatchValue

from core.observability.logging import get_logger
from core.services.vectorstore.exceptions import VectorStoreError

if TYPE_CHECKING:
    from core.services.vectorstore.providers.qdrant_provider import QdrantProvider

logger = get_logger(__name__)


def merge_tenant_filter(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Fold ``tenant_id`` into ``query_filter`` (isolation is never optional).

    Mutates and returns *kwargs*: pops ``tenant_id``/``filter`` aliases and
    installs the merged ``query_filter``.
    """
    tenant_id = kwargs.pop("tenant_id", None)
    query_filter = kwargs.pop("query_filter", kwargs.pop("filter", None))

    if tenant_id:
        tenant_condition = FieldCondition(
            key="tenant_id", match=MatchValue(value=tenant_id)
        )
        if query_filter and isinstance(query_filter, Filter):
            if query_filter.must:
                if isinstance(query_filter.must, list):
                    query_filter.must.append(tenant_condition)
                else:
                    query_filter.must = [query_filter.must, tenant_condition]
            else:
                query_filter.must = [tenant_condition]
        else:
            query_filter = Filter(must=[tenant_condition])

    if query_filter:
        kwargs["query_filter"] = query_filter
    return kwargs


async def query_points_impl(
    provider: QdrantProvider,
    collection_name: str,
    query_vector: Sequence[float],
    limit: int,
    **kwargs: Any,
) -> Any:
    """See ``QdrantProvider.query_points``."""
    try:
        kwargs = merge_tenant_filter(kwargs)
        return await provider.client.query_points(
            collection_name=collection_name,
            query=list(query_vector),
            limit=limit,
            **kwargs,
        )
    except Exception as e:
        logger.error(f"Raw query_points in '{collection_name}' failed: {e}")
        raise VectorStoreError(f"Query points failed: {e}") from e


async def query_points_groups_impl(
    provider: QdrantProvider,
    collection_name: str,
    query_vector: Sequence[float],
    group_by: str,
    limit: int,
    group_size: int,
    **kwargs: Any,
) -> Any:
    """See ``QdrantProvider.query_points_groups``."""
    try:
        kwargs = merge_tenant_filter(kwargs)
        return await provider.client.query_points_groups(
            collection_name=collection_name,
            query=list(query_vector),
            group_by=group_by,
            limit=limit,
            group_size=group_size,
            **kwargs,
        )
    except Exception as e:
        logger.error(f"Grouped query in '{collection_name}' failed: {e}")
        raise VectorStoreError(f"Query points groups failed: {e}") from e


__all__ = ["merge_tenant_filter", "query_points_groups_impl", "query_points_impl"]
