"""
Caching infrastructure for optimization.

Provides Redis-based caching and Semantic Caching capabilities to reduce
latency and LLM costs.
"""

import json
from core.observability.logging import get_logger
import hashlib
from typing import TYPE_CHECKING, Any, Optional

from core.cache.protocols import TTLCacheProtocol
from core.config.storage import get_storage_config
from core.context import get_current_tenant_id

if TYPE_CHECKING:
    from core.services.vectorstore.service import VectorStoreService

try:
    import redis.asyncio as redis
except ImportError:
    redis = None  # type: ignore

logger = get_logger(__name__)

# Alias for backward compatibility - RedisCache implements this
CacheABC = TTLCacheProtocol


class RedisCache:
    """
    Redis-based cache implementation.

    Uses the global storage configuration for connection details.
    """

    def __init__(self, prefix: str = "cache"):
        self.config = get_storage_config()
        self.prefix = prefix
        self._client: Optional["redis.Redis"] = None
        self._enabled = False

        if not redis:
            logger.warning("Redis python client not installed. RedisCache disabled.")
            return

        try:
            # Create client from URL
            # decode_responses=True ensures we get strings back, not bytes
            self._client = redis.from_url(
                self.config.cache_redis_url, decode_responses=True
            )
            # Async init check usually needs await, can't verify in __init__ easily without async factory
            # We assume it works or fails on first call
            self._enabled = True
            logger.info(f"RedisCache initialized with prefix '{prefix}'")
        except Exception as e:
            logger.warning(f"Failed to connect to Redis: {e}. RedisCache disabled.")
            self._client = None

    def _make_key(self, key: str) -> str:
        tenant_id = get_current_tenant_id()
        return f"{self.config.cache_redis_prefix}:{tenant_id}:{self.prefix}:{key}"

    async def get(self, key: str) -> Optional[Any]:
        """
        Retrieve a value from the cache.

        Args:
            key: The unique key for the cached item.

        Returns:
            The decrypted/deserialized value if found and valid, else None.
        """
        if not self._enabled or not self._client:
            return None

        try:
            full_key = self._make_key(key)
            data = await self._client.get(full_key)
            if data:
                try:
                    return json.loads(str(data))
                except json.JSONDecodeError:
                    return data
            return None
        except Exception as e:
            logger.warning(f"Redis get error for key {key}: {e}")
            return None

    async def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        """
        Store a value in the cache with an optional TTL.

        Args:
            key: The unique key for the cached item.
            value: The data to store (will be serialized to JSON).
            ttl: Optional time-to-live in seconds.
        """
        if not self._enabled or not self._client:
            return

        try:
            full_key = self._make_key(key)
            # Serialize complex objects to JSON
            if isinstance(value, (dict, list, bool, int, float)):
                serialized = json.dumps(value)
            else:
                serialized = str(value)

            await self._client.set(full_key, serialized, ex=ttl)
        except Exception as e:
            logger.warning(f"Redis set error for key {key}: {e}")

    async def delete(self, key: str) -> None:
        """
        Remove a value from the cache.

        Args:
            key: The unique key for the cached item.
        """
        if not self._enabled or not self._client:
            return

        try:
            full_key = self._make_key(key)
            await self._client.delete(full_key)
        except Exception as e:
            logger.warning(f"Redis delete error for key {key}: {e}")


class SemanticCache:
    """
    Semantic Cache for LLM responses.

    Currently implements exact match caching on prompt hash.
    Future versions will implement semantic similarity search using vector embeddings.
    """

    def __init__(self, ttl: int = 3600):
        self.cache = RedisCache(prefix="semantic")
        self.ttl = ttl

    def _hash_prompt(self, prompt: str, **kwargs) -> str:
        """Create a deterministic hash from prompt and parameters."""
        # Sort kwargs to ensure deterministic hash
        params_str = json.dumps(kwargs, sort_keys=True)
        content = f"{prompt}|{params_str}"
        return hashlib.sha256(content.encode()).hexdigest()

    async def get_response(self, prompt: str, **kwargs) -> Optional[str]:
        """Retrieve cached response if available."""
        key = self._hash_prompt(prompt, **kwargs)
        return await self.cache.get(key)

    async def cache_response(self, prompt: str, response: str, **kwargs) -> None:
        """Store response in cache."""
        key = self._hash_prompt(prompt, **kwargs)
        await self.cache.set(key, response, ttl=self.ttl)


