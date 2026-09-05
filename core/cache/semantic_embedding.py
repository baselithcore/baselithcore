"""
Prompt embedding for the semantic cache.

Owns the lazy embedder load and the bounded LRU memo of normalized query
vectors. Mixed into :class:`~core.cache.semantic_cache.SemanticLLMCache`;
split out of ``semantic_cache.py`` to respect the module size cap. State
(``_embedder``, ``_embedding_memo``) is still initialised by the host class.
"""

from __future__ import annotations

import inspect
from collections import OrderedDict
from typing import Any

import numpy as np

from core.observability.logging import get_logger
from core.utils.concurrency import run_inference

logger = get_logger(__name__)


class PromptEmbeddingMixin:
    """Lazy embedder + memoized, L2-normalized prompt embeddings."""

    # Provided by SemanticLLMCache.__init__.
    _embedder: Any
    _embedding_memo: OrderedDict[str, np.ndarray]

    def _get_embedder(self) -> Any:
        """Lazy load the embedder model using config for model selection."""
        if self._embedder is None:
            try:
                from core.config import get_voice_config
                from core.nlp import get_embedder

                model_name = get_voice_config().embedding_model
                self._embedder = get_embedder(model_name)
            except Exception as e:
                logger.warning(f"Failed to load embedder: {e}")
                raise
        return self._embedder

    # Bounded LRU for query embeddings: hot/repeated prompts skip the
    # sentence-transformer inference (tens of ms) entirely.
    _EMBEDDING_MEMO_MAX = 256

    async def _compute_embedding(self, text: str) -> np.ndarray:
        """Compute (or recall) the normalized embedding for a text."""
        memo = self._embedding_memo
        cached = memo.get(text)
        if cached is not None:
            memo.move_to_end(text)
            return cached

        embedder = self._get_embedder()
        # The production embedder (core.nlp.CachedEmbedder) exposes an async
        # encode; awaiting it here is what keeps the cache alive. A sync
        # embedder is offloaded to the dedicated inference pool rather than the
        # default executor, which serves latency-critical short tasks.
        if inspect.iscoroutinefunction(embedder.encode):
            raw = await embedder.encode(text, convert_to_numpy=True)
        else:
            raw = await run_inference(
                lambda: embedder.encode(text, convert_to_numpy=True)
            )

        embedding = np.asarray(raw, dtype=np.float32)
        # Normalize for cosine similarity
        norm = np.linalg.norm(embedding)
        if norm > 0:
            embedding = embedding / norm

        memo[text] = embedding
        memo.move_to_end(text)
        while len(memo) > self._EMBEDDING_MEMO_MAX:
            memo.popitem(last=False)
        return embedding


__all__ = ["PromptEmbeddingMixin"]
