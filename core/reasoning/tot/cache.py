"""
Thought Evaluation Cache.

Optimizes reasoning performance by caching the results of expensive LLM
evaluations. Prevents redundant scoring for semantically identical
thoughts within the same problem context (ToT pattern).
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from threading import Lock
from typing import Optional

from core.observability.logging import get_logger

logger = get_logger(__name__)


@dataclass
class ThoughtCacheEntry:
    """A cached thought evaluation."""

    thought: str
    score: float
    context_hash: str  # Hash of problem context
    timestamp: float = field(default_factory=time.time)
    hits: int = 0


class ThoughtCache:
    """

    # Retrieve (context-aware)
    score = cache.get("Break problem into steps", problem="Solve equation")
    # Returns 0.8

    # Different context returns None
    score = cache.get("Break problem into steps", problem="Write code")
    # Returns None
    ```
    """

    def __init__(
        self,
        *,
        maxsize: int = 1000,
        ttl: float = 1800.0,  # 30 minutes
    ) -> None:
        """
        Initialize ThoughtCache.

        Args:
            maxsize: Maximum number of entries
            ttl: Time-to-live in seconds
        """
        if maxsize <= 0:
            raise ValueError("maxsize must be positive")
        if ttl <= 0:
            raise ValueError("ttl must be positive")

        self._maxsize = maxsize
        self._ttl = ttl
        self._entries: dict[str, ThoughtCacheEntry] = {}
        self._lock = Lock()

        # Stats
        self._hits = 0
        self._misses = 0

        logger.debug(f"ThoughtCache initialized: maxsize={maxsize}, ttl={ttl}")

    def _hash_key(self, thought: str, context: str = "") -> str:
        """Generate cache key from thought and context."""
        combined = f"{context}::{thought}"
        return hashlib.sha256(combined.encode()).hexdigest()[:24]

    def _context_hash(self, problem: str) -> str:
        """Hash the problem context."""
        return hashlib.md5(problem.encode(), usedforsecurity=False).hexdigest()[:16]

    def _purge_expired(self) -> None:
        """Remove expired entries."""
        now = time.time()
        expired = [
            key
            for key, entry in self._entries.items()
            if (now - entry.timestamp) > self._ttl
        ]
        for key in expired:
            del self._entries[key]

    def _evict_lru(self) -> None:
        """Evict least recently used entry if at capacity."""
        if len(self._entries) >= self._maxsize:
            oldest_key = min(
                self._entries.keys(),
                key=lambda k: (self._entries[k].timestamp, -self._entries[k].hits),
            )
            del self._entries[oldest_key]

    def set(
        self,
        thought: str,
        score: float,
        problem: str = "",
    ) -> None:
        """
        Cache a thought evaluation.

        Args:
            thought: The thought content
            score: Evaluation score (0.0-1.0)
            problem: Problem context for context-aware caching
        """
        with self._lock:
            self._purge_expired()
            self._evict_lru()

            context_hash = self._context_hash(problem)
            key = self._hash_key(thought, context_hash)

            self._entries[key] = ThoughtCacheEntry(
                thought=thought,
                score=score,
                context_hash=context_hash,
            )

    def get(
        self,
        thought: str,
        problem: str = "",
    ) -> Optional[float]:
        """
        Get cached evaluation score.

        Args:
            thought: The thought content
            problem: Problem context

        Returns:
            Cached score or None if not found
        """
        with self._lock:
            context_hash = self._context_hash(problem)
            key = self._hash_key(thought, context_hash)

            entry = self._entries.get(key)

            if entry is None:
                self._misses += 1
                return None

            # Check TTL
            if (time.time() - entry.timestamp) > self._ttl:
                del self._entries[key]
                self._misses += 1
                return None

            # Update access
            entry.hits += 1
            entry.timestamp = time.time()
            self._hits += 1

            return entry.score

    def get_or_evaluate(
        self,
        thought: str,
        problem: str,
        evaluator,
    ) -> float:
        """
        Get cached score or evaluate and cache.

        Args:
            thought: The thought content
            problem: Problem context
            evaluator: Callable that returns score for thought

        Returns:
            Evaluation score (cached or fresh)
        """
        cached = self.get(thought, problem)
        if cached is not None:
            return cached

        # Evaluate and cache
        score = evaluator(thought)
        self.set(thought, score, problem)
        return score

    async def get_or_evaluate_async(
        self,
        thought: str,
        problem: str,
        evaluator,
    ) -> float:
        """
        Async version of get_or_evaluate.

        Args:
            thought: The thought content
            problem: Problem context
            evaluator: Async callable that returns score for thought

        Returns:
            Evaluation score (cached or fresh)
        """
        cached = self.get(thought, problem)
        if cached is not None:
            logger.debug(f"ThoughtCache hit for: '{thought[:30]}...'")
            return cached

        # Evaluate and cache
        score = await evaluator(thought)
        self.set(thought, score, problem)
        return score

    def clear(self) -> None:
        """Clear all cache entries."""
        with self._lock:
            self._entries.clear()

    @property
    def stats(self) -> dict:
        """Get cache statistics."""
        with self._lock:
            total = self._hits + self._misses
            hit_rate = (self._hits / total * 100) if total > 0 else 0.0
            return {
                "size": len(self._entries),
                "maxsize": self._maxsize,
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": f"{hit_rate:.1f}%",
            }

    def __len__(self) -> int:
        """Return number of cached entries."""
        with self._lock:
            return len(self._entries)

    def __repr__(self) -> str:
        return f"<ThoughtCache size={len(self)} maxsize={self._maxsize}>"


# Global instance for shared caching across ToT instances
_global_thought_cache: Optional[ThoughtCache] = None


def get_thought_cache(
    maxsize: Optional[int] = None,
    ttl: Optional[float] = None,
) -> ThoughtCache:
    """
    Get or create the global thought cache.

    Defaults are loaded from ReasoningConfig if not explicitly provided.

    Args:
        maxsize: Maximum entries (only used on first call)
        ttl: TTL in seconds (only used on first call)

    Returns:
        Global ThoughtCache instance
    """
    global _global_thought_cache
    if _global_thought_cache is None:
        # Load defaults from config if not provided
        if maxsize is None or ttl is None:
            try:
                from core.config import get_reasoning_config

                config = get_reasoning_config()
                if maxsize is None:
                    maxsize = config.thought_cache_maxsize
                if ttl is None:
                    ttl = config.thought_cache_ttl
            except ImportError:
                # Fallback to hardcoded defaults
                maxsize = maxsize or 1000
                ttl = ttl or 1800.0

        _global_thought_cache = ThoughtCache(maxsize=maxsize, ttl=ttl)
    return _global_thought_cache
