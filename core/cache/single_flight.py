"""Per-key single-flight coalescing for cache miss → fill paths.

A cache miss for a popular key triggers an expensive backend call (LLM
prompt, vector search, etc). Without coordination, every concurrent caller
that arrives during the in-flight call independently re-issues the same
request — the well-known *thundering herd* / *cache stampede* problem.

``SingleFlight`` coalesces concurrent calls for the same key: only the first
caller executes the supplied factory; subsequent waiters share the eventual
result (or exception).

Usage::

    sf = SingleFlight()

    async def fetch(prompt: str) -> str:
        cached = await cache.get(prompt)
        if cached is not None:
            return cached
        return await sf.do(prompt, lambda: expensive_call(prompt))
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Dict, Generic, TypeVar

from core.observability.logging import get_logger

logger = get_logger(__name__)

T = TypeVar("T")


class SingleFlight(Generic[T]):
    """Coalesce concurrent calls keyed by hashable identity.

    Implementation is async-safe within a single event loop. Cross-process
    coalescing (e.g. across worker pods) requires a distributed lock such as
    Redis ``SET NX EX`` — see :class:`RedisSingleFlight` if/when that becomes
    a real bottleneck.
    """

    def __init__(self) -> None:
        self._inflight: Dict[Any, asyncio.Future[T]] = {}
        self._lock = asyncio.Lock()

    async def do(self, key: Any, factory: Callable[[], Awaitable[T]]) -> T:
        """Run ``factory`` exactly once for ``key`` while concurrent callers wait."""
        async with self._lock:
            existing = self._inflight.get(key)
            if existing is not None:
                future = existing
                owner = False
            else:
                loop = asyncio.get_running_loop()
                future = loop.create_future()
                self._inflight[key] = future
                owner = True

        if owner:
            try:
                value = await factory()
            except BaseException as exc:
                future.set_exception(exc)
                async with self._lock:
                    self._inflight.pop(key, None)
                raise
            future.set_result(value)
            async with self._lock:
                self._inflight.pop(key, None)
            return value

        return await future

    def in_flight(self) -> int:
        """Return the number of currently coalesced keys (testing/diagnostics)."""
        return len(self._inflight)
