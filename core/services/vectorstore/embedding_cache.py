"""
Embedding Cache for VectorStore.

Extracted from service.py to keep per-file LOC under 500.
Provides cached embedding generation with Redis backing.
"""

import hashlib
import inspect
from typing import Any, Protocol, runtime_checkable

from core.observability.logging import get_logger

logger = get_logger(__name__)


def _supports_batch_get(cache: Any) -> bool:
    return callable(getattr(type(cache), "get_many", None))


def _supports_batch_set(cache: Any) -> bool:
    return callable(getattr(type(cache), "set_many", None))


async def _encode_texts(embedder: "EmbedderProtocol", texts: list[str]) -> list[Any]:
    vectors = embedder.encode(texts, convert_to_numpy=True)
    if inspect.isawaitable(vectors):
        vectors = await vectors
    if hasattr(vectors, "tolist"):
        return vectors.tolist()
    return vectors


@runtime_checkable
class EmbedderProtocol(Protocol):
    """Protocol for Embedder used in VectorStore."""

    def encode(
        self,
        sentences: str | list[str],
        batch_size: int = 32,
        show_progress_bar: bool | None = None,
        output_value: str = "sentence_embedding",
        convert_to_numpy: bool = True,
        convert_to_tensor: bool = False,
        device: str | None = None,
        normalize_embeddings: bool = False,
    ) -> Any:
        """
        Encode sentences into embeddings.

        Args:
            sentences: Single string or list of strings to encode.
            batch_size: Size of batches for processing.
            show_progress_bar: Whether to display a progress indicator.
            output_value: The type of output (default 'sentence_embedding').
            convert_to_numpy: Whether to return numpy arrays.
            convert_to_tensor: Whether to return torch tensors.
            device: Computation device (e.g., 'cpu', 'cuda').
            normalize_embeddings: Whether to normalize output vectors.

        Returns:
            Computed embeddings.
        """
        ...


def _cache_key(text: str, model_id: str) -> str:
    """Build a cache key scoped to both text content and model."""
    raw = f"{model_id}:{text}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def get_embeddings_cached(
    embedder: EmbedderProtocol,
    texts: list[str],
    cache: Any,
    model_id: str = "",
) -> list[Any]:
    """
    Get embeddings for a list of texts, using cache when available.

    Args:
        embedder: Embedder instance implementing EmbedderProtocol
        texts: List of texts to embed
        cache: Redis cache instance (or None)
        model_id: Embedding model identifier (prevents cross-model collisions)

    Returns:
        List of embedding vectors
    """
    if not cache:
        return await _encode_texts(embedder, texts)

    vectors_map: dict[int, Any] = {}
    missing_indices: list[int] = []
    missing_texts: list[str] = []
    cache_keys = [_cache_key(text, model_id) for text in texts]

    # Check cache
    if _supports_batch_get(cache):
        cached_vectors = await cache.get_many(cache_keys)
        for i, cached_vector in enumerate(cached_vectors):
            if cached_vector is not None:
                vectors_map[i] = cached_vector
            else:
                missing_indices.append(i)
                missing_texts.append(texts[i])
    else:
        for i, key in enumerate(cache_keys):
            cached_vector = await cache.get(key)
            if cached_vector is not None:
                vectors_map[i] = cached_vector
            else:
                missing_indices.append(i)
                missing_texts.append(texts[i])

    # Encode missing
    if missing_texts:
        new_vectors = await _encode_texts(embedder, missing_texts)

        # Update cache and map
        cache_updates: list[tuple[str, Any]] = []
        for i, vector in zip(missing_indices, new_vectors):
            cache_updates.append((cache_keys[i], vector))
            vectors_map[i] = vector

        if cache_updates:
            if _supports_batch_set(cache):
                await cache.set_many(cache_updates)
            else:
                for key, vector in cache_updates:
                    await cache.set(key, vector)

    # Reconstruct order
    return [vectors_map[i] for i in range(len(texts))]
