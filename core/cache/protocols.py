"""Unified cache protocols for the baselith-core.

This module provides standardized cache protocol definitions to avoid
duplicate protocol definitions across the codebase.

All modules should import cache protocols from this central location.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol, TypeVar, runtime_checkable

K = TypeVar("K")
V = TypeVar("V")


@runtime_checkable
class CacheProtocol(Protocol[K, V]):  # type: ignore[misc]
    """Protocol for generic cache implementations.

    This is the canonical cache protocol that all cache implementations
    should conform to. It supports generic key/value types.
    """

    async def get(self, key: K) -> V | None:
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

    async def get(self, key: K) -> V | None:
        """Get a value from cache."""
        ...

    async def set(self, key: K, value: V, ttl: int | None = None) -> None:
        """Set a value with optional TTL."""
        ...

    async def delete(self, key: K) -> None:
        """Delete a value from cache."""
        ...


@runtime_checkable
class BatchCacheProtocol(CacheProtocol[K, V], Protocol[K, V]):  # type: ignore[misc]
    """Optional protocol for caches that support batch operations."""

    async def get_many(self, keys: Sequence[K]) -> list[V | None]:
        """Fetch many values in a single cache round-trip."""
        ...

    async def set_many(self, items: Sequence[tuple[K, V]]) -> None:
        """Store many values in a single cache round-trip."""
        ...


# Convenience type aliases for common use cases
StringCache = CacheProtocol[str, str]
AnyCache = CacheProtocol[Any, Any]


__all__ = [
    "AnyCache",
    "BatchCacheProtocol",
    "CacheProtocol",
    "ClearableCacheProtocol",
    "StringCache",
    "TTLCacheProtocol",
]
