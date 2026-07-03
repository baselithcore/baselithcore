"""
Core Cache Module.

Provides in-memory and Redis-backed TTL caches with LRU eviction policy.
These are generic, reusable cache implementations for the baselith-core.
"""

from __future__ import annotations

# Implementations
from core.cache.local_cache import TTLCache

# Protocols
from core.cache.protocols import (
    AnyCache,
    BatchCacheProtocol,
    CacheProtocol,
    ClearableCacheProtocol,
    StringCache,
    TTLCacheProtocol,
)
from core.cache.redis_cache import RedisTTLCache, close_redis_pools, create_redis_client


def SemanticLLMCache(*args, **kwargs):
    """Lazy factory for SemanticLLMCache to avoid circular imports."""
    from core.cache.semantic_cache import SemanticLLMCache as _SemanticLLMCache

    return _SemanticLLMCache(*args, **kwargs)


__all__ = [
    "AnyCache",
    "BatchCacheProtocol",
    "CacheProtocol",
    "ClearableCacheProtocol",
    "RedisTTLCache",
    "SemanticLLMCache",
    "StringCache",
    "TTLCache",
    "TTLCacheProtocol",
    "close_redis_pools",
    "create_redis_client",
]
