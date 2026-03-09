"""
Semantic Cache for LLM Responses.

Provides embedding-based similarity lookup to find cached responses
for semantically similar prompts, reducing redundant LLM calls.

Usage:
    from core.cache.semantic_cache import SemanticLLMCache

    cache = SemanticLLMCache()
    await cache.set("What is Python?", "Python is a programming language...")

    # Later, a semantically similar query:
    result = await cache.get_similar("Tell me about Python", threshold=0.85)
    # Returns the cached response if similarity > threshold
"""

from __future__ import annotations

import asyncio
import hashlib
from core.observability.logging import get_logger
import time
from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Optional, Tuple, Dict
from core.context import get_current_tenant_id
from core.utils.similarity import cosine_similarity

import numpy as np

logger = get_logger(__name__)


@dataclass
class CacheEntry:
    """A cached prompt-response pair with embedding."""

    prompt: str
    response: str
    embedding: np.ndarray
    timestamp: float = field(default_factory=time.time)
    hits: int = 0


class SemanticLLMCache:
    """
    Semantic cache for LLM responses using embedding similarity.

    Unlike exact-match caching, this cache finds responses for prompts
    that are semantically similar to cached prompts, reducing LLM calls
    even when prompts are phrased differently.

    Features:
    - Embedding-based similarity matching
    - Configurable similarity threshold
    - TTL expiration
    - LRU eviction when maxsize reached
    - Thread-safe operations
    - Async embedding generation to prevent blocking

    Example:
        ```python
        cache = SemanticLLMCache(threshold=0.85)

        # Cache a response
        await cache.set("What is machine learning?", "ML is a subset of AI...")

        # Query with similar prompt
        result = await cache.get_similar("Explain machine learning")
        # Returns cached response if similarity >= 0.85
        ```
    """

    def __init__(
        self,
        *,
        maxsize: int | None = None,
        ttl: float | None = None,
        threshold: float | None = None,
        embedder: Any = None,
    ) -> None:
        """
        Initialize SemanticLLMCache.

        Args:
            maxsize: Maximum number of entries to cache (per tenant)
            ttl: Time-to-live in seconds for cache entries
            threshold: Minimum cosine similarity for cache hit (0.0-1.0)
            embedder: Embedder instance (creates default if None)
        """
        from core.config.cache import get_semantic_cache_config

        config = get_semantic_cache_config()

        _maxsize = maxsize if maxsize is not None else config.maxsize
        _ttl = ttl if ttl is not None else config.ttl
        _threshold = threshold if threshold is not None else config.threshold

        if _maxsize <= 0:
            raise ValueError("maxsize must be positive")
        if _ttl <= 0:
            raise ValueError("ttl must be positive")
        if not 0.0 <= _threshold <= 1.0:
            raise ValueError("threshold must be between 0.0 and 1.0")

        self._maxsize = _maxsize
        self._ttl = _ttl
        self._threshold = _threshold

        self._entries: Dict[
            str, Dict[str, CacheEntry]
        ] = {}  # Storage: entries[tenant_id][prompt_hash] = CacheEntry
        self._lock = Lock()
        self._embedder: Any = embedder  # Lazy loaded if None

        # Stats
        self._hits = 0
        self._misses = 0

        logger.info(
            f"SemanticCache initialized: maxsize={maxsize}, ttl={ttl}, threshold={threshold}"
        )

    def _get_embedder(self) -> Any:
        """Lazy load the embedder model using config for model selection."""
        if self._embedder is None:
            try:
                from core.nlp import get_embedder
                from core.config import get_voice_config

                model_name = get_voice_config().embedding_model
                self._embedder = get_embedder(model_name)
            except Exception as e:
                logger.warning(f"Failed to load embedder: {e}")
                raise
        return self._embedder

    async def _compute_embedding(self, text: str) -> np.ndarray:
        """Compute embedding for a text asynchronously."""
        loop = asyncio.get_running_loop()

        def _compute():
            embedder = self._get_embedder()
            embedding = embedder.encode(text, convert_to_numpy=True)
            # Normalize for cosine similarity
            norm = np.linalg.norm(embedding)
            if norm > 0:
                embedding = embedding / norm
            return embedding

        return await loop.run_in_executor(None, _compute)

    def _hash_prompt(self, prompt: str, **kwargs) -> str:
        """Generate a hash key for exact match lookup."""
        # Include kwargs in hash for potential future variations (e.g., model_id)
        hash_input = prompt + str(sorted(kwargs.items()))
        return hashlib.sha256(hash_input.encode("utf-8")).hexdigest()[:16]

    def _purge_expired(self, tenant_id: str) -> None:
        """Remove expired entries for a tenant."""
        if tenant_id not in self._entries:
            return

        now = time.time()
        expired = [
            h
            for h, e in self._entries[tenant_id].items()
            if (now - e.timestamp) > self._ttl
        ]
        for h in expired:
            del self._entries[tenant_id][h]

    def _evict_lru(self, tenant_id: str) -> None:
        """Evict least recently used entry for a specific tenant."""
        if tenant_id not in self._entries or not self._entries[tenant_id]:
            return

        # Find entry with oldest timestamp and lowest hits
        oldest_hash = min(
            self._entries[tenant_id].keys(),
            key=lambda k: (
                self._entries[tenant_id][k].timestamp,
                -self._entries[tenant_id][k].hits,
            ),
        )
        del self._entries[tenant_id][oldest_hash]

    async def set(self, prompt: str, response: str, **kwargs) -> None:
        """
        Cache a prompt-response pair.

        Args:
            prompt: The input prompt
            response: The LLM response
        """
        tenant_id = get_current_tenant_id()
        # Compute embedding first (outside lock)
        try:
            embedding = await self._compute_embedding(prompt)
            prompt_hash = self._hash_prompt(prompt, **kwargs)
        except Exception as e:
            logger.warning(f"Failed to compute embedding for cache: {e}")
            return

        with self._lock:
            # Ensure tenant dict exists
            if tenant_id not in self._entries:
                self._entries[tenant_id] = {}

            self._purge_expired(tenant_id)
            if len(self._entries[tenant_id]) >= self._maxsize:
                self._evict_lru(tenant_id)

            self._entries[tenant_id][prompt_hash] = CacheEntry(
                prompt=prompt,
                response=response,
                embedding=embedding,
            )

            logger.debug(
                f"Cached response for prompt: '{prompt[:50]}...' for tenant {tenant_id}"
            )

    async def get_exact(self, prompt: str, **kwargs) -> Optional[str]:
        """
        Get cached response for exact prompt match.

        Args:
            prompt: The input prompt

        Returns:
            Cached response or None
        """
        tenant_id = get_current_tenant_id()
        with self._lock:
            if tenant_id not in self._entries:
                return None

            self._purge_expired(tenant_id)  # Purge before checking

            prompt_hash = self._hash_prompt(prompt, **kwargs)
            entry = self._entries[tenant_id].get(prompt_hash)

            if entry is None:
                return None

            entry.hits += 1
            entry.timestamp = time.time()  # Update access time
            self._hits += 1
            return entry.response

    async def get(self, key: str) -> Optional[str]:
        """Support standard CacheProtocol get (same as get_exact)."""
        return await self.get_exact(key)

    async def delete(self, key: str) -> None:
        """Support standard CacheProtocol delete."""
        tenant_id = get_current_tenant_id()
        with self._lock:
            if tenant_id in self._entries:
                prompt_hash = self._hash_prompt(key)
                self._entries[tenant_id].pop(prompt_hash, None)

    async def get_similar(
        self, prompt: str, threshold: Optional[float] = None, **kwargs
    ) -> Optional[str]:
        """
        Find cached response for semantically similar prompt.

        Args:
            prompt: The input prompt
            threshold: Override default similarity threshold

        Returns:
            Cached response if similar prompt found, else None
        """
        res, _ = await self.get_similar_with_score(prompt, threshold, **kwargs)
        return res

    async def get_similar_with_score(
        self, prompt: str, threshold: Optional[float] = None, **kwargs
    ) -> Tuple[Optional[str], float]:
        """
        Find cached response with similarity score.

        Args:
            prompt: The input prompt
            threshold: Override default similarity threshold

        Returns:
            Tuple of (response or None, similarity score)
        """
        threshold = threshold or self._threshold
        tenant_id = get_current_tenant_id()

        # Check exact match first
        exact = await self.get_exact(prompt, **kwargs)
        if exact:
            return exact, 1.0

        try:
            query_embedding = await self._compute_embedding(prompt)
        except Exception as e:
            logger.warning(f"Failed to compute embedding: {e}")
            with self._lock:
                self._misses += 1
            return None, 0.0

        with self._lock:
            # Explicit check for tenant existence
            if tenant_id not in self._entries:
                self._misses += 1
                return None, 0.0

            self._purge_expired(tenant_id)

            if not self._entries[tenant_id]:
                self._misses += 1
                return None, 0.0

            best_entry: Optional[CacheEntry] = None
            best_similarity: float = 0.0

            for entry in self._entries[tenant_id].values():
                similarity = cosine_similarity(query_embedding, entry.embedding)

                if similarity >= threshold and similarity > best_similarity:
                    best_similarity = similarity
                    best_entry = entry

            if best_entry:
                best_entry.hits += 1
                best_entry.timestamp = time.time()
                self._hits += 1
                logger.info(
                    f"🧠 Semantic cache hit (similarity={best_similarity:.3f}) for tenant {tenant_id}: "
                    f"'{prompt[:30]}...' \u2192 '{best_entry.prompt[:30]}...'"
                )
                return best_entry.response, best_similarity

            self._misses += 1
            return None, best_similarity

    async def clear(self) -> None:
        """Clear all cache entries."""
        with self._lock:
            self._entries.clear()
            logger.info("Semantic cache cleared (all tenants)")

    @property
    def stats(self) -> dict:
        """Get cache statistics."""
        with self._lock:
            total_size = sum(len(t) for t in self._entries.values())
            total = self._hits + self._misses
            hit_rate = (self._hits / total * 100) if total > 0 else 0.0
            return {
                "size": total_size,
                "maxsize": self._maxsize,
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": f"{hit_rate:.1f}%",
            }

    def __len__(self) -> int:
        """Return the number of cached entries."""
        with self._lock:
            return sum(len(t) for t in self._entries.values())
