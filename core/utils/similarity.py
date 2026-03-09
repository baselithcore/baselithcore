"""
Vector Similarity Utilities.

Provides efficient cosine similarity using numpy, shared across
memory, cache, and other modules that need vector comparisons.
"""

from __future__ import annotations

from typing import Any, List, Sequence, Union

import numpy as np

# Accepted vector types
VectorLike = Union[List[float], Sequence[float], np.ndarray]


def cosine_similarity(vec1: VectorLike, vec2: VectorLike) -> float:
    """
    Compute cosine similarity between two vectors using numpy.

    Handles both raw Python lists and numpy arrays efficiently.
    Returns 0.0 for empty, zero-norm, or mismatched vectors.

    Args:
        vec1: First vector (list, sequence, or ndarray)
        vec2: Second vector (list, sequence, or ndarray)

    Returns:
        Cosine similarity in [-1.0, 1.0], or 0.0 on invalid input
    """
    if vec1 is None or vec2 is None:
        return 0.0

    a = _to_ndarray(vec1)
    b = _to_ndarray(vec2)

    if a.size == 0 or b.size == 0 or a.shape != b.shape:
        return 0.0

    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)

    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0

    return float(np.dot(a, b) / (norm_a * norm_b))


def _to_ndarray(vec: Any) -> np.ndarray:
    """Convert a vector-like input to a 1-D float64 ndarray."""
    if isinstance(vec, np.ndarray):
        return vec.astype(np.float64, copy=False).ravel()
    return np.asarray(vec, dtype=np.float64).ravel()
