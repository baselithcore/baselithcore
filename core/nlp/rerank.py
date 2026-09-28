"""
Cross-encoder scoring with a device-appropriate batch size.

``CrossEncoder.predict`` defaults to ``batch_size=32``. On CPU and Apple MPS
that is a padding trap: sentence-transformers already sorts the pairs by
length inside ``predict`` and maps the scores back, so the batch size changes
only how much padding each forward pass carries — never the scores or their
order. Measured on 40 candidates, batch 32 cost 8.1 s on CPU against 4.8 s at
batch 4 (3.1 s vs 1.7 s on MPS). Large batches win only on CUDA, where the
forward pass is bound by kernel launches rather than memory bandwidth.

:func:`score_pairs` is the one place the reranking call sites go through, so
the choice is made once and applies to the chat pipeline, the retrieval
service and memory recall alike.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from typing import Any

from core.nlp.lazy import LazyReranker

CPU_BATCH_SIZE = 8
"""Batch size off CUDA: small enough to avoid padding, large enough to amortise."""

CUDA_BATCH_SIZE = 32
"""Batch size on CUDA, where the library default is already the fast choice."""


def _is_cross_encoder(model: Any) -> bool:
    # Checked through ``sys.modules`` so this never imports torch itself: a
    # real CrossEncoder can only exist once sentence-transformers is loaded.
    st = sys.modules.get("sentence_transformers")
    cross_encoder = getattr(st, "CrossEncoder", None)
    return isinstance(cross_encoder, type) and isinstance(model, cross_encoder)


def batch_size_for(model: Any) -> int:
    """Return the ``predict`` batch size suited to the model's device.

    Args:
        model: A loaded cross-encoder; anything without a ``device`` attribute
            is treated as CPU.

    Returns:
        :data:`CUDA_BATCH_SIZE` on a CUDA device, else :data:`CPU_BATCH_SIZE`.
    """
    device = getattr(model, "device", None)
    kind = str(getattr(device, "type", device) or "cpu").lower()
    return CUDA_BATCH_SIZE if kind.startswith("cuda") else CPU_BATCH_SIZE


def score_pairs(reranker: Any, pairs: Sequence[tuple[str, str]]) -> Any:
    """Score ``(query, passage)`` pairs with a cross-encoder. Blocking.

    A real sentence-transformers ``CrossEncoder`` (or a :class:`LazyReranker`
    wrapping one) is called with a device-appropriate ``batch_size``. Any other
    reranker — a custom implementation or a test double — gets the plain
    ``predict(pairs)`` call the reranking protocol promises, so its signature
    is never assumed to accept extra keywords.

    Args:
        reranker: The reranker to score with.
        pairs: The ``(query, passage)`` pairs.

    Returns:
        Whatever the reranker's ``predict`` returns (an array of scores for a
        ``CrossEncoder``).
    """
    model = reranker.load() if isinstance(reranker, LazyReranker) else reranker
    batch = list(pairs)
    if _is_cross_encoder(model):
        return model.predict(
            batch, batch_size=batch_size_for(model), show_progress_bar=False
        )
    return model.predict(batch)


__all__ = ["CPU_BATCH_SIZE", "CUDA_BATCH_SIZE", "batch_size_for", "score_pairs"]
