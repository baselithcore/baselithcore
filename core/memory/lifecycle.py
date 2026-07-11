"""Tier lifecycle policies for hierarchical memory.

Pure policy helpers used by
:class:`~core.memory.hierarchy.HierarchicalMemory` during consolidation and
compression. This is where the declarative :class:`TierConfig` knobs become
behavior:

* ``auto_promote_threshold`` — items leaving STM are **promoted** to MTM only
  when their importance clears the threshold; the rest are evicted (the
  rolling working-set window still moves, but trivia no longer pollutes
  mid-term storage).
* write-side **dedup** — an item whose normalized content already exists in
  the target tier is dropped at promotion time instead of accumulating and
  being re-deduplicated on every recall.
* ``ttl_seconds`` — expired items are swept from a tier during maintenance
  (and via :meth:`HierarchicalMemory.purge_expired`). Kill-switch:
  ``BASELITH_MEMORY_TTL_ENFORCE=false`` restores the legacy
  capacity-only eviction.

Kept out of ``hierarchy.py`` to respect the module size cap and keep the
policies unit-testable without a memory instance.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from core.observability.logging import get_logger

from .hierarchy_search import _normalize_content

if TYPE_CHECKING:
    from .types import MemoryItem

logger = get_logger(__name__)

_TRUTHY = ("1", "true", "yes", "on")


def ttl_enforcement_enabled() -> bool:
    """Whether ``TierConfig.ttl_seconds`` is enforced (default: yes)."""
    return os.getenv("BASELITH_MEMORY_TTL_ENFORCE", "true").lower() in _TRUTHY


def select_promotable(
    candidates: list[tuple[MemoryItem, list[float]]],
    threshold: float,
) -> tuple[list[tuple[MemoryItem, list[float]]], int]:
    """Split items leaving a tier into (promote, evicted_count).

    An item is promoted when its ``importance`` metadata (default 0.5)
    clears *threshold*; anything below is evicted. With the default
    importance and the default threshold (both 0.5) every item still
    promotes, so callers that never set importance see no behavior change.
    """
    promote: list[tuple[MemoryItem, list[float]]] = []
    evicted = 0
    for item, embedding in candidates:
        importance = float(item.metadata.get("importance", 0.5))
        if importance >= threshold:
            promote.append((item, embedding))
        else:
            evicted += 1
    return promote, evicted


def drop_duplicates(
    candidates: list[tuple[MemoryItem, list[float]]],
    existing: list[MemoryItem],
) -> tuple[list[tuple[MemoryItem, list[float]]], int]:
    """Filter out candidates already present in the target tier.

    Presence is judged by whitespace/case-normalized content (the same key
    hybrid recall uses for read-side dedup). Duplicates within the candidate
    batch itself are also collapsed, keeping the first occurrence.
    """
    seen = {_normalize_content(item.content) for item in existing}
    unique: list[tuple[MemoryItem, list[float]]] = []
    dropped = 0
    for item, embedding in candidates:
        key = _normalize_content(item.content)
        if key in seen:
            dropped += 1
            continue
        seen.add(key)
        unique.append((item, embedding))
    return unique, dropped


def is_expired(
    item: MemoryItem, ttl_seconds: int | None, now: datetime | None = None
) -> bool:
    """True when *item* has outlived *ttl_seconds* (False when no TTL)."""
    if ttl_seconds is None:
        return False
    now = now or datetime.now(UTC)
    return (now - item.created_at).total_seconds() > ttl_seconds


def partition_expired(
    items: list[MemoryItem],
    embeddings: list[list[float]] | None,
    ttl_seconds: int | None,
    now: datetime | None = None,
) -> tuple[list[MemoryItem], list[list[float]], int]:
    """Return (alive_items, alive_embeddings, expired_count) for a tier.

    ``embeddings`` may be None for tiers without an embedding cache; an
    empty list is returned in that case so callers can unpack uniformly.
    """
    if ttl_seconds is None or not items:
        return items, embeddings if embeddings is not None else [], 0

    now = now or datetime.now(UTC)
    alive_items: list[MemoryItem] = []
    alive_embeddings: list[list[float]] = []
    expired = 0
    paired = embeddings if embeddings is not None else [[] for _ in items]
    for item, embedding in zip(items, paired):
        if is_expired(item, ttl_seconds, now):
            expired += 1
            continue
        alive_items.append(item)
        alive_embeddings.append(embedding)
    return alive_items, alive_embeddings, expired


async def summarize_items(llm_service: object, items: list[MemoryItem]) -> str | None:
    """Summarize memory items via the LLM, with a concatenation fallback."""
    if not items:
        return None

    contents = [item.content for item in items]

    if llm_service is not None:
        try:
            prompt = (
                "Summarize the following memory fragments into a concise summary:\n\n"
                + "\n".join(f"- {c}" for c in contents)
                + "\n\nProvide a brief, information-dense summary that preserves "
                "key facts."
            )
            return await llm_service.generate_response(prompt)  # type: ignore[attr-defined]
        except Exception as e:
            logger.warning(f"LLM summarization failed: {e}")

    combined = " | ".join(contents)
    if len(combined) > 500:
        combined = combined[:497] + "..."
    return f"[Summary of {len(items)} items]: {combined}"


__all__ = [
    "drop_duplicates",
    "is_expired",
    "partition_expired",
    "select_promotable",
    "summarize_items",
    "ttl_enforcement_enabled",
]
