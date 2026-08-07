"""
Vector Similarity Utilities.

Provides efficient cosine similarity using numpy, shared across
memory, cache, and other modules that need vector comparisons.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Union

import numpy as np

# Accepted vector types
VectorLike = Union[list[float], Sequence[float], np.ndarray]


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


def cosine_similarity_many(
    query: VectorLike | None, vectors: Sequence[VectorLike | None]
) -> list[float]:
    """
    Cosine similarity of one query against many vectors in a single matmul.

    Equivalent to ``[cosine_similarity(query, v) for v in vectors]`` but
    converts the query once and scores every row with one matrix product,
    instead of re-deriving the query ndarray and its norm per comparison.

    Args:
        query: The query vector.
        vectors: Candidate vectors; entries may be ``None``, empty, or of a
            mismatched dimension — those score 0.0, like ``cosine_similarity``.

    Returns:
        One score per input vector, aligned by index.
    """
    if query is None or not vectors:
        return [0.0] * len(vectors)

    q = _to_ndarray(query)
    q_norm = np.linalg.norm(q)
    if q.size == 0 or q_norm == 0.0:
        return [0.0] * len(vectors)

    scores = [0.0] * len(vectors)
    valid_indices: list[int] = []
    valid_rows: list[np.ndarray] = []
    for i, vec in enumerate(vectors):
        if vec is None:
            continue
        row = _to_ndarray(vec)
        if row.shape == q.shape:
            valid_indices.append(i)
            valid_rows.append(row)
    if not valid_rows:
        return scores

    matrix = np.stack(valid_rows)
    norms = np.linalg.norm(matrix, axis=1)
    dots = matrix @ q
    with np.errstate(divide="ignore", invalid="ignore"):
        sims = np.where(norms > 0.0, dots / (norms * q_norm), 0.0)
    for i, sim in zip(valid_indices, sims):
        scores[i] = float(sim)
    return scores


def _to_ndarray(vec: Any) -> np.ndarray:
    """Convert a vector-like input to a 1-D float64 ndarray."""
    if isinstance(vec, np.ndarray):
        return vec.astype(np.float64, copy=False).ravel()
    return np.asarray(vec, dtype=np.float64).ravel()
