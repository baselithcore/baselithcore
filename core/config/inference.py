"""Configuration for the shared inference services (embedding, rerank, Qdrant).

Embedding, reranking and vector search are *core services*: plugins call them
instead of loading ``torch`` models or opening a Qdrant client in-process. The
default backend is ``remote`` (Hugging Face Text Embeddings Inference over
HTTP), so an API pod carries no ML runtime at all; ``local`` is an explicit
development opt-in.
"""

from __future__ import annotations

from typing import Literal
from urllib.parse import urlsplit

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Backend = Literal["remote", "local"]
#: Wire protocol of the model server (see core/services/inference/_protocols.py):
#: the platform's TEI, or what a customer's own GPUs already expose.
EmbeddingApi = Literal["tei", "openai"]
RerankApi = Literal["tei", "cohere", "nim"]


def _strip_url(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip().rstrip("/")
    return value or None


def _relative_path(value: str | None) -> str | None:
    """Refuse a request path that would replace the server URL.

    httpx resolves an absolute (``https://host/x``) or network-path
    (``//host/x``) reference against ``base_url`` by *discarding* it, so such a
    path would send the request — and the bearer token checked only against
    the configured URL — to another host.
    """
    if value is None:
        return None
    value = value.strip()
    parts = urlsplit(value)
    if parts.scheme or parts.netloc or value.startswith("//"):
        raise ValueError(
            "path must be relative to the server URL (e.g. '/embed'), "
            f"not an absolute URL: {value!r}"
        )
    return value or None


class EmbeddingConfig(BaseSettings):
    """``BASELITH_EMBEDDING_*`` settings."""

    model_config = SettingsConfigDict(
        env_prefix="BASELITH_EMBEDDING_", case_sensitive=False, extra="ignore"
    )

    backend: Backend = Field(
        default="remote",
        description="'remote' calls a TEI server; 'local' loads the model "
        "in-process (development only, imports torch).",
    )
    url: str | None = Field(
        default=None, description="Base URL of the TEI server (remote backend)."
    )
    api_key: SecretStr | None = Field(
        default=None, description="Optional bearer token for the TEI server."
    )
    timeout: float = Field(default=60.0, gt=0, description="Per-request timeout (s).")
    max_retries: int = Field(
        default=3,
        ge=0,
        le=10,
        description="Retries on 5xx / timeout (429 only with retry_rate_limited).",
    )
    backoff_base: float = Field(
        default=0.5, ge=0, description="First retry delay (s); doubles each attempt."
    )
    max_total_seconds: float = Field(
        default=50.0,
        gt=0,
        description="Budget for one call, retries and backoff included; each "
        "attempt's timeout is clamped to what is left. Keep it below the edge "
        "proxy's read timeout (nginx/ingress default 60 s) so a slow model "
        "server fails the call rather than the client's connection.",
    )
    max_response_bytes: int = Field(
        default=64 * 1024 * 1024,
        ge=1024,
        description="Largest response body accepted from the server.",
    )
    retry_rate_limited: bool = Field(
        default=False,
        description="Retry on HTTP 429. Off by default: a full TEI queue is "
        "not helped by more requests on the interactive path; enable for "
        "batch indexing jobs.",
    )
    allow_insecure_key: bool = Field(
        default=False,
        description="Send the API key over plain http to a host that is not "
        "loopback or cluster-internal (single-label, `*.svc`, `*.cluster.local`). "
        "Off by default: the key would cross the network in clear.",
    )

    api: EmbeddingApi = Field(
        default="tei",
        description="Server protocol: 'tei' (Hugging Face TEI /embed) or "
        "'openai' (/embeddings: OpenAI, Azure OpenAI, vLLM, NVIDIA NIM, "
        "Infinity, Ollama /v1). For 'openai' the URL includes /v1.",
    )
    path: str | None = Field(
        default=None,
        description="Request path under the URL; empty = the protocol's own "
        "(/embed for tei, /embeddings for openai).",
    )
    query_prefix: str = Field(
        default="",
        description="Text prepended to queries, for models trained with an "
        "instruction (e5: 'query: ', Qwen3-Embedding, ...). bge-m3 needs none.",
    )
    document_prefix: str = Field(
        default="", description="Text prepended to documents (e5: 'passage: ')."
    )
    ca_bundle: str | None = Field(
        default=None,
        description="PEM file of a private CA to trust, for a model server on "
        "the customer's own hardware.",
    )
    client_cert: str | None = Field(
        default=None,
        description="PEM client certificate presented to the server (mutual TLS).",
    )
    client_key: str | None = Field(
        default=None,
        description="PEM private key of client_cert (mutual TLS).",
    )

    @field_validator("url")
    @classmethod
    def _normalize_url(cls, value: str | None) -> str | None:
        return _strip_url(value)

    @field_validator("path")
    @classmethod
    def _path_is_relative(cls, value: str | None) -> str | None:
        return _relative_path(value)

    model: str = Field(default="BAAI/bge-m3", description="Embedding model id.")
    dim: int = Field(default=1024, gt=0, description="Vector dimension.")
    allow_model_substitution: bool = Field(
        default=False,
        description="Let the served model stand in for a different requested "
        "one (VECTORSTORE_EMBEDDING_MODEL) when no local runtime can serve it. "
        "Off by default: vectors from another model do not share the index's "
        "geometry. Even when on, BASELITH_EMBEDDING_DIM must equal "
        "VECTORSTORE_EMBEDDING_DIM; a warning is logged.",
    )
    batch_size: int = Field(
        default=32, ge=1, le=512, description="Texts per HTTP request."
    )


class RerankConfig(BaseSettings):
    """``BASELITH_RERANK_*`` settings."""

    model_config = SettingsConfigDict(
        env_prefix="BASELITH_RERANK_", case_sensitive=False, extra="ignore"
    )

    backend: Backend = Field(
        default="remote",
        description="'remote' calls a TEI server; 'local' loads the model "
        "in-process (development only, imports torch).",
    )
    url: str | None = Field(
        default=None, description="Base URL of the TEI server (remote backend)."
    )
    api_key: SecretStr | None = Field(
        default=None, description="Optional bearer token for the TEI server."
    )
    timeout: float = Field(default=60.0, gt=0, description="Per-request timeout (s).")
    max_retries: int = Field(
        default=3,
        ge=0,
        le=10,
        description="Retries on 5xx / timeout (429 only with retry_rate_limited).",
    )
    backoff_base: float = Field(
        default=0.5, ge=0, description="First retry delay (s); doubles each attempt."
    )
    max_total_seconds: float = Field(
        default=50.0,
        gt=0,
        description="Budget for one call, retries and backoff included; each "
        "attempt's timeout is clamped to what is left. Keep it below the edge "
        "proxy's read timeout (nginx/ingress default 60 s) so a slow model "
        "server fails the call rather than the client's connection.",
    )
    max_response_bytes: int = Field(
        default=64 * 1024 * 1024,
        ge=1024,
        description="Largest response body accepted from the server.",
    )
    retry_rate_limited: bool = Field(
        default=False,
        description="Retry on HTTP 429. Off by default: a full TEI queue is "
        "not helped by more requests on the interactive path; enable for "
        "batch indexing jobs.",
    )
    allow_insecure_key: bool = Field(
        default=False,
        description="Send the API key over plain http to a host that is not "
        "loopback or cluster-internal (single-label, `*.svc`, `*.cluster.local`). "
        "Off by default: the key would cross the network in clear.",
    )

    api: RerankApi = Field(
        default="tei",
        description="Server protocol: 'tei' (TEI /rerank), 'cohere' (/rerank "
        "with documents/top_n: Cohere, Jina, vLLM, Infinity) or 'nim' (NVIDIA "
        "NIM /ranking). URL includes /v1 where the server has one.",
    )
    path: str | None = Field(
        default=None,
        description="Request path under the URL; empty = the protocol's own.",
    )
    ca_bundle: str | None = Field(
        default=None,
        description="PEM file of a private CA to trust, for a model server on "
        "the customer's own hardware.",
    )
    client_cert: str | None = Field(
        default=None,
        description="PEM client certificate presented to the server (mutual TLS).",
    )
    client_key: str | None = Field(
        default=None,
        description="PEM private key of client_cert (mutual TLS).",
    )

    @field_validator("url")
    @classmethod
    def _normalize_url(cls, value: str | None) -> str | None:
        return _strip_url(value)

    @field_validator("path")
    @classmethod
    def _path_is_relative(cls, value: str | None) -> str | None:
        return _relative_path(value)

    model: str = Field(default="BAAI/bge-reranker-v2-m3", description="Reranker id.")
    max_candidates: int = Field(
        default=100, ge=1, le=1000, description="Hard cap on texts per rerank call."
    )
    batch_size: int = Field(
        default=32, ge=1, le=512, description="Texts per HTTP request (TEI limit)."
    )


class QdrantServerConfig(BaseSettings):
    """``BASELITH_QDRANT_*`` settings. Server mode only — never ``path=``."""

    model_config = SettingsConfigDict(
        env_prefix="BASELITH_QDRANT_",
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
    )

    # ``QDRANT_URL`` / ``QDRANT_API_KEY`` are what every deploy file already
    # sets for the vector store (compose, Helm, configs/.env.*); the prefixed
    # names remain for an inference-only Qdrant and win when both are set.
    url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("BASELITH_QDRANT_URL", "QDRANT_URL"),
        description="Qdrant server URL (e.g. http://qdrant:6333).",
    )
    api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("BASELITH_QDRANT_API_KEY", "QDRANT_API_KEY"),
    )
    timeout: float = Field(default=60.0, gt=0)
    prefer_grpc: bool = Field(
        default=False,
        description="Use gRPC for data calls; multivector (ColBERT) payloads "
        "serialize far faster than over REST.",
    )
    grpc_port: int = Field(default=6334, ge=1, le=65535)


_embedding: EmbeddingConfig | None = None
_rerank: RerankConfig | None = None
_qdrant: QdrantServerConfig | None = None


def get_embedding_config() -> EmbeddingConfig:
    """Return the process-wide :class:`EmbeddingConfig`."""
    global _embedding
    if _embedding is None:
        _embedding = EmbeddingConfig()
    return _embedding


def get_rerank_config() -> RerankConfig:
    """Return the process-wide :class:`RerankConfig`."""
    global _rerank
    if _rerank is None:
        _rerank = RerankConfig()
    return _rerank


def get_qdrant_server_config() -> QdrantServerConfig:
    """Return the process-wide :class:`QdrantServerConfig`."""
    global _qdrant
    if _qdrant is None:
        _qdrant = QdrantServerConfig()
    return _qdrant


def reset_inference_config() -> None:
    """Drop the cached configs (tests)."""
    global _embedding, _rerank, _qdrant
    _embedding = _rerank = _qdrant = None


__all__ = [
    "EmbeddingApi",
    "EmbeddingConfig",
    "QdrantServerConfig",
    "RerankApi",
    "RerankConfig",
    "get_embedding_config",
    "get_qdrant_server_config",
    "get_rerank_config",
    "reset_inference_config",
]
