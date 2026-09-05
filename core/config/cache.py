"""
Cache configuration settings.

Configuration for Local, Redis, and Semantic caches.
"""

from typing import Any

from pydantic import Field, field_serializer
from pydantic_settings import BaseSettings, SettingsConfigDict


def _redact_url(value: str | None) -> str | None:
    """Strip ``user:password@`` userinfo from a connection string, if present.

    Same contract as ``core.config.storage``: serialization surfaces are
    redacted, attribute access keeps the usable DSN. Imported lazily because
    ``core.observability.redaction`` imports ``core.config`` at module level.
    """
    if not value:
        return value
    try:
        from core.observability.redaction import redact_url_credentials

        return redact_url_credentials(value)
    except Exception:  # pragma: no cover - redaction must never break config
        return value


class CacheConfig(BaseSettings):
    """
    General cache configuration.

    Environment variables: CACHE_TTL_DEFAULT, CACHE_MAXSIZE_DEFAULT
    """

    model_config = SettingsConfigDict(
        env_prefix="CACHE_",
        case_sensitive=False,
        extra="ignore",
    )

    ttl_default: float = Field(
        default=300.0, description="Default TTL in seconds for caches"
    )

    maxsize_default: int = Field(
        default=256, description="Default maximum size for in-memory caches"
    )

    cross_worker_single_flight: bool = Field(
        default=False,
        description=(
            "Opt-in: coalesce cache-miss fills ACROSS workers/pods via a Redis "
            "lock, not just within one event loop. Only takes effect where the "
            "backing cache is genuinely shared (Redis) — an in-process store "
            "gives the losing worker nothing to read back. Fail-open: if Redis "
            "is unreachable the path degrades to in-process coalescing."
        ),
    )


class RedisCacheConfig(BaseSettings):
    """
    Redis cache configuration.

    Environment variables: CACHE_REDIS_URL (the ``url`` field's alias), plus
    the ``REDIS_``-prefixed fields (REDIS_CACHE_PREFIX, REDIS_CACHE_TTL, ...).

    The connection URL can embed a password; like ``StorageConfig`` it stays a
    plain ``str`` for the many DSN consumers, but every serialization surface
    (``repr``, ``model_dump``, ``model_dump_json``) redacts the credential.
    """

    model_config = SettingsConfigDict(
        env_prefix="REDIS_",
        case_sensitive=False,
        extra="ignore",
    )

    url: str = Field(
        default="redis://redis:6379/1",
        alias="CACHE_REDIS_URL",
        description="Redis connection URL",
    )

    @field_serializer("url", when_used="always")
    def _redact_url_credentials(self, value: str | None) -> str | None:
        """Redact the embedded password from the URL on dump."""
        return _redact_url(value)

    def __repr_args__(self) -> Any:
        """Redact the URL credential in ``repr()`` as well as in dumps."""
        for name, value in super().__repr_args__():
            if name == "url" and isinstance(value, str):
                yield name, _redact_url(value)
            else:
                yield name, value

    cache_prefix: str = Field(
        default="baselithcore:cache", description="Prefix for Redis cache keys"
    )

    cache_ttl: float = Field(
        default=3600.0, description="Default TTL for Redis cache entries"
    )

    max_connections: int = Field(
        default=50,
        ge=1,
        description="Maximum connections per shared Redis connection pool",
    )

    health_check_interval: float = Field(
        default=30.0,
        ge=0.0,
        description="Seconds between idle-connection health checks (0 disables)",
    )

    socket_timeout: float = Field(
        default=5.0,
        gt=0.0,
        description=(
            "Per-operation socket read deadline. Without it, a Redis that "
            "accepts the connection but stops responding mid-command hangs the "
            "caller forever while holding a pooled connection, so enough hung "
            "operations exhaust the bounded pool."
        ),
    )

    socket_connect_timeout: float = Field(
        default=2.0,
        gt=0.0,
        description="TCP connect deadline for a new pooled Redis connection",
    )


class SemanticCacheConfig(BaseSettings):
    """
    Semantic cache configuration.

    Environment variables: SEMANTIC_CACHE_MAXSIZE, SEMANTIC_CACHE_TTL, etc.
    """

    model_config = SettingsConfigDict(
        env_prefix="SEMANTIC_CACHE_",
        case_sensitive=False,
        extra="ignore",
    )

    maxsize: int = Field(
        default=1000, description="Maximum number of semantic cache entries per tenant"
    )

    ttl: float = Field(
        default=3600.0, description="TTL in seconds for semantic cache entries"
    )

    threshold: float = Field(
        default=0.85,
        ge=0.0,
        le=1.0,
        description="Minimum similarity threshold (0.0-1.0)",
    )

    fingerprint_enabled: bool = Field(
        default=True,
        description=(
            "Enable the word n-gram fingerprint lookup tier between the "
            "exact-match key and the embedding similarity scan"
        ),
    )

    fingerprint_threshold: float = Field(
        default=0.8,
        ge=0.0,
        le=1.0,
        description=(
            "Minimum Jaccard similarity of word n-gram fingerprints for a "
            "fingerprint-tier hit (0.0-1.0)"
        ),
    )


# Global instances
_cache_config: CacheConfig | None = None
_redis_cache_config: RedisCacheConfig | None = None
_semantic_cache_config: SemanticCacheConfig | None = None


def get_cache_config() -> CacheConfig:
    """Get or create global Cache config."""
    global _cache_config
    if _cache_config is None:
        _cache_config = CacheConfig()
    return _cache_config


def get_redis_cache_config() -> RedisCacheConfig:
    """Get or create global Redis Cache config."""
    global _redis_cache_config
    if _redis_cache_config is None:
        _redis_cache_config = RedisCacheConfig()
    return _redis_cache_config


def get_semantic_cache_config() -> SemanticCacheConfig:
    """Get or create global Semantic Cache config."""
    global _semantic_cache_config
    if _semantic_cache_config is None:
        _semantic_cache_config = SemanticCacheConfig()
    return _semantic_cache_config
