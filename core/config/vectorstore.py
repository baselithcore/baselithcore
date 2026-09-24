"""Vector store configuration (Qdrant / pgvector).

Extracted from ``core.config.services`` for the module size cap; that module
re-exports everything here, so existing ``from core.config import
get_vectorstore_config`` imports are unchanged.
"""

from __future__ import annotations

import logging
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, model_validator
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
        default="BAAI/bge-m3",
        description="Embedding model name",
    )

    # Dimension size of the vectors produced by the model.
    embedding_dim: int = Field(default=1024, description="Embedding dimension")
    embedding_fallback_model: str = Field(
        default="sentence-transformers/all-MiniLM-L6-v2",
        description=(
            "Operator fallback embedding model. Use it together with "
            "VECTORSTORE_EMBEDDING_FALLBACK_DIM when bge-m3 is not available; "
            "switching models requires a matching vector dimension and a fresh "
            "or migrated collection."
        ),
    )
    embedding_fallback_dim: int = Field(
        default=384,
        description="Vector dimension for VECTORSTORE_EMBEDDING_FALLBACK_MODEL.",
    )

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
    # Both were read with getattr by the search orchestrator and the service,
    # but never declared — and this model ignores unknown keys — so setting
    # either variable did nothing and the cache could not be turned off.
    search_cache_enabled: bool = Field(
        default=True,
        description="Cache vector search results in Redis (keyed per tenant, "
        "vector, filter and re-rank question).",
    )
    search_cache_ttl: int = Field(
        default=300,
        ge=1,
        description="Lifetime of a cached search result, in seconds.",
    )

    # Qdrant deployment mode: 'server' for cluster/docker, 'local' for in-memory/disk.
    qdrant_mode: str = Field(
        default="server",
        validation_alias=AliasChoices("VECTORSTORE_QDRANT_MODE", "QDRANT_MODE"),
    )
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

    # == pgvector HNSW tuning ==
    # Defaults are pgvector's own, so a deployment that never touches these
    # behaves exactly as before. ``m`` and ``ef_construction`` are *build*
    # parameters baked into the index (raising them costs build time and index
    # size, and buys recall); ``ef_search`` is a *query* parameter applied per
    # search, trading latency for recall without a rebuild.
    hnsw_m: int = Field(
        default=16,
        ge=2,
        le=100,
        description="HNSW graph connectivity (pgvector 'm'); build-time.",
    )
    hnsw_ef_construction: int = Field(
        default=64,
        ge=4,
        le=1000,
        description="HNSW build-time candidate list size (pgvector "
        "'ef_construction'); must be >= 2 * hnsw_m.",
    )
    hnsw_ef_search: int = Field(
        default=40,
        ge=0,
        le=1000,
        description="HNSW query-time candidate list size, applied as "
        "SET LOCAL hnsw.ef_search per search. 0 leaves the server default "
        "alone and skips the enclosing transaction.",
    )

    # Span token usage costs a second tokenizer pass over the texts that miss
    # the cache. That is cheap next to the transformer forward pass, but it is
    # not free and it buys nothing unless someone is reading the spans — so it
    # is opt-in, and when on it runs on the inference pool rather than on the
    # event loop.
    embedding_token_usage_enabled: bool = Field(
        default=False,
        description="Record gen_ai.usage.input_tokens on embedding spans. Costs "
        "an extra tokenizer pass per cache miss; off by default.",
    )

    @model_validator(mode="after")
    def _validate_hnsw(self) -> VectorStoreConfig:
        """Reject an index build pgvector itself would refuse.

        pgvector requires ``ef_construction >= 2 * m``; catching it here turns
        a failed ``CREATE INDEX`` during the first indexing run into a
        configuration error at startup.
        """
        if self.hnsw_ef_construction < 2 * self.hnsw_m:
            raise ValueError(
                f"VECTORSTORE_HNSW_EF_CONSTRUCTION={self.hnsw_ef_construction} "
                f"must be at least 2 * VECTORSTORE_HNSW_M ({2 * self.hnsw_m}); "
                "pgvector rejects the index build otherwise."
            )
        return self


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
