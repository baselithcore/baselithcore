"""Blocking façade over the async inference services.

Some plugins (vendored engines) are synchronous end to end. Calling
``asyncio.run`` per call would build a throw-away event loop — and a throw-away
``httpx`` pool — each time, and sharing the application's loop deadlocks when
the caller already runs on it. The bridge instead owns **one dedicated loop in
a daemon thread** and builds its own service instances on it, lazily and on
first use; callers on any thread block on ``run_coroutine_threadsafe``.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

from core.services.inference.embedding import EmbeddingService
from core.services.inference.rerank import RerankService
from core.services.inference.vectorstore import QdrantRuntime, ScopedVectorStore

T = TypeVar("T")

_STORE_METHODS = frozenset(
    {
        "list_collections",
        "collection_exists",
        "create_collection",
        "get_collection",
        "delete_collection",
        "create_payload_index",
        "upsert",
        "query_points",
        "search",
        "retrieve",
        "scroll",
        "count",
        "delete",
    }
)


class SyncScopedStore:
    """Sync view of a :class:`ScopedVectorStore` (same method names)."""

    def __init__(self, bridge: SyncInference, store: ScopedVectorStore) -> None:
        self._bridge = bridge
        self._store = store

    def collection_name(self, name: str) -> str:
        return self._store.collection_name(name)

    def __getattr__(self, attr: str) -> Callable[..., Any]:
        if attr not in _STORE_METHODS:
            raise AttributeError(attr)
        method = getattr(self._store, attr)

        def call(*args: Any, **kwargs: Any) -> Any:
            return self._bridge.run(method(*args, **kwargs))

        return call


class SyncInference:
    """Dedicated-loop bridge to embedding, rerank and the scoped store."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._embedding: EmbeddingService | None = None
        self._rerank: RerankService | None = None
        self._qdrant: QdrantRuntime | None = None

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop is None:
                loop = asyncio.new_event_loop()
                thread = threading.Thread(
                    target=loop.run_forever, name="baselith-inference", daemon=True
                )
                thread.start()
                self._loop, self._thread = loop, thread
            return self._loop

    def run(self, coro: Coroutine[Any, Any, T], timeout: float | None = None) -> T:
        """Run ``coro`` on the bridge loop and block for its result."""
        loop = self._ensure_loop()
        return asyncio.run_coroutine_threadsafe(coro, loop).result(timeout)

    async def _embedding_service(self) -> EmbeddingService:
        if self._embedding is None:
            self._embedding = EmbeddingService.from_config()
        return self._embedding

    async def _rerank_service(self) -> RerankService:
        if self._rerank is None:
            self._rerank = RerankService.from_config()
        return self._rerank

    async def _qdrant_runtime(self) -> QdrantRuntime:
        if self._qdrant is None:
            self._qdrant = QdrantRuntime.open()
        return self._qdrant

    def use_qdrant_runtime(self, runtime: QdrantRuntime | None) -> None:
        """Inject a runtime (tests: an in-memory one). ``None`` resets it."""
        self._qdrant = runtime

    @property
    def embedding_dim(self) -> int:
        from core.config.inference import get_embedding_config

        return get_embedding_config().dim

    @property
    def embedding_model(self) -> str:
        from core.config.inference import get_embedding_config

        return get_embedding_config().model

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        async def go() -> list[list[float]]:
            return await (await self._embedding_service()).embed_documents(texts)

        return self.run(go())

    def embed_query(self, text: str) -> list[float]:
        async def go() -> list[float]:
            return await (await self._embedding_service()).embed_query(text)

        return self.run(go())

    def rerank(
        self, query: str, texts: list[str], top_k: int
    ) -> list[tuple[int, float]]:
        async def go() -> list[tuple[int, float]]:
            return await (await self._rerank_service()).rerank(query, texts, top_k)

        return self.run(go())

    def store(self, plugin: str, tenant: str) -> SyncScopedStore:
        """Sync scoped store for ``tenant`` + ``plugin``."""

        async def go() -> ScopedVectorStore:
            return (await self._qdrant_runtime()).scoped(plugin, tenant)

        return SyncScopedStore(self, self.run(go()))

    def close(self) -> None:
        """Close services and stop the loop; safe to call twice."""
        with self._lock:
            loop, thread = self._loop, self._thread
            self._loop = self._thread = None
        if loop is None:
            return

        async def shut() -> None:
            for svc in (self._embedding, self._rerank, self._qdrant):
                if svc is not None:
                    await svc.shutdown()

        try:
            asyncio.run_coroutine_threadsafe(shut(), loop).result(10)
        finally:
            self._embedding = self._rerank = self._qdrant = None
            loop.call_soon_threadsafe(loop.stop)
            if thread is not None:
                thread.join(timeout=5)


_bridge: SyncInference | None = None
_bridge_lock = threading.Lock()


def get_sync_inference() -> SyncInference:
    """Process-wide bridge (created on first call, never at import)."""
    global _bridge
    with _bridge_lock:
        if _bridge is None:
            _bridge = SyncInference()
        return _bridge


def shutdown_sync_inference() -> None:
    """Close the process-wide bridge (lifespan shutdown)."""
    global _bridge
    with _bridge_lock:
        bridge, _bridge = _bridge, None
    if bridge is not None:
        bridge.close()
