"""A synchronous TTL cache for the graph's synchronous query surface.

``GraphDb.query`` and ``query_decoded`` are ``def``, not ``async def``. They
were reaching for the async cache layer (``TTLCache`` / ``RedisTTLCache``,
whose ``get``/``set`` are coroutines) and calling it without ``await``, which
went wrong in two different ways depending on the configured backend:

* **``cache_backend=redis``.** ``RedisTTLCache`` defines no ``__len__``, so it
  is always truthy and the cache branch ran. ``self._cache.get(key)`` returned
  a *coroutine object*, which is never ``None``, so the ``if cached is not
  None`` guard passed and ``query`` **returned that coroutine in place of the
  rows** — for every read-only query. The companion ``set`` was likewise never
  awaited.
* **``cache_backend=memory``.** ``TTLCache`` defines ``__len__`` and no
  ``__bool__``, so an *empty* one is falsy and ``and self._cache`` skipped the
  branch entirely. Because the ``set`` that would have filled it was never
  awaited either, it stayed empty, so it stayed falsy: graph caching was inert
  and silently so, which is also why nothing ever noticed the first bug.

The unit tests did not catch either one: they patch the cache classes with
``MagicMock``, whose ``get`` is synchronous and returns a truthy ``Mock``.

The fix is not to await from a sync method — that is impossible from inside a
running loop, and ``GraphDb``'s callers are sync by design. It is to give a
sync surface a sync cache. This one is deliberately in-process: the async
layer's Redis backing bought cross-process sharing, which a cache that never
worked was not delivering anyway, and reintroducing it would mean the sync
Redis client and a serialisation format — a larger change than the bug needs.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from threading import Lock
from typing import Any

__all__ = ["SyncTTLCache"]


class SyncTTLCache:
    """Bounded, thread-safe, least-recently-used TTL cache.

    Thread-safe because ``GraphDb`` is reached from request handlers and from
    worker threads, and ``OrderedDict`` mutation is not atomic across the
    read-modify-write that an LRU touch performs.
    """

    def __init__(self, maxsize: int = 128, ttl: float = 300.0) -> None:
        """
        Args:
            maxsize: Maximum number of live entries; the least recently used
                is evicted first. Must be positive.
            ttl: Seconds an entry stays fresh.
        """
        if maxsize <= 0:
            raise ValueError("maxsize must be positive")
        self._maxsize = maxsize
        self._ttl = ttl
        self._entries: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._lock = Lock()

    def get(self, key: str) -> Any | None:
        """Return the fresh value for ``key``, or ``None``.

        A miss and a stored ``None`` are indistinguishable here, which is
        fine: the graph caches query result *lists*, never ``None``.
        """
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if time.monotonic() >= expires_at:
                del self._entries[key]
                return None
            self._entries.move_to_end(key)
            return value

    def set(self, key: str, value: Any) -> None:
        """Store ``value`` under ``key``, evicting the least recent if full."""
        with self._lock:
            if key in self._entries:
                del self._entries[key]
            elif len(self._entries) >= self._maxsize:
                self._entries.popitem(last=False)
            self._entries[key] = (time.monotonic() + self._ttl, value)

    def clear(self) -> None:
        """Drop every entry."""
        with self._lock:
            self._entries.clear()

    def __len__(self) -> int:
        """Number of entries, expired ones included.

        Note that ``__bool__`` is defined below precisely so that this does
        NOT make an empty cache falsy — the mistake that hid the original bug.
        """
        with self._lock:
            return len(self._entries)

    def __bool__(self) -> bool:
        """Always ``True``: a cache exists whether or not it holds anything.

        Without this, ``__len__`` would make an empty cache falsy and
        ``if self._cache:`` would read as "no cache configured", which is how
        graph caching came to be silently inert.
        """
        return True
