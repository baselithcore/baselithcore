"""Shared Qdrant runtime and the tenant-scoped store plugins use.

One ``AsyncQdrantClient`` per process, **server mode only**. ``path=``
(embedded) is refused; tests use ``":memory:"`` through ``allow_memory=True``.

Plugins never see the raw client: they get a :class:`ScopedVectorStore` bound
to ``(tenant, plugin)``. Every method takes a *logical* collection name and
maps it to ``<tenant>.<plugin>.<name>`` itself, so a plugin cannot address
another tenant's (or another plugin's) data.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from qdrant_client import AsyncQdrantClient

from core.config.inference import QdrantServerConfig, get_qdrant_server_config
from core.services.inference.errors import InferenceConfigError
from core.services.inference.scope import scope_prefix, scoped_name

if TYPE_CHECKING:
    from core.plugins.interface import Plugin

MEMORY_LOCATION = ":memory:"


class QdrantRuntime:
    """Owns the single async Qdrant client."""

    def __init__(self, client: AsyncQdrantClient) -> None:
        self._client = client

    @classmethod
    def open(
        cls, config: QdrantServerConfig | None = None, *, allow_memory: bool = False
    ) -> QdrantRuntime:
        """Connect to the Qdrant server named by ``config``."""
        cfg = config or get_qdrant_server_config()
        if cfg.url is None:
            raise InferenceConfigError(
                "BASELITH_QDRANT_URL is required: the core vector store talks to "
                "a Qdrant server (embedded path= mode is not supported)."
            )
        if cfg.url == MEMORY_LOCATION:
            if not allow_memory:
                raise InferenceConfigError(
                    "':memory:' Qdrant is for tests only; point "
                    "BASELITH_QDRANT_URL at a server."
                )
            return cls(AsyncQdrantClient(location=MEMORY_LOCATION))
        if not cfg.url.startswith(("http://", "https://")):
            raise InferenceConfigError(
                f"BASELITH_QDRANT_URL must be http(s)://..., got {cfg.url!r}"
            )
        return cls(
            AsyncQdrantClient(
                url=cfg.url,
                api_key=cfg.api_key.get_secret_value() if cfg.api_key else None,
                timeout=int(cfg.timeout),
                prefer_grpc=cfg.prefer_grpc,
                grpc_port=cfg.grpc_port,
            )
        )

    def scoped(self, plugin: str, tenant: str) -> ScopedVectorStore:
        """Store bound to ``tenant`` + ``plugin``."""
        return ScopedVectorStore(self._client, plugin=plugin, tenant=tenant)

    def for_plugin(self, plugin: Plugin) -> ScopedVectorStore:
        """Store scoped by the plugin's own ``tenant_key()`` (identity-derived)."""
        return self.scoped(plugin.metadata.name, plugin.tenant_key())

    async def shutdown(self) -> None:
        """Close the client (called by the lazy registry at shutdown)."""
        await self._client.close()


class ScopedVectorStore:
    """Tenant+plugin scoped façade over the shared Qdrant client."""

    def __init__(self, client: AsyncQdrantClient, *, plugin: str, tenant: str) -> None:
        self._client = client
        self._plugin = plugin
        self._tenant = tenant
        self._prefix = scope_prefix(tenant, plugin)  # validates both

    def collection_name(self, name: str) -> str:
        """Physical name of the logical collection ``name`` (for diagnostics)."""
        return scoped_name(self._tenant, self._plugin, name)

    def _c(self, name: str) -> str:
        return scoped_name(self._tenant, self._plugin, name)

    async def list_collections(self) -> list[str]:
        """Logical names of this scope's collections only."""
        resp = await self._client.get_collections()
        return [
            c.name[len(self._prefix) :]
            for c in resp.collections
            if c.name.startswith(self._prefix)
        ]

    async def collection_exists(self, name: str) -> bool:
        return await self._client.collection_exists(self._c(name))

    async def create_collection(self, name: str, **kwargs: Any) -> bool:
        """Create a collection (``vectors_config``, ``sparse_vectors_config``...)."""
        return await self._client.create_collection(self._c(name), **kwargs)

    async def get_collection(self, name: str) -> Any:
        return await self._client.get_collection(self._c(name))

    async def delete_collection(self, name: str) -> bool:
        return await self._client.delete_collection(self._c(name))

    async def create_payload_index(self, name: str, **kwargs: Any) -> Any:
        return await self._client.create_payload_index(self._c(name), **kwargs)

    async def upsert(self, name: str, points: Any, **kwargs: Any) -> Any:
        return await self._client.upsert(self._c(name), points=points, **kwargs)

    async def query_points(self, name: str, **kwargs: Any) -> Any:
        """Query (dense, sparse, multivector, prefetch/fusion...)."""
        return await self._client.query_points(self._c(name), **kwargs)

    async def search(
        self, name: str, vector: list[float], *, limit: int = 10, **kwargs: Any
    ) -> list[Any]:
        """Dense nearest-neighbour search."""
        resp = await self._client.query_points(
            self._c(name), query=vector, limit=limit, **kwargs
        )
        return list(resp.points)

    async def retrieve(self, name: str, ids: list[Any], **kwargs: Any) -> list[Any]:
        return await self._client.retrieve(self._c(name), ids=ids, **kwargs)

    async def scroll(self, name: str, **kwargs: Any) -> Any:
        return await self._client.scroll(self._c(name), **kwargs)

    async def count(self, name: str, **kwargs: Any) -> Any:
        return await self._client.count(self._c(name), **kwargs)

    async def delete(self, name: str, points_selector: Any, **kwargs: Any) -> Any:
        """Delete points by id list or filter selector."""
        return await self._client.delete(
            self._c(name), points_selector=points_selector, **kwargs
        )
