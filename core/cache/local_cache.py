"""
In-memory Cache implementations.

Provides in-memory TTL cache with LRU eviction policy.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from threading import Lock
from typing import Generic, Optional, Tuple, TypeVar

from core.cache.metrics import get_metrics_collector


K = TypeVar("K")
V = TypeVar("V")


class TTLCache(Generic[K, V]):
    """
    Simple in-memory cache with TTL and LRU eviction policy.
    Async interface for system-wide consistency.
    """

    def __init__(
        self,
        maxsize: int | None = None,
        ttl: float | None = None,
        metrics_name: str = "ttl_cache",
    ) -> None:
        from core.config.cache import get_cache_config

        config = get_cache_config()
        self._maxsize = maxsize if maxsize is not None else config.maxsize_default
        self._ttl = ttl if ttl is not None else config.ttl_default

        if self._maxsize <= 0:
            raise ValueError("maxsize must be positive")
        if self._ttl <= 0:
            raise ValueError("ttl must be positive")

        self._store: OrderedDict[K, Tuple[V, float]] = OrderedDict()
        self._lock = Lock()
        self._last_purge_time: float = 0.0
        self.PURGE_INTERVAL: float = 60.0

        # Initialize metrics tracking
        self._metrics_name = metrics_name
        self._metrics = get_metrics_collector().get_or_create_metrics(metrics_name)

    def _should_purge(self) -> bool:
        return (time.time() - self._last_purge_time) > self.PURGE_INTERVAL

    def _purge_expired(self) -> None:
        now = time.time()
        self._last_purge_time = now
        keys_to_delete = [
            key for key, (_, expiry) in self._store.items() if expiry <= now
        ]
        for key in keys_to_delete:
            self._store.pop(key, None)

    async def get(self, key: K) -> Optional[V]:
        """Get a value from the cache (async wrapper)."""
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self._metrics.record_miss()
                return None
            value, expiry = entry
            if expiry <= time.time():
                self._store.pop(key, None)
                self._metrics.record_miss()
                self._metrics.update_size(len(self._store))
                return None
            # Update LRU order
            self._store.pop(key)
            self._store[key] = (value, expiry)
            self._metrics.record_hit()
            return value

    async def set(self, key: K, value: V) -> None:
        """Set a value in the cache (async wrapper)."""
        with self._lock:
            if self._should_purge():
                self._purge_expired()

            evicted = False
            if key in self._store:
                self._store.pop(key)
            elif len(self._store) >= self._maxsize:
                self._purge_expired()
                if len(self._store) >= self._maxsize:
                    self._store.popitem(last=False)
                    evicted = True

            expiry = time.time() + self._ttl
            self._store[key] = (value, expiry)

            # Track metrics
            self._metrics.record_set(ttl_seconds=self._ttl)
            if evicted:
                self._metrics.record_eviction()
            self._metrics.update_size(len(self._store))

    async def delete(self, key: K) -> None:
        """Delete a value from the cache (async wrapper)."""
        with self._lock:
            if key in self._store:
                self._store.pop(key, None)
                self._metrics.record_delete()
                self._metrics.update_size(len(self._store))

    async def clear(self) -> None:
        """Clear all entries from the cache (async wrapper)."""
        with self._lock:
            self._store.clear()

    def __len__(self) -> int:
        with self._lock:
            if self._should_purge():
                self._purge_expired()
            return len(self._store)
