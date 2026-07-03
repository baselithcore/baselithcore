"""
Core NLP Module.

Provides embedder and reranker utilities with caching support.
Consolidated entry point that re-exports from models.py.
"""

from __future__ import annotations

from functools import cache, lru_cache
from typing import Any

from core.nlp.models import (
    CachedEmbedder,
    get_embedder,
    get_reranker,
)
from core.observability.logging import get_logger

logger = get_logger(__name__)


@cache
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
    "get_pipeline",
    "get_reranker",
]
