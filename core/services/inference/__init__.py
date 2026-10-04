"""Shared inference services: embedding, rerank and the scoped vector store.

Importing this package loads no model, opens no connection and imports no
optional SDK: ``torch`` is only ever imported by the explicit ``local``
backends, and ``qdrant_client`` (the ``[qdrant]`` extra) only when a
:class:`QdrantRuntime` is opened. Names resolve on first access
(:pep:`562`, :mod:`core._lazy`), so ``from core.services.inference import
shutdown_sync_inference`` works on a pgvector deployment without the extra.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core._lazy import lazy_exports

if TYPE_CHECKING:  # pragma: no cover - the eager view, for type checkers
    from core.services.inference.embedding import EmbeddingService
    from core.services.inference.errors import (
        InferenceConfigError,
        InferenceError,
        TenantScopeError,
    )
    from core.services.inference.rerank import RerankService
    from core.services.inference.sync_bridge import (
        SyncInference,
        SyncScopedStore,
        get_sync_inference,
        shutdown_sync_inference,
    )
    from core.services.inference.vectorstore import QdrantRuntime, ScopedVectorStore

_EXPORTS = {
    "EmbeddingService": "embedding",
    "InferenceConfigError": "errors",
    "InferenceError": "errors",
    "QdrantRuntime": "vectorstore",
    "RerankService": "rerank",
    "ScopedVectorStore": "vectorstore",
    "SyncInference": "sync_bridge",
    "SyncScopedStore": "sync_bridge",
    "TenantScopeError": "errors",
    "get_sync_inference": "sync_bridge",
    "shutdown_sync_inference": "sync_bridge",
}

__getattr__ = lazy_exports(__name__, _EXPORTS)

__all__ = [
    "EmbeddingService",
    "InferenceConfigError",
    "InferenceError",
    "QdrantRuntime",
    "RerankService",
    "ScopedVectorStore",
    "SyncInference",
    "SyncScopedStore",
    "TenantScopeError",
    "get_sync_inference",
    "shutdown_sync_inference",
]
