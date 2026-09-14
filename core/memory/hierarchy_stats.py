"""Tier statistics for :class:`~core.memory.hierarchy.HierarchicalMemory`.

Split out of ``hierarchy.py`` to keep that module under the 500-line cap; the
snapshot is read-only over the tiers, so it sits naturally beside the search
and context mixins rather than inside the store itself.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from .hierarchy_config import MemoryTier, TierStats
from .tenant_state import current_memory_tenant
from .types import MemoryItem

__all__ = ["HierarchyStatsMixin"]


class HierarchyStatsMixin:
    """Per-tier counts, ages and mean importance, cached for a second.

    The tiers read here are the *calling tenant's* (see
    :mod:`core.memory.tenant_state`), so a scrape reports the tenant it runs
    under rather than a process-wide total.
    """

    config: Any
    # Mirrors the declarations on the sibling hierarchy mixins so the
    # concrete store does not see two bases disagreeing about a tier.
    _stm: list[MemoryItem]
    _mtm: list[MemoryItem]
    _ltm: Iterable[MemoryItem]  # deque(maxlen=...) in HierarchicalMemory

    _STATS_CACHE_TTL = 1.0  # seconds — coalesce metrics-scrape bursts

    def get_tier_stats(self) -> list[TierStats]:
        """Get statistics for all tiers.

        LTM holds up to ``ltm.max_items`` (default 500) entries, so the
        ``min(created_at)`` + ``mean(importance)`` pass is O(n). Endpoints
        like ``/metrics`` may scrape this multiple times per second; cache
        the computed snapshot for one second to coalesce bursts.

        The cached snapshot is stamped with the tenant it was computed for and
        only reused for that tenant: the tiers are per-tenant, so a snapshot
        shared across the one-second window would report one tenant's item
        counts and ages to another.

        Returns:
            One :class:`TierStats` per tier, for the calling tenant.
        """
        tenant = current_memory_tenant()
        cached: tuple[str, float, list[TierStats]] | None = getattr(
            self, "_stats_cache", None
        )
        if cached is not None:
            cached_tenant, cached_at, snapshot = cached
            if (
                cached_tenant == tenant
                and time.monotonic() - cached_at < self._STATS_CACHE_TTL
            ):
                return snapshot

        now = datetime.now(UTC)
        tier_map = {
            MemoryTier.STM: "stm",
            MemoryTier.MTM: "mtm",
            MemoryTier.LTM: "ltm",
        }

        def calc_stats(tier: MemoryTier, items: Iterable[MemoryItem]) -> TierStats:
            tier_config = getattr(self.config, tier_map.get(tier, "stm"))

            count = 0
            oldest: datetime | None = None
            importance_sum = 0.0
            for item in items:
                count += 1
                if oldest is None or item.created_at < oldest:
                    oldest = item.created_at
                importance_sum += item.metadata.get("importance", 0.5)

            if count == 0:
                return TierStats(
                    tier=tier,
                    item_count=0,
                    capacity=tier_config.max_items,
                )

            assert oldest is not None  # guaranteed by count > 0
            return TierStats(
                tier=tier,
                item_count=count,
                capacity=tier_config.max_items,
                oldest_item_age_seconds=(now - oldest).total_seconds(),
                avg_importance=importance_sum / count,
            )

        snapshot = [
            calc_stats(MemoryTier.STM, self._stm),
            calc_stats(MemoryTier.MTM, self._mtm),
            calc_stats(MemoryTier.LTM, self._ltm),
        ]
        self._stats_cache: tuple[str, float, list[TierStats]] = (
            tenant,
            time.monotonic(),
            snapshot,
        )
        return snapshot
