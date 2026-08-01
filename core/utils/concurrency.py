"""Small async concurrency helpers.

``bounded_gather`` runs many coroutines with a hard ceiling on how many are
in-flight at once — the missing guard on ``asyncio.gather`` fan-outs over
user-controlled cardinality (a source emptied of thousands of documents, a
bulk memory rewrite), which would otherwise open thousands of simultaneous
round-trips and exhaust connections/memory.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Iterable
from typing import TypeVar

_T = TypeVar("_T")


async def bounded_gather(
    awaitables: Iterable[Awaitable[_T]],
    *,
    limit: int,
    return_exceptions: bool = False,
) -> list[_T | BaseException]:
    """Like ``asyncio.gather`` but with at most ``limit`` coroutines in flight.

    Results are returned in submission order. With ``return_exceptions=True`` a
    failing item yields its exception in place (mirroring ``asyncio.gather``);
    otherwise the first exception propagates and the rest are cancelled.

    Args:
        awaitables: The coroutines/awaitables to run.
        limit: Maximum number running concurrently (>= 1).
        return_exceptions: Capture per-item exceptions instead of raising.
    """
    semaphore = asyncio.Semaphore(max(1, limit))

    async def _run(item: Awaitable[_T]) -> _T:
        async with semaphore:
            return await item

    return await asyncio.gather(
        *(_run(item) for item in awaitables),
        return_exceptions=return_exceptions,
    )


__all__ = ["bounded_gather"]
