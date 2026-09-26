"""Background memory-write scheduling for the execution mixin.

Extracted from ``execution.py`` for the module size cap. Each remember() call
costs an embedding pass plus a vector upsert; running them post-response in a
tracked background task removes that latency from the caller without losing
failures (logged via the done callback).
"""

from __future__ import annotations

import asyncio
from typing import Any

from core.observability.logging import get_logger

logger = get_logger(__name__)

# Max concurrent background memory writes (each = embed + vector upsert). Caps
# the heavy work a request burst can schedule at once; excess writes queue on
# the semaphore rather than all running in parallel.
_MEMORY_WRITE_CONCURRENCY = 32

# Max scheduled-but-unfinished writes (running + queued on the semaphore).
# The semaphore bounds concurrency, not the backlog: under a sustained burst
# faster than the vector store, every request parked one more task (holding
# its query and response text) with no ceiling. Beyond this bound new writes
# are dropped — memory is best-effort, the request path is not.
_MEMORY_WRITE_MAX_BACKLOG = 1024


def schedule_memory_write(
    owner: Any, query: str, response_text: str, intent: str | None
) -> None:
    """Persist the interaction to memory off the request path.

    ``owner`` is the orchestrator (execution mixin): its ``memory_manager``
    performs the writes; task references and the concurrency semaphore are
    kept on the owner so lifetimes follow the orchestrator instance.
    """
    memory_manager = owner.memory_manager
    if memory_manager is None:
        return
    if getattr(owner, "_memory_writes_closed", False):
        # After aclose() nothing may start a write the drain will never see.
        logger.debug("memory_write_skipped_after_close")
        return
    if not hasattr(owner, "_memory_write_tasks"):
        owner._memory_write_tasks = set()
    # Bound concurrent embed+upsert work so a request burst can't spawn an
    # unbounded number of heavy background writes at once (lazy-init: the
    # semaphore binds to the loop active on first use).
    if getattr(owner, "_memory_write_sem", None) is None:
        owner._memory_write_sem = asyncio.Semaphore(_MEMORY_WRITE_CONCURRENCY)
    sem = owner._memory_write_sem
    assert sem is not None
    if len(owner._memory_write_tasks) >= _MEMORY_WRITE_MAX_BACKLOG:
        # Counted on the owner so an operator (or a test) can see the loss.
        owner._memory_writes_dropped = getattr(owner, "_memory_writes_dropped", 0) + 1
        logger.debug(
            "memory_write_dropped_backlog_full backlog=%d dropped_total=%d",
            len(owner._memory_write_tasks),
            owner._memory_writes_dropped,
        )
        return

    async def _write() -> None:
        async with sem:
            # The query and response writes are independent, so persist them
            # concurrently instead of paying two embed+upsert passes in series.
            writes = [
                memory_manager.remember(
                    f"User Query: {query}",
                    metadata={"type": "query", "intent": intent},
                )
            ]
            if response_text:
                writes.append(
                    memory_manager.remember(
                        f"Agent Response: {response_text}",
                        metadata={"type": "response", "intent": intent},
                    )
                )
            await asyncio.gather(*writes)

    task = asyncio.create_task(_write())
    owner._memory_write_tasks.add(task)

    def _done(finished: asyncio.Task) -> None:
        owner._memory_write_tasks.discard(finished)
        if not finished.cancelled() and finished.exception() is not None:
            logger.warning(f"Failed to save memory: {finished.exception()}")

    task.add_done_callback(_done)


async def drain_memory_writes(owner: Any, timeout: float) -> None:
    """Wait for in-flight background writes, then cancel the stragglers.

    Idempotent: the first call closes the owner to new writes; later calls
    find nothing left to wait for.

    Args:
        owner: The orchestrator whose ``_memory_write_tasks`` to drain.
        timeout: Seconds to wait for pending writes before cancelling them.
    """
    owner._memory_writes_closed = True
    pending = set(getattr(owner, "_memory_write_tasks", ()))
    if not pending:
        return
    _, still_running = await asyncio.wait(pending, timeout=max(0.0, timeout))
    if not still_running:
        return
    logger.warning(
        "memory_writes_cancelled_on_shutdown pending=%d timeout_s=%.1f",
        len(still_running),
        timeout,
    )
    for task in still_running:
        task.cancel()
    # Let the cancellations land so no task outlives the pools it writes to.
    await asyncio.gather(*still_running, return_exceptions=True)


__all__ = ["drain_memory_writes", "schedule_memory_write"]
