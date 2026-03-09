"""
Core Cache Module.

Provides in-memory and Redis-backed TTL caches with LRU eviction policy.
These are generic, reusable cache implementations for the baselith-core.
"""

from __future__ import annotations

# Protocols
from core.cache.protocols import (
    AnyCache,
    CacheProtocol,
    ClearableCacheProtocol,
    StringCache,
    TTLCacheProtocol,
)

# Implementations
from core.cache.local_cache import TTLCache
from core.cache.redis_cache import RedisTTLCache, create_redis_client


def SemanticLLMCache(*args, **kwargs):
    """Lazy factory for SemanticLLMCache to avoid circular imports."""
    from core.cache.semantic_cache import SemanticLLMCache as _SemanticLLMCache

    return _SemanticLLMCache(*args, **kwargs)


__all__ = [
    "CacheProtocol",
    "ClearableCacheProtocol",
    "TTLCacheProtocol",
    "AnyCache",
    "StringCache",
    "TTLCache",
    "RedisTTLCache",
    "SemanticLLMCache",
    "create_redis_client",
]
