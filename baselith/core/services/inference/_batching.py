"""Run the batches of one embed/rerank call, a few at a time.

A call that spans several batches used to send them one after another: a
640-chunk indexing job at the default batch size of 32 was twenty serial
round trips to a server that batches concurrent requests itself. Remote
backends now overlap a bounded number of them; in-process backends keep
running one at a time, since their batches would only contend for the same
CPU threads.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence

#: Batches a remote backend may have in flight for one call. Small on
#: purpose: enough to hide the round-trip latency, not enough to trip a
#: hosted endpoint's rate limit or starve other callers of the shared pool.
REMOTE_BATCH_CONCURRENCY = 4


async def map_batches[T, R](
    batches: Sequence[T],
    call: Callable[[T], Awaitable[R]],
    *,
    concurrency: int,
) -> list[R]:
    """Apply ``call`` to every batch; results come back in batch order.

    Args:
        batches: The inputs, one per request.
        call: The request for one batch.
        concurrency: Requests allowed in flight at once (``<= 1`` is serial).

    Returns:
        One result per batch, in the order of ``batches``.

    Raises:
        Exception: The first failure, as the backend raised it; the batches
            still in flight are cancelled rather than left to finish.
    """
    if concurrency <= 1 or len(batches) <= 1:
        return [await call(batch) for batch in batches]

    gate = asyncio.Semaphore(concurrency)

    async def bounded(batch: T) -> R:
        async with gate:
            return await call(batch)

    try:
        async with asyncio.TaskGroup() as group:
            tasks = [group.create_task(bounded(batch)) for batch in batches]
    except* Exception as failures:
        # One backend error, not a group: callers catch InferenceError.
        raise failures.exceptions[0] from None
    return [task.result() for task in tasks]


__all__ = ["REMOTE_BATCH_CONCURRENCY", "map_batches"]
