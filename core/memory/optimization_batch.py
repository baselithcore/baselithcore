"""Batched provider writes for memory maintenance.

Consolidation and compression each write a whole batch of items. Routing every
item through ``MemoryProvider.add`` cost one embedding call and one
durability-acked upsert *per item*; providers that can take a batch expose
``add_many`` and do it in a single pass.
"""

from __future__ import annotations

from typing import Any

from core.memory.types import MemoryItem
from core.observability.logging import get_logger
from core.utils.concurrency import bounded_gather

logger = get_logger(__name__)


async def add_items(
    provider: Any, items: list[MemoryItem], *, fanout_limit: int
) -> None:
    """Write ``items`` through the provider's batch API when it has one.

    Falls back to a bounded fan-out of single ``add`` calls for providers that
    only implement the item-at-a-time protocol.
    """
    if not items:
        return
    add_many = getattr(provider, "add_many", None)
    if callable(add_many):
        await add_many(items)
        return
    await bounded_gather(
        (provider.add(item) for item in items),
        limit=fanout_limit,
    )


__all__ = ["add_items"]
