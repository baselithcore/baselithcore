"""Tests for vector similarity utilities."""

import numpy as np
import pytest

from core.utils.similarity import cosine_similarity, cosine_similarity_many


def test_cosine_identical_vectors():
    assert cosine_similarity([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)


def test_cosine_orthogonal_vectors():
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_many_matches_scalar_loop():
    """The batched matmul must be numerically equivalent to the per-item calls."""
    rng = np.random.default_rng(42)
    query = rng.normal(size=16).tolist()
    vectors = [rng.normal(size=16).tolist() for _ in range(20)]

    batched = cosine_similarity_many(query, vectors)
    scalar = [cosine_similarity(query, v) for v in vectors]

    assert batched == pytest.approx(scalar)


def test_many_handles_invalid_entries():
    """None, empty, zero-norm and dimension-mismatched entries score 0.0."""
    query = [1.0, 2.0, 3.0]
    vectors = [
        [1.0, 2.0, 3.0],  # valid
        None,  # missing
        [],  # empty
        [0.0, 0.0, 0.0],  # zero norm
        [1.0, 2.0],  # dimension mismatch
    ]
    scores = cosine_similarity_many(query, vectors)
    assert scores[0] == pytest.approx(1.0)
    assert scores[1:] == [0.0, 0.0, 0.0, 0.0]


def test_many_empty_inputs():
    assert cosine_similarity_many(None, [[1.0]]) == [0.0]
    assert cosine_similarity_many([1.0], []) == []
    assert cosine_similarity_many([], [[1.0], [2.0]]) == [0.0, 0.0]
