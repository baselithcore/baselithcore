"""
Cache Metrics Collection System.

Provides tracking and analytics for cache performance across all cache implementations.
Enables data-driven optimization decisions through hit/miss rates, eviction tracking,
and TTL analysis.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Optional
from core.observability.logging import get_logger

logger = get_logger(__name__)


@dataclass
class CacheMetrics:
    """
    Comprehensive cache performance metrics.

    Tracks cache effectiveness through hit/miss rates, evictions,
    and TTL statistics to enable performance optimization.
    """

    # Counter metrics
    hits: int = 0
    misses: int = 0
    sets: int = 0
    deletes: int = 0
    evictions: int = 0

    # TTL metrics
    total_ttl_seconds: float = 0.0
    ttl_count: int = 0

    # Size metrics
    current_size: int = 0
    max_size_seen: int = 0

    # Timing
    created_at: datetime = field(default_factory=datetime.now)
    last_reset_at: Optional[datetime] = None

    @property
    def hit_rate(self) -> float:
        """
        Calculate cache hit rate (0.0 to 1.0).

        Returns:
            Hit rate as decimal (0.0 = no hits, 1.0 = all hits)
        """
        total = self.hits + self.misses
        return self.hits / total if total > 0 else 0.0

    @property
    def miss_rate(self) -> float:
        """
        Calculate cache miss rate (0.0 to 1.0).

        Returns:
            Miss rate as decimal (0.0 = no misses, 1.0 = all misses)
        """
        return 1.0 - self.hit_rate

    @property
    def avg_ttl_seconds(self) -> float:
        """
        Calculate average TTL across all cached items.

        Returns:
            Average TTL in seconds (0.0 if no items with TTL)
        """
        return self.total_ttl_seconds / self.ttl_count if self.ttl_count > 0 else 0.0

    @property
    def total_requests(self) -> int:
        """Total cache access requests (hits + misses)."""
        return self.hits + self.misses

    def record_hit(self) -> None:
        """Increment hit counter."""
        self.hits += 1

    def record_miss(self) -> None:
        """Increment miss counter."""
        self.misses += 1

    def record_set(self, ttl_seconds: Optional[float] = None) -> None:
        """
        Record a cache set operation.

        Args:
            ttl_seconds: Optional TTL for this item
        """
        self.sets += 1
        if ttl_seconds is not None and ttl_seconds > 0:
            self.total_ttl_seconds += ttl_seconds
            self.ttl_count += 1

    def record_delete(self) -> None:
        """Increment delete counter."""
        self.deletes += 1

    def record_eviction(self) -> None:
        """Increment eviction counter (items removed due to size/TTL limits)."""
        self.evictions += 1

    def update_size(self, size: int) -> None:
        """
        Update current cache size and track maximum.

        Args:
            size: Current number of items in cache
        """
        self.current_size = size
        if size > self.max_size_seen:
            self.max_size_seen = size

    def reset(self) -> None:
        """Reset all counters (preserves max_size_seen for historical tracking)."""
        self.hits = 0
        self.misses = 0
        self.sets = 0
        self.deletes = 0
        self.evictions = 0
        self.total_ttl_seconds = 0.0
        self.ttl_count = 0
        self.current_size = 0
        self.last_reset_at = datetime.now()

    def to_dict(self) -> Dict[str, float]:
        """
        Export metrics as dictionary for monitoring/logging.

        Returns:
            Dictionary with all metric values and calculated rates
        """
        return {
            "hits": self.hits,
            "misses": self.misses,
            "sets": self.sets,
            "deletes": self.deletes,
            "evictions": self.evictions,
            "hit_rate": self.hit_rate,
            "miss_rate": self.miss_rate,
            "total_requests": self.total_requests,
            "avg_ttl_seconds": self.avg_ttl_seconds,
            "current_size": self.current_size,
            "max_size_seen": self.max_size_seen,
        }

    def __str__(self) -> str:
        """Human-readable metrics summary."""
        return (
            f"CacheMetrics(hit_rate={self.hit_rate:.2%}, "
            f"requests={self.total_requests}, "
            f"size={self.current_size}/{self.max_size_seen})"
        )


class CacheMetricsCollector:
    """
    Global metrics collector for all cache instances.

    Aggregates metrics from multiple cache implementations
    to provide system-wide cache performance insights.
    """

    def __init__(self):
        """Initialize metrics collector with empty registry."""
        self._metrics: Dict[str, CacheMetrics] = {}
        logger.debug("CacheMetricsCollector initialized")

    def get_or_create_metrics(self, cache_name: str) -> CacheMetrics:
        """
        Get metrics for a named cache, creating if doesn't exist.

        Args:
            cache_name: Identifier for the cache instance

        Returns:
            CacheMetrics instance for this cache
        """
        if cache_name not in self._metrics:
            self._metrics[cache_name] = CacheMetrics()
            logger.debug(f"Created metrics for cache '{cache_name}'")
        return self._metrics[cache_name]

    def get_metrics(self, cache_name: str) -> Optional[CacheMetrics]:
        """
        Get metrics for a named cache without creating.

        Args:
            cache_name: Identifier for the cache instance

        Returns:
            CacheMetrics if exists, None otherwise
        """
        return self._metrics.get(cache_name)

    def reset_metrics(self, cache_name: str) -> None:
        """
        Reset metrics for a specific cache.

        Args:
            cache_name: Identifier for the cache instance
        """
        if cache_name in self._metrics:
            self._metrics[cache_name].reset()
            logger.info(f"Reset metrics for cache '{cache_name}'")

    def reset_all(self) -> None:
        """Reset metrics for all caches."""
        for cache_name, metrics in self._metrics.items():
            metrics.reset()
        logger.info(f"Reset metrics for {len(self._metrics)} caches")

    def get_all_metrics(self) -> Dict[str, Dict[str, float]]:
        """
        Get metrics for all registered caches.

        Returns:
            Dictionary mapping cache names to their metrics dictionaries
        """
        return {name: metrics.to_dict() for name, metrics in self._metrics.items()}

    def get_summary(self) -> Dict[str, float]:
        """
        Get aggregated summary across all caches.

        Returns:
            Dictionary with system-wide cache statistics
        """
        total_hits = sum(m.hits for m in self._metrics.values())
        total_misses = sum(m.misses for m in self._metrics.values())
        total_requests = total_hits + total_misses

        return {
            "total_caches": len(self._metrics),
            "total_hits": total_hits,
            "total_misses": total_misses,
            "total_requests": total_requests,
            "overall_hit_rate": total_hits / total_requests
            if total_requests > 0
            else 0.0,
            "total_evictions": sum(m.evictions for m in self._metrics.values()),
            "total_size": sum(m.current_size for m in self._metrics.values()),
        }


# Global singleton instance
_global_collector: Optional[CacheMetricsCollector] = None


def get_metrics_collector() -> CacheMetricsCollector:
    """
    Get the global cache metrics collector singleton.

    Returns:
        CacheMetricsCollector instance
    """
    global _global_collector
    if _global_collector is None:
        _global_collector = CacheMetricsCollector()
    return _global_collector
