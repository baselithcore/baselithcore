"""
Reranking Utilities.

Provides hit reranking with cross-encoder models and optional caching.
Migrated from app/chat/reranking.py
"""

from __future__ import annotations

from core.observability.logging import get_logger
from typing import Any, List, Optional, Protocol, Sequence, Tuple

from core.cache.protocols import AnyCache as CacheProtocol
from core.interfaces import ScoreRerankerProtocol

logger = get_logger(__name__)

HitType = Any
RankedHit = Tuple[HitType, float]

# Alias for backward compatibility within this module
RerankerProtocol = ScoreRerankerProtocol


class MetricsProtocol(Protocol):
    """Protocol for metrics collection."""

    def inc(self) -> None:
        """Increment counter."""
        ...

    def time(self) -> Any:
        """Context manager for timing."""
        ...


class TelemetryProtocol(Protocol):
    """Protocol for telemetry."""

    def increment(self, key: str) -> None:
        """Increment telemetry counter."""
        ...


# Null implementations for when dependencies are not available
class NullMetrics:
    """No-op metrics implementation."""

    def inc(self) -> None:
        """No-op increment."""
        pass

    def time(self):
        """No-op timing context manager."""
        from contextlib import nullcontext

        return nullcontext()


class NullTelemetry:
    """No-op telemetry implementation."""

    def increment(self, key: str) -> None:
        """No-op telemetry increment."""
        pass


_null_metrics = NullMetrics()
_null_telemetry = NullTelemetry()


def _build_cache_key(
    normalized_query: str,
    payload: dict,
    hit_id: Any,
) -> Optional[Tuple[str, str, str, str]]:
    """Build cache key for a hit."""
    fingerprint = payload.get("fingerprint")
    if not isinstance(fingerprint, str) or not fingerprint:
        return None

    chunk_index = payload.get("chunk_index")
    document_id = payload.get("document_id") or hit_id

    return (
        normalized_query,
        fingerprint,
        str(chunk_index) if chunk_index is not None else "",
        str(document_id),
    )


async def rerank_hits(
    user_query: str,
    normalized_query: str,
    hits: Sequence[HitType],
    *,
    reranker: RerankerProtocol,
    cache: Optional[CacheProtocol] = None,
    metrics_requests: Optional[MetricsProtocol] = None,
    metrics_latency: Optional[MetricsProtocol] = None,
    metrics_cache_hit: Optional[MetricsProtocol] = None,
    metrics_cache_miss: Optional[MetricsProtocol] = None,
    telemetry: Optional[TelemetryProtocol] = None,
) -> List[RankedHit]:
    """
    Rerank hits using a cross-encoder model.

    Args:
        user_query: Original user query
        normalized_query: Normalized query for caching
        hits: List of hits to rerank
        reranker: Cross-encoder model
        cache: Optional cache for scores
        metrics_*: Optional Prometheus metrics
        telemetry: Optional telemetry tracker

    Returns:
        List of (hit, score) tuples sorted by score descending
    """
    # Use null implementations if not provided
    m_requests = metrics_requests or _null_metrics
    m_latency = metrics_latency or _null_metrics
    m_cache_hit = metrics_cache_hit or _null_metrics
    m_cache_miss = metrics_cache_miss or _null_metrics
    telem = telemetry or _null_telemetry

    m_requests.inc()
    rerank_entries: List[Optional[RankedHit]] = [None] * len(hits)
    uncached_pairs: List[Tuple[str, str]] = []
    uncached_meta: List[Tuple[int, HitType, Optional[Tuple[str, str, str, str]]]] = []

    for idx, hit in enumerate(hits):
        payload = getattr(hit, "payload", None) or {}
        raw_chunk_text = payload.get("text") or ""
        chunk_text = raw_chunk_text.strip()
        cache_key = None
        cached_score = None

        if cache is not None and chunk_text:
            cache_key = _build_cache_key(
                normalized_query, payload, getattr(hit, "id", "")
            )
            if cache_key is not None:
                cached_score = await cache.get(cache_key)

        if cached_score is not None:
            rerank_entries[idx] = (hit, cached_score)
            telem.increment("rerank_cache.hit")
            m_cache_hit.inc()
            continue

        uncached_pairs.append((user_query, raw_chunk_text))
        uncached_meta.append((idx, hit, cache_key))

    if uncached_pairs:
        with m_latency.time():
            predicted_scores = reranker.predict(uncached_pairs).tolist()
        for (idx, hit, cache_key), score in zip(uncached_meta, predicted_scores):
            if cache_key and cache is not None:
                await cache.set(cache_key, score)
                telem.increment("rerank_cache.write")
            telem.increment("rerank_cache.miss")
            m_cache_miss.inc()
            rerank_entries[idx] = (hit, score)

    ranked_hits = [entry for entry in rerank_entries if entry is not None]
    if not ranked_hits:
        ranked_hits = [(hit, 0.0) for hit in hits]

    ranked_hits = sorted(ranked_hits, key=lambda item: item[1], reverse=True)
    return ranked_hits


__all__ = [
    "rerank_hits",
    "RankedHit",
    "HitType",
    "CacheProtocol",
    "RerankerProtocol",
    "MetricsProtocol",
    "TelemetryProtocol",
]
