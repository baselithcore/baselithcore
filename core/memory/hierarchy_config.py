"""Tier definitions and configuration for hierarchical memory.

Split out of ``hierarchy.py`` to respect the module size cap; all names are
re-exported from :mod:`core.memory.hierarchy` for backward compatibility.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class MemoryTier(Enum):
    """Memory storage tiers in the hierarchy."""

    STM = "short_term"  # Working memory, in-context
    MTM = "mid_term"  # Topic-segmented, summarized
    LTM = "long_term"  # Compressed, provider-backed


@dataclass
class TierConfig:
    """Configuration for a memory tier."""

    max_items: int
    auto_promote_threshold: float = 0.5  # Importance threshold for promotion
    ttl_seconds: int | None = None  # Time-to-live before eviction


@dataclass
class HierarchyConfig:
    """Configuration for the hierarchical memory system."""

    stm: TierConfig = field(default_factory=lambda: TierConfig(max_items=10))
    mtm: TierConfig = field(
        default_factory=lambda: TierConfig(max_items=50, ttl_seconds=86400)
    )
    ltm: TierConfig = field(
        default_factory=lambda: TierConfig(max_items=500, ttl_seconds=604800)
    )
    auto_consolidate: bool = True  # Automatically consolidate on overflow


@dataclass
class TierStats:
    """Statistics for a memory tier."""

    tier: MemoryTier
    item_count: int
    capacity: int
    oldest_item_age_seconds: float | None = None
    avg_importance: float = 0.0


__all__ = ["HierarchyConfig", "MemoryTier", "TierConfig", "TierStats"]
