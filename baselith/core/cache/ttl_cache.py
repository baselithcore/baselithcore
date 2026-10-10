"""
TTL Cache module - Backwards compatibility shim.

This module provides backwards compatibility for code that imports from
`baselith.core.cache.ttl_cache`. New code should import directly from `baselith.core.cache`.

Migration:
    # Old (still works)
    from baselith.core.cache.ttl_cache import TTLCache

    # New (preferred)
    from baselith.core.cache import TTLCache
"""

# Re-export all cache classes for backwards compatibility
from baselith.core.cache import (
    CacheProtocol,
    RedisTTLCache,
    TTLCache,
    create_redis_client,
)

__all__ = [
    "CacheProtocol",
    "RedisTTLCache",
    "TTLCache",
    "create_redis_client",
]
