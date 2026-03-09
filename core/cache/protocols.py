"""Unified cache protocols for the baselith-core.

This module provides standardized cache protocol definitions to avoid
duplicate protocol definitions across the codebase.

All modules should import cache protocols from this central location.
"""

from __future__ import annotations

from typing import Any, Optional, Protocol, TypeVar, runtime_checkable

K = TypeVar("K")
V = TypeVar("V")


@runtime_checkable
class CacheProtocol(Protocol[K, V]):  # type: ignore[misc]
    """Protocol for generic cache implementations.

    This is the canonical cache protocol that all cache implementations
    should conform to. It supports generic key/value types.
    """

    async def get(self, key: K) -> Optional[V]:
        """Get a value from cache."""
        ...

    async def set(self, key: K, value: V) -> None:
        """Set a value in cache."""
        ...

    async def delete(self, key: K) -> None:
        """Delete a value from cache."""
        ...


@runtime_checkable
class ClearableCacheProtocol(CacheProtocol[K, V], Protocol[K, V]):  # type: ignore[misc]
    """Extended cache protocol with clear capability."""

    async def clear(self) -> None:
        """Clear all entries from the cache."""
        ...


@runtime_checkable
class TTLCacheProtocol(Protocol[K, V]):  # type: ignore[misc]
    """Protocol for TTL-based cache implementations."""

    async def get(self, key: K) -> Optional[V]:
        """Get a value from cache."""
        ...

    async def set(self, key: K, value: V, ttl: Optional[int] = None) -> None:
        """Set a value with optional TTL."""
        ...

    async def delete(self, key: K) -> None:
        """Delete a value from cache."""
        ...


# Convenience type aliases for common use cases
StringCache = CacheProtocol[str, str]
AnyCache = CacheProtocol[Any, Any]


__all__ = [
    "CacheProtocol",
    "ClearableCacheProtocol",
    "TTLCacheProtocol",
    "StringCache",
    "AnyCache",
]
