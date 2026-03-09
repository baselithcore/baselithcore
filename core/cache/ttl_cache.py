"""
TTL Cache module - Backwards compatibility shim.

This module provides backwards compatibility for code that imports from
`core.cache.ttl_cache`. New code should import directly from `core.cache`.

Migration:
    # Old (still works)
    from core.cache.ttl_cache import TTLCache

    # New (preferred)
    from core.cache import TTLCache
"""

# Re-export all cache classes for backwards compatibility
from core.cache import (
    CacheProtocol,
    TTLCache,
    RedisTTLCache,
    create_redis_client,
)

__all__ = [
    "CacheProtocol",
    "TTLCache",
    "RedisTTLCache",
    "create_redis_client",
]
