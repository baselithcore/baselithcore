"""
NLP Models

Provides embedder and reranker model loading with caching.
"""

from __future__ import annotations

import hashlib
from core.observability.logging import get_logger
from functools import lru_cache
from typing import Any, List, Optional, Union

import numpy as np
from sentence_transformers import SentenceTransformer, CrossEncoder  # type: ignore[import-untyped]

from core.cache import TTLCache, RedisTTLCache, create_redis_client
from core.config import get_chat_config, get_storage_config, get_vectorstore_config

logger = get_logger(__name__)


class CachedEmbedder:
    """
    Wrapper around SentenceTransformer with caching capabilities.

    Caches embeddings to reduce computation for repeated texts.

    Example:
        ```python
        embedder = get_embedder()
        embedding = embedder.encode("Hello world")
        ```
    """

    def __init__(
        self,
        model: SentenceTransformer,
        cache: Optional[Union[TTLCache, RedisTTLCache]] = None,
        semantic_cache: Optional[Any] = None,
        cache_backend: str = "memory",
        redis_url: Optional[str] = None,
        redis_prefix: str = "cache",
        cache_ttl: int = 3600,
    ):
        """
        Initialize cached embedder.

        Args:
            model: SentenceTransformer model instance
            cache: Optional external cache instance
            semantic_cache: Optional semantic cache instance
            cache_backend: Cache backend type ("redis" or "memory")
            redis_url: Redis connection URL (required if backend is redis)
            redis_prefix: Prefix for redis keys
            cache_ttl: Cache TTL in seconds
        """
        self.model = model
        self.semantic_cache = semantic_cache
        self._cache = cache

        if self._cache is None:
            try:
                if cache_backend == "redis" and redis_url:
                    redis_client = create_redis_client(redis_url)
                    self._cache = RedisTTLCache(
                        redis_client,
                        prefix=f"{redis_prefix}:embed:{model.get_sentence_embedding_dimension()}",
                        default_ttl=cache_ttl,
                    )
                else:
                    self._cache = TTLCache(maxsize=10000, ttl=cache_ttl)
            except Exception as e:
                logger.warning(f"[embedder] Failed to initialize cache: {e}")

    async def encode(
        self, sentences: Union[str, List[str]], **kwargs: Any
    ) -> Union[List[float], np.ndarray, List[np.ndarray]]:
        """
        Encode sentences to embeddings with caching (async).

        Args:
            sentences: Text or list of texts to encode
            **kwargs: Additional arguments for SentenceTransformer.encode

        Returns:
            Embedding(s) as numpy array(s)
        """
        # Passthrough if cache disabled
        if not self._cache:
            # Run blocking encode in executor to keep it async-friendly
            import asyncio

            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                None, lambda: self.model.encode(sentences, **kwargs)
            )

        is_single = isinstance(sentences, str)
        inputs: List[str] = [sentences] if is_single else list(sentences)  # type: ignore[list-item]

        # 1. Identify hashes
        hashes: List[str] = [
            hashlib.sha256(text.encode("utf-8")).hexdigest() for text in inputs
        ]

        # 2. Check cache
        results: List[Any] = [None] * len(inputs)
        missing_indices = []
        missing_texts = []

        for idx, h in enumerate(hashes):
            # 2a. Check semantic cache first (if enabled)
            if self.semantic_cache is not None:
                # Use Any cast since semantic_cache has extra methods beyond CacheProtocol
                from typing import Any as TypeAny
                from typing import cast

                semantic_cached = await cast(TypeAny, self.semantic_cache).get_similar(
                    inputs[idx]
                )
                if semantic_cached is not None:
                    results[idx] = semantic_cached
                    continue  # Skip regular cache check if semantic cache hit

            # 2b. Check regular cache
            cached_val = await self._cache.get(h)
            if cached_val is not None:
                results[idx] = cached_val
            else:
                missing_indices.append(idx)
                missing_texts.append(inputs[idx])

        # 3. Compute missing
        if missing_texts:
            # Blocking model call in executor
            import asyncio

            loop = asyncio.get_running_loop()
            embeddings = await loop.run_in_executor(
                None, lambda: self.model.encode(missing_texts, **kwargs)
            )

            # 4. Update cache
            for i, emb in enumerate(embeddings):
                real_idx = missing_indices[i]
                results[real_idx] = emb
                await self._cache.set(hashes[real_idx], emb)

        # 5. Format Output
        final_results: Any = results

        if kwargs.get("convert_to_numpy", True):
            final_results = np.array(results)

        if is_single:
            return final_results[0]  # type: ignore[return-value]

        return final_results  # type: ignore[return-value]

    def __getattr__(self, name: str) -> Any:
        """Delegate other calls to model."""
        return getattr(self.model, name)


@lru_cache(maxsize=None)
def get_embedder(model_name: Optional[str] = None) -> CachedEmbedder:
    """
    Get cached embedder instance.

    Args:
        model_name: Name of the SentenceTransformer model (optional, defaults to config)

    Returns:
        CachedEmbedder instance
    """
    vs_config = get_vectorstore_config()
    storage_config = get_storage_config()

    actual_model_name = model_name or vs_config.embedding_model
    base_model = SentenceTransformer(actual_model_name)

    return CachedEmbedder(
        base_model,
        cache_backend=storage_config.cache_backend,
        redis_url=storage_config.cache_redis_url,
        redis_prefix=storage_config.cache_redis_prefix,
        cache_ttl=vs_config.embedding_cache_ttl,
    )


@lru_cache(maxsize=None)
def get_reranker(model_name: Optional[str] = None) -> CrossEncoder:
    """
    Get cached reranker instance.

    Args:
        model_name: Name of the CrossEncoder model (optional, defaults to config)

    Returns:
        CrossEncoder instance
    """
    chat_config = get_chat_config()
    actual_model_name = model_name or chat_config.reranker_model
    return CrossEncoder(actual_model_name)


__all__ = ["get_embedder", "get_reranker", "CachedEmbedder"]
