"""
Embedding Cache for VectorStore.

Extracted from service.py to keep per-file LOC under 500.
Provides cached embedding generation with Redis backing.
"""

import hashlib
from core.observability.logging import get_logger
from typing import Any, List, Optional, Protocol, runtime_checkable

logger = get_logger(__name__)


@runtime_checkable
class EmbedderProtocol(Protocol):
    """Protocol for Embedder used in VectorStore."""

    def encode(
        self,
        sentences: str | List[str],
        batch_size: int = 32,
        show_progress_bar: Optional[bool] = None,
        output_value: str = "sentence_embedding",
        convert_to_numpy: bool = True,
        convert_to_tensor: bool = False,
        device: Optional[str] = None,
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
    texts: List[str],
    cache: Any,
    model_id: str = "",
) -> List[Any]:
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
        vectors = embedder.encode(texts, convert_to_numpy=True)
        if hasattr(vectors, "tolist"):
            return vectors.tolist()
        return vectors

    vectors_map: dict[int, Any] = {}
    missing_indices: list[int] = []
    missing_texts: list[str] = []

    # Check cache
    for i, text in enumerate(texts):
        key = _cache_key(text, model_id)
        cached_vector = await cache.get(key)

        if cached_vector is not None:
            vectors_map[i] = cached_vector
        else:
            missing_indices.append(i)
            missing_texts.append(text)

    # Encode missing
    if missing_texts:
        new_vectors = embedder.encode(missing_texts, convert_to_numpy=True)
        if hasattr(new_vectors, "tolist"):
            new_vectors = new_vectors.tolist()

        # Update cache and map
        for i, vector in zip(missing_indices, new_vectors):
            key = _cache_key(texts[i], model_id)
            await cache.set(key, vector)
            vectors_map[i] = vector

    # Reconstruct order
    return [vectors_map[i] for i in range(len(texts))]
