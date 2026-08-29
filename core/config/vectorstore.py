"""Vector store configuration (Qdrant / pgvector).

Extracted from ``core.config.services`` for the module size cap; that module
re-exports everything here, so existing ``from core.config import
get_vectorstore_config`` imports are unchanged.
"""

from __future__ import annotations

import logging
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class VectorStoreConfig(BaseSettings):
    """
    Configuration for semantic database and indexing.

    BaselithCore primarily uses Qdrant for high-performance vector operations.
    """

    model_config = SettingsConfigDict(
        env_prefix="VECTORSTORE_",
        case_sensitive=False,
        extra="ignore",
    )

    provider: Literal["qdrant", "pgvector"] = Field(
        default="qdrant",
        description="Vector store provider: 'qdrant' (dedicated vector DB) or "
        "'pgvector' (PostgreSQL + vector extension, reuses the shared pool).",
    )

    # The default logical container for vector embeddings.
    collection_name: str = Field(
        default="documents", description="Collection name for documents"
    )

    host: str = Field(
        default="localhost",
        validation_alias=AliasChoices("VECTORSTORE_HOST", "VECTORSTORE_QDRANT_HOST"),
        description="Vector store server host",
    )
    port: int = Field(default=6333, description="Vector store HTTP/REST port")
    grpc_port: int = Field(default=6334, description="Vector store gRPC port")

    # == Embedding Settings ==
    # Model used to convert text into numerical vectors.
    embedding_model: str = Field(
        default="sentence-transformers/all-MiniLM-L6-v2",
        description="Embedding model name",
    )

    # Dimension size of the vectors produced by the model.
    embedding_dim: int = Field(default=384, description="Embedding dimension")

    # Embeddings are deterministic per model, so a long TTL is safe; the TTL
    # exists to bound Redis memory, not to refresh values.
    embedding_cache_ttl: int = Field(
        default=7 * 24 * 3600,
        alias="EMBEDDING_CACHE_TTL",
        description="Embedding cache TTL in seconds (default 7 days)",
    )

    # == Search Settings ==
    # Number of documents to return by default in vector searches.
    search_limit: int = Field(
        default=10, description="Default number of search results"
    )

    # Qdrant deployment mode: 'server' for cluster/docker, 'local' for in-memory/disk.
    qdrant_mode: str = Field(default="server", alias="QDRANT_MODE")
    qdrant_path: str | None = Field(default=None, alias="QDRANT_PATH")

    # Managed/remote Qdrant: API key + TLS. Both unset for the loopback
    # compose default; a remote instance without them would send unauthenticated
    # plaintext traffic.
    qdrant_api_key: SecretStr | None = Field(
        default=None,
        alias="QDRANT_API_KEY",
        description="API key for managed/remote Qdrant (unset for local)",
    )
    qdrant_https: bool = Field(
        default=False,
        alias="QDRANT_HTTPS",
        description="Use TLS for the Qdrant REST endpoint",
    )
    # Deadline on every Qdrant request: a hung server must fail fast into the
    # retry/breaker wrappers instead of stalling callers indefinitely.
    request_timeout_seconds: float = Field(
        default=30.0,
        gt=0,
        alias="VECTORSTORE_TIMEOUT_SECONDS",
        description="Per-request timeout (seconds) for vector store calls",
    )

    # Bulk-ingestion batching + bounded delete fan-out (see indexing service):
    # docs per index() call, and max concurrent vector-store delete round-trips.
    index_batch_size: int = Field(default=32, ge=1, alias="INDEX_BATCH_SIZE")
    index_max_concurrency: int = Field(default=8, ge=1, alias="INDEX_MAX_CONCURRENCY")


_vectorstore_config: VectorStoreConfig | None = None


def get_vectorstore_config() -> VectorStoreConfig:
    """Retrieve or initialize the global VectorStoreConfig singleton."""
    global _vectorstore_config
    if _vectorstore_config is None:
        _vectorstore_config = VectorStoreConfig()
        logger.info(
            f"Initialized VectorStoreConfig with collection={_vectorstore_config.collection_name}"
        )
    return _vectorstore_config


def get_vectorstore_config_no_lazy() -> VectorStoreConfig:
    """Non-logging version for bootstrap safety."""
    return get_vectorstore_config()


__all__ = [
    "VectorStoreConfig",
    "get_vectorstore_config",
    "get_vectorstore_config_no_lazy",
]
