"""
Qdrant provider implementation.
"""

from core.observability.logging import get_logger
from typing import List, Dict, Any, Sequence

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance,
    VectorParams,
    PointStruct,
    Filter,
    FieldCondition,
    MatchValue,
    HasIdCondition,
)

from core.services.vectorstore.exceptions import VectorStoreError
from core.resilience.circuit_breaker import get_circuit_breaker
from core.resilience.retry import retry

logger = get_logger(__name__)


class QdrantProvider:
    """Qdrant vector store provider."""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 6333,
        grpc_port: int | None = None,
        prefer_grpc: bool = False,
        mode: str = "server",
        path: str | None = None,
    ):
        """
        Initialize Qdrant provider.

        Args:
            host: Qdrant host
            port: Qdrant HTTP port
            grpc_port: Qdrant gRPC port (optional)
            prefer_grpc: Whether to prefer gRPC over HTTP
            mode: Operation mode ('server' or 'embedded')
            path: Persistence path for embedded mode (default: ":memory:")
        """
        self.host = host
        self.port = port
        self.grpc_port = grpc_port
        self.prefer_grpc = prefer_grpc
        self.mode = mode
        self.path = path

        try:
            if mode == "embedded":
                location = path if path else ":memory:"
                self.client = AsyncQdrantClient(location=location)
                logger.info(
                    f"Initialized Qdrant provider in embedded mode (location={location})"
                )
            else:
                self.client = AsyncQdrantClient(
                    host=host,
                    port=port,
                    grpc_port=grpc_port,  # type: ignore[arg-type]
                    prefer_grpc=prefer_grpc,
                )
                logger.info(f"Initialized Qdrant provider at {host}:{port}")
        except Exception as e:
            logger.error(f"Failed to initialize Qdrant client: {e}")
            raise VectorStoreError(f"Qdrant initialization failed: {e}") from e

    @get_circuit_breaker("vectorstore")
    @retry(max_attempts=3, exponential_base=2.0)
    async def create_collection(
        self, collection_name: str, vector_size: int, **kwargs
    ) -> None:
        """
        Create a Qdrant collection.

        Args:
            collection_name: Name of the collection
            vector_size: Dimension of vectors
            **kwargs: Additional parameters (distance, on_disk_payload, etc.)
        """
        try:
            # Check if collection already exists to avoid 409 Conflict
            if await self.collection_exists(collection_name):
                logger.info(f"Collection '{collection_name}' already exists.")
                return

            distance = kwargs.get("distance", Distance.COSINE)
            on_disk_payload = kwargs.get("on_disk_payload", True)

            await self.client.create_collection(
                collection_name=collection_name,
                vectors_config=VectorParams(size=vector_size, distance=distance),
                on_disk_payload=on_disk_payload,
            )
            logger.info(
                f"Created collection '{collection_name}' with size {vector_size}"
            )
        except Exception as e:
            # Handle possible race condition
            if "already exists" in str(e).lower() or "409" in str(e):
                logger.info(
                    f"Collection '{collection_name}' already exists (handled conflict)."
                )
                return
            logger.error(f"Failed to create collection '{collection_name}': {e}")
            raise VectorStoreError(f"Collection creation failed: {e}") from e

    @get_circuit_breaker("vectorstore")
    @retry(max_attempts=3, exponential_base=2.0)
    async def upsert(
        self, collection_name: str, points: List[Dict[str, Any]], **kwargs
    ) -> None:
        """
        Upsert points into Qdrant collection.

        Args:
            collection_name: Name of the collection
            points: List of dicts with 'id', 'vector', 'payload'
            **kwargs: Additional parameters (wait, batch_size, etc.)
        """
        try:
            wait = kwargs.get("wait", True)

            # Convert to PointStruct
            qdrant_points = [
                PointStruct(
                    id=point["id"],
                    vector=point["vector"],
                    payload=point.get("payload", {}),
                )
                for point in points
            ]

            await self.client.upsert(
                collection_name=collection_name,
                points=qdrant_points,
                wait=wait,
            )
            logger.debug(f"Upserted {len(points)} points to '{collection_name}'")
        except Exception as e:
            logger.error(f"Failed to upsert points to '{collection_name}': {e}")
            raise VectorStoreError(f"Upsert failed: {e}") from e

    @get_circuit_breaker("vectorstore")
    @retry(max_attempts=3, exponential_base=2.0)
    async def search(
        self,
        collection_name: str,
        query_vector: Sequence[float],
        limit: int = 10,
        **kwargs,
    ) -> List[Any]:
        """
        Search for similar vectors in Qdrant.

        Args:
            collection_name: Name of the collection
            query_vector: Query vector
            limit: Maximum number of results
            **kwargs: Additional parameters (score_threshold, filter, etc.)

        Returns:
            List of search results (ScoredPoint objects)
        """
        try:
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

            response = await self.client.query_points(
                collection_name=collection_name,
                query=list(query_vector),
                limit=limit,
                **kwargs,
            )
            logger.debug(
                f"Search in '{collection_name}' returned {len(response.points)} results"
            )
            return response.points
        except Exception as e:
            logger.error(f"Search in '{collection_name}' failed: {e}")
            raise VectorStoreError(f"Search failed: {e}") from e

    @get_circuit_breaker("vectorstore")
    @retry(max_attempts=3, exponential_base=2.0)
    async def retrieve(
        self, collection_name: str, point_ids: List[int | str], **kwargs
    ) -> List[Any]:
        """
        Retrieve points by ID from Qdrant.

        Args:
            collection_name: Name of the collection
            point_ids: List of point IDs to retrieve
            **kwargs: Additional parameters

        Returns:
            List of points (Record objects)
        """
        try:
            tenant_id = kwargs.pop("tenant_id", None)

            if tenant_id:
                scroll_filter = Filter(
                    must=[
                        HasIdCondition(has_id=point_ids),
                        FieldCondition(
                            key="tenant_id", match=MatchValue(value=tenant_id)
                        ),
                    ]
                )
                response_scroll, _ = await self.client.scroll(
                    collection_name=collection_name,
                    scroll_filter=scroll_filter,
                    limit=len(point_ids),
                    **kwargs,
                )
                return response_scroll

            response = await self.client.retrieve(
                collection_name=collection_name,
                ids=point_ids,
                **kwargs,
            )
            return response
        except Exception as e:
            logger.error(f"Retrieve from '{collection_name}' failed: {e}")
            raise VectorStoreError(f"Retrieve failed: {e}") from e

    @get_circuit_breaker("vectorstore")
    @retry(max_attempts=3, exponential_base=2.0)
    async def delete(
        self, collection_name: str, point_ids: List[int | str], **kwargs
    ) -> None:
        """
        Delete points from Qdrant collection.

        Args:
            collection_name: Name of the collection
            point_ids: List of point IDs to delete
            **kwargs: Additional parameters (wait, etc.)
        """
        try:
            wait = kwargs.get("wait", True)

            await self.client.delete(
                collection_name=collection_name,
                points_selector=point_ids,
                wait=wait,
            )
            logger.debug(f"Deleted {len(point_ids)} points from '{collection_name}'")
        except Exception as e:
            logger.error(f"Failed to delete points from '{collection_name}': {e}")
            raise VectorStoreError(f"Delete failed: {e}") from e

    async def collection_exists(self, collection_name: str) -> bool:
        """Check if collection exists."""
        try:
            collections = await self.client.get_collections()
            return any(c.name == collection_name for c in collections.collections)
        except Exception as e:
            logger.error(f"Failed to check collection existence: {e}")
            return False

    @get_circuit_breaker("vectorstore")
    @retry(max_attempts=3, exponential_base=2.0)
    async def scroll(
        self,
        collection_name: str,
        limit: int = 100,
        offset: int | str | None = None,
        **kwargs,
    ):
        """
        Scroll through collection points.

        Args:
            collection_name: Name of the collection
            limit: Number of points to return
            offset: Offset for pagination
            **kwargs: Additional parameters

        Returns:
            Scroll response with points and next offset
        """
        try:
            tenant_id = kwargs.pop("tenant_id", None)
            scroll_filter = kwargs.pop("scroll_filter", kwargs.pop("filter", None))

            if tenant_id:
                tenant_condition = FieldCondition(
                    key="tenant_id", match=MatchValue(value=tenant_id)
                )
                if scroll_filter and isinstance(scroll_filter, Filter):
                    if scroll_filter.must:
                        if isinstance(scroll_filter.must, list):
                            scroll_filter.must.append(tenant_condition)
                        else:
                            scroll_filter.must = [scroll_filter.must, tenant_condition]
                    else:
                        scroll_filter.must = [tenant_condition]
                else:
                    scroll_filter = Filter(must=[tenant_condition])

            if scroll_filter:
                kwargs["scroll_filter"] = scroll_filter

            return await self.client.scroll(
                collection_name=collection_name, limit=limit, offset=offset, **kwargs
            )
        except Exception as e:
            logger.error(f"Scroll in '{collection_name}' failed: {e}")
            raise VectorStoreError(f"Scroll failed: {e}") from e

    async def delete_by_filter(
        self, collection_name: str, key: str, value: Any, **kwargs
    ) -> None:
        """
        Delete points by filtering on a payload field.

        Args:
            collection_name: Name of the collection
            key: Payload field key (e.g., "document_id")
            value: Value to match
            **kwargs: Additional parameters (wait, etc.)
        """
        try:
            wait = kwargs.pop("wait", True)
            tenant_id = kwargs.pop("tenant_id", None)

            must_conditions: list[Any] = [
                FieldCondition(
                    key=key,
                    match=MatchValue(value=value),
                )
            ]

            if tenant_id:
                must_conditions.append(
                    FieldCondition(
                        key="tenant_id",
                        match=MatchValue(value=tenant_id),
                    )
                )

            filter_condition = Filter(must=must_conditions)

            await self.client.delete(
                collection_name=collection_name,
                points_selector=filter_condition,
                wait=wait,
            )
            logger.debug(f"Deleted points from '{collection_name}' where {key}={value}")
        except Exception as e:
            logger.error(
                f"Failed to delete points from '{collection_name}' with filter {key}={value}: {e}"
            )
            raise VectorStoreError(f"Delete by filter failed: {e}") from e
