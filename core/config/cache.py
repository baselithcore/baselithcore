"""
Cache configuration settings.

Configuration for Local, Redis, and Semantic caches.
"""

from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class CacheConfig(BaseSettings):
    """
    General cache configuration.

    Environment variables: CACHE_TTL_DEFAULT, CACHE_MAXSIZE_DEFAULT
    """

    model_config = SettingsConfigDict(
        env_prefix="CACHE_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    ttl_default: float = Field(
        default=300.0, description="Default TTL in seconds for caches"
    )

    maxsize_default: int = Field(
        default=256, description="Default maximum size for in-memory caches"
    )


class RedisCacheConfig(BaseSettings):
    """
    Redis cache configuration.

    Environment variables: REDIS_URL, REDIS_CACHE_PREFIX, REDIS_CACHE_TTL
    """

    model_config = SettingsConfigDict(
        env_prefix="REDIS_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    url: str = Field(
        default="redis://redis:6379/1",
        alias="CACHE_REDIS_URL",
        description="Redis connection URL",
    )

    cache_prefix: str = Field(
        default="baselithcore:cache", description="Prefix for Redis cache keys"
    )

    cache_ttl: float = Field(
        default=3600.0, description="Default TTL for Redis cache entries"
    )


class SemanticCacheConfig(BaseSettings):
    """
    Semantic cache configuration.

    Environment variables: SEMANTIC_CACHE_MAXSIZE, SEMANTIC_CACHE_TTL, etc.
    """

    model_config = SettingsConfigDict(
        env_prefix="SEMANTIC_CACHE_",
        env_file=".env",
        env_file_encoding="utf-8",
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


# Global instances
_cache_config: Optional[CacheConfig] = None
_redis_cache_config: Optional[RedisCacheConfig] = None
_semantic_cache_config: Optional[SemanticCacheConfig] = None


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
