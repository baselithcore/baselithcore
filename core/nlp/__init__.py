"""
Core NLP Module.

Provides embedder and reranker utilities with caching support.
Consolidated entry point that re-exports from models.py.
"""

from __future__ import annotations

from core.observability.logging import get_logger
from functools import lru_cache
from typing import Any

from core.nlp.models import (
    CachedEmbedder,
    get_embedder,
    get_reranker,
)

logger = get_logger(__name__)


@lru_cache(maxsize=None)
def get_pipeline(task: str, model_name: str | None = None, **kwargs) -> Any:
    """
    Get a HuggingFace pipeline for various NLP tasks.

    Args:
        task: One of "summarization", "translation", "sentiment-analysis",
              "text-generation", "question-answering", "zero-shot-classification"
        model_name: Optional specific model, uses task default otherwise
        **kwargs: Additional arguments passed to pipeline()

    Returns:
        HuggingFace Pipeline instance
    """
    from transformers import pipeline

    return pipeline(task, model=model_name, **kwargs)  # type: ignore[call-overload]


__all__ = [
    "CachedEmbedder",
    "get_embedder",
    "get_reranker",
    "get_pipeline",
]
