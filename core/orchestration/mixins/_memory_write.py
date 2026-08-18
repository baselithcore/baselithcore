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
    if not hasattr(owner, "_memory_write_tasks"):
        owner._memory_write_tasks = set()
    # Bound concurrent embed+upsert work so a request burst can't spawn an
    # unbounded number of heavy background writes at once (lazy-init: the
    # semaphore binds to the loop active on first use).
    if getattr(owner, "_memory_write_sem", None) is None:
        owner._memory_write_sem = asyncio.Semaphore(_MEMORY_WRITE_CONCURRENCY)
    sem = owner._memory_write_sem
    assert sem is not None

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


__all__ = ["schedule_memory_write"]
