"""Shared inference services: embedding, rerank and the scoped vector store.

Importing this package loads no model and opens no connection; ``torch`` is
only ever imported by the explicit ``local`` backends.
"""

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