class SemanticCacheVectorBacked(SemanticCache):
    """
    Enhanced Semantic Cache with vector similarity search.

    Uses embeddings to find semantically similar cached prompts,
    allowing cache hits even when prompts are slightly different
    but semantically equivalent.

    Falls back to exact hash matching when no embedder is configured.

    Example:
        ```python
        from sentence_transformers import SentenceTransformer
        embedder = SentenceTransformer('all-MiniLM-L6-v2')
        cache = SemanticCacheVectorBacked(embedder=embedder)

        # First call - cache miss
        response = await cache.get_response_semantic("What is Python?")

        # Second call with similar prompt - cache hit!
        response = await cache.get_response_semantic("Tell me about Python")
        ```
    """

    CACHE_COLLECTION_NAME = "semantic_cache"

    def __init__(
        self,
        ttl: int = 3600,
        embedder=None,
        similarity_threshold: float = 0.85,
        collection_name: str | None = None,
    ):
        """
        Initialize vector-backed semantic cache.

        Args:
            ttl: Time-to-live for cached entries in seconds
            embedder: Embedder with encode() method for generating vectors.
                      If None, falls back to hash-based caching.
            similarity_threshold: Minimum similarity score (0-1) to consider
                                  a cache hit (default: 0.85)
            collection_name: Vector collection name (default: "semantic_cache")
        """
        super().__init__(ttl=ttl)
        self.embedder = embedder
        self.similarity_threshold = similarity_threshold
        self.collection_name = collection_name or self.CACHE_COLLECTION_NAME

        # Lazy-load vector store to avoid circular imports
        self._vector_service: Optional["VectorStoreService"] = None

    @property
    def vector_service(self):
        """Lazy load vector store service."""
        if self._vector_service is None:
            try:
                from core.services.vectorstore.service import get_vectorstore_service

                self._vector_service = get_vectorstore_service()
                # Ensure collection exists logic moved to runtime/background
                # Not doing create_collection here to avoid async property issues
            except ImportError:
                logger.warning("VectorStoreService not available for semantic cache")
        return self._vector_service

    async def _ensure_collection(self):
        """Ensure the vector collection exists."""
        if self.vector_service:
            try:
                # We need to await this
                await self.vector_service.create_collection(
                    collection_name=self.collection_name
                )
            except Exception:
                pass  # nosec B110 - Collection may already exist

    async def get_response(self, prompt: str, **kwargs) -> Optional[str]:
        """
        Override to use semantic search when available.

        This allows LLMService to use the same interface regardless of
        whether using SemanticCache or SemanticCacheVectorBacked.
        """
        return await self.get_response_semantic(prompt, **kwargs)

    async def cache_response(self, prompt: str, response: str, **kwargs) -> None:
        """
        Override to cache with embedding when available.

        This allows LLMService to use the same interface regardless of
        whether using SemanticCache or SemanticCacheVectorBacked.
        """
        await self.cache_response_with_embedding(prompt, response, **kwargs)

    async def get_response_semantic(self, prompt: str, **kwargs) -> Optional[str]:
        """
        Search for semantically similar cached prompts.

        Args:
            prompt: The prompt to search for
            **kwargs: Additional parameters (used for hash fallback)

        Returns:
            Cached response if similar prompt found, None otherwise
        """
        # Fallback to hash-based if no embedder
        if not self.embedder:
            return await super().get_response(prompt, **kwargs)

        if not self.vector_service:
            logger.debug("No vector service, falling back to hash-based cache")
            return await super().get_response(prompt, **kwargs)

        try:
            # Generate embedding for the query
            # embeddings are usually sync cpu bound
            query_vector = self.embedder.encode(prompt)
            if hasattr(query_vector, "tolist"):
                query_vector = query_vector.tolist()

            # Search for similar prompts
            results = await self.vector_service.search(
                query_vector=query_vector,
                k=1,
                collection_name=self.collection_name,
                use_cache=False,  # Don't cache search results
            )

            if results and len(results) > 0:
                top_result = results[0]
                if top_result.score >= self.similarity_threshold:
                    # Get the response from Redis cache using stored hash
                    prompt_hash = top_result.document.metadata.get("prompt_hash")
                    if prompt_hash:
                        cached_response = await self.cache.get(prompt_hash)
                        if cached_response:
                            logger.debug(
                                f"Semantic cache hit with score {top_result.score:.3f}"
                            )
                            return cached_response

            logger.debug("Semantic cache miss")
            return None

        except Exception as e:
            logger.warning(f"Semantic cache search failed: {e}")
            return await super().get_response(prompt, **kwargs)

    async def cache_response_with_embedding(
        self, prompt: str, response: str, **kwargs
    ) -> None:
        """
        Cache response with its embedding for semantic search.

        Args:
            prompt: The prompt to cache
            response: The response to cache
            **kwargs: Additional parameters for hash key
        """
        # Always store in Redis with hash key
        prompt_hash = self._hash_prompt(prompt, **kwargs)
        await self.cache.set(prompt_hash, response, ttl=self.ttl)

        # Also index embedding for semantic search
        if not self.embedder or not self.vector_service:
            return

        try:
            # Ensure collection exists
            await self._ensure_collection()

            # Generate embedding
            embedding = self.embedder.encode(prompt)
            if hasattr(embedding, "tolist"):
                embedding = embedding.tolist()

            # Index the embedding (we use low-level upsert)
            await self.vector_service.provider.upsert(
                collection_name=self.collection_name,
                points=[
                    {
                        "id": prompt_hash,
                        "vector": embedding,
                        "payload": {
                            "prompt": prompt[:500],  # Truncate for storage
                            "prompt_hash": prompt_hash,
                        },
                    }
                ],
            )
            logger.debug(f"Cached response with embedding (hash={prompt_hash[:8]}...)")

        except Exception as e:
            logger.warning(f"Failed to index embedding for semantic cache: {e}")


# Global instances (lazy loaded pattern could be applied here if needed)
_semantic_cache: Optional[SemanticCache] = None
_semantic_cache_vector: Optional[SemanticCacheVectorBacked] = None


def get_semantic_cache(vector_backed: bool = False) -> SemanticCache:
    """
    Get or create a semantic cache instance.

    Args:
        vector_backed: If True, returns vector-backed semantic cache
                       (requires embedder to be configured separately)

    Returns:
        SemanticCache or SemanticCacheVectorBacked instance
    """
    global _semantic_cache, _semantic_cache_vector

    if vector_backed:
        if _semantic_cache_vector is None:
            _semantic_cache_vector = SemanticCacheVectorBacked()
        return _semantic_cache_vector

    if _semantic_cache is None:
        _semantic_cache = SemanticCache()
    return _semantic_cache
