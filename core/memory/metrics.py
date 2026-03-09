"""
Memory Efficiency Metrics Module.

Provides comprehensive observability for memory operations, tracking
latency, token consumption, and cache effectiveness. This allows for
data-driven optimization of the hierarchical memory system.
"""

from core.observability.logging import get_logger
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = get_logger(__name__)


@dataclass
class MemoryMetrics:
    """
    Aggregated efficiency data for memory orchestration.

    Tracks high-level performance indicators (KPIs) like retrieval
    latency and semantic cache hits to monitor the system's 'cognitive'
    overhead.
    """

    retrieval_latency_ms: float = 0.0
    """Average retrieval latency in milliseconds."""

    tokens_consumed: int = 0
    """Estimated tokens consumed by memory context."""

    cache_hit_rate: float = 0.0
    """Semantic cache hit rate (0-1)."""

    compression_ratio: float = 0.0
    """Memory compression ratio achieved."""

    tier_distribution: Dict[str, int] = field(default_factory=dict)
    """Item count per memory tier."""

    total_retrievals: int = 0
    total_cache_hits: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for logging/export."""
        return {
            "retrieval_latency_ms": round(self.retrieval_latency_ms, 2),
            "tokens_consumed": self.tokens_consumed,
            "cache_hit_rate": round(self.cache_hit_rate, 4),
            "compression_ratio": round(self.compression_ratio, 4),
            "tier_distribution": self.tier_distribution,
            "total_retrievals": self.total_retrievals,
            "total_cache_hits": self.total_cache_hits,
        }


@dataclass
class OperationRecord:
    """Record of a single memory operation."""

    operation: str
    timestamp: datetime
    latency_ms: float
    success: bool
    tokens_estimated: int = 0
    cache_hit: bool = False
    tier: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class MemoryMetricsCollector:
    """
    Observer for memory system performance.

    Collects granular operation records and aggregates them into
    actionable metrics. It is designed for minimal performance
    overhead, operating on a rolling history window.
    """

    # Example:
    #     >>> collector = MemoryMetricsCollector()
    #     >>> with collector.track_operation("recall") as tracker:
    #     ...     results = await memory.recall(query)
    #     ...     tracker.set_tokens(estimate_tokens(results))
    #     >>> metrics = collector.get_metrics()

    def __init__(
        self,
        max_history: int = 1000,
        window_seconds: int = 3600,
    ):
        """
        Initialize metrics collector.

        Args:
            max_history: Maximum operation records to keep
            window_seconds: Time window for metric calculation
        """
        self.max_history = max_history
        self.window_seconds = window_seconds
        self._history: List[OperationRecord] = []
        self._total_retrievals = 0
        self._total_cache_hits = 0

    def track_operation(self, operation: str) -> "OperationTracker":
        """
        Create a tracker for a memory operation.

        Args:
            operation: Operation name (e.g., "recall", "add", "compress")

        Returns:
            OperationTracker context manager
        """
        return OperationTracker(self, operation)

    def record(self, record: OperationRecord) -> None:
        """
        Record an operation.

        Args:
            record: Operation record
        """
        self._history.append(record)

        # Update counters
        if record.operation == "recall":
            self._total_retrievals += 1
            if record.cache_hit:
                self._total_cache_hits += 1

        # Trim history
        if len(self._history) > self.max_history:
            self._history = self._history[-self.max_history :]

    def get_metrics(self) -> MemoryMetrics:
        """
        Calculate current metrics from operation history.

        Returns:
            MemoryMetrics with aggregated values
        """
        if not self._history:
            return MemoryMetrics()

        # Filter to time window
        now = datetime.now(timezone.utc)
        window_records = [
            r
            for r in self._history
            if (now - r.timestamp).total_seconds() <= self.window_seconds
        ]

        if not window_records:
            return MemoryMetrics(
                total_retrievals=self._total_retrievals,
                total_cache_hits=self._total_cache_hits,
            )

        # Calculate averages
        latencies = [r.latency_ms for r in window_records if r.latency_ms > 0]
        avg_latency = sum(latencies) / len(latencies) if latencies else 0.0

        tokens = sum(r.tokens_estimated for r in window_records)

        # Cache hit rate
        recalls = [r for r in window_records if r.operation == "recall"]
        cache_hits = sum(1 for r in recalls if r.cache_hit)
        hit_rate = cache_hits / len(recalls) if recalls else 0.0

        # Tier distribution from latest records
        tier_counts: Dict[str, int] = {}
        for r in window_records:
            if r.tier:
                tier_counts[r.tier] = tier_counts.get(r.tier, 0) + 1

        return MemoryMetrics(
            retrieval_latency_ms=avg_latency,
            tokens_consumed=tokens,
            cache_hit_rate=hit_rate,
            tier_distribution=tier_counts,
            total_retrievals=self._total_retrievals,
            total_cache_hits=self._total_cache_hits,
        )

    def get_latency_percentiles(self) -> Dict[str, float]:
        """Get latency percentiles (p50, p90, p99)."""
        latencies = sorted([r.latency_ms for r in self._history if r.latency_ms > 0])

        if not latencies:
            return {"p50": 0.0, "p90": 0.0, "p99": 0.0}

        def percentile(sorted_list: List[float], p: float) -> float:
            idx = int(len(sorted_list) * p)
            return sorted_list[min(idx, len(sorted_list) - 1)]

        return {
            "p50": percentile(latencies, 0.5),
            "p90": percentile(latencies, 0.9),
            "p99": percentile(latencies, 0.99),
        }

    def reset(self) -> None:
        """Reset all metrics."""
        self._history.clear()
        self._total_retrievals = 0
        self._total_cache_hits = 0


class OperationTracker:
    """Context manager for tracking a memory operation."""

    def __init__(self, collector: MemoryMetricsCollector, operation: str):
        self.collector = collector
        self.operation = operation
        self._start_time: float = 0.0
        self._tokens: int = 0
        self._cache_hit: bool = False
        self._tier: Optional[str] = None
        self._metadata: Dict[str, Any] = {}
        self._success: bool = True

    def __enter__(self) -> "OperationTracker":
        self._start_time = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        elapsed_ms = (time.perf_counter() - self._start_time) * 1000
        self._success = exc_type is None

        record = OperationRecord(
            operation=self.operation,
            timestamp=datetime.now(timezone.utc),
            latency_ms=elapsed_ms,
            success=self._success,
            tokens_estimated=self._tokens,
            cache_hit=self._cache_hit,
            tier=self._tier,
            metadata=self._metadata,
        )
        self.collector.record(record)

    def set_tokens(self, tokens: int) -> None:
        """Set estimated tokens for this operation."""
        self._tokens = tokens

    def set_cache_hit(self, hit: bool) -> None:
        """Mark whether this was a cache hit."""
        self._cache_hit = hit

    def set_tier(self, tier: str) -> None:
        """Set the memory tier used."""
        self._tier = tier

    def add_metadata(self, key: str, value: Any) -> None:
        """Add metadata to the operation record."""
        self._metadata[key] = value
