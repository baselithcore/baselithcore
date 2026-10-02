"""Remote stand-ins for the sentence-transformers models in :mod:`core.nlp.models`.

``get_embedder`` / ``get_reranker`` used to build a sentence-transformers model
in-process, unconditionally. With the core inference services configured
(``BASELITH_EMBEDDING_BACKEND=remote`` + ``BASELITH_EMBEDDING_URL``, and the
``BASELITH_RERANK_*`` pair), they return the classes below instead: the same
surface the callers use (``encode`` / ``get_sentence_embedding_dimension`` /
``predict``), backed by TEI through the blocking bridge. That is what lets an
image built without torch (``ML_RUNTIME=remote``) still serve the core's own
retrieval, memory and chat.

A remote model is used when the configured server serves the model the caller
asked for, or when no local runtime is installed at all — in the second case a
differently named model is still the only one available, and failing would
leave the feature dead. Both methods are blocking and are called from worker
threads (``run_inference`` / ``asyncio.to_thread``), never from the event loop.
"""

from __future__ import annotations

from collections.abc import Sequence
from importlib.util import find_spec
from typing import Any

import numpy as np

from core.config.inference import get_embedding_config, get_rerank_config
from core.observability.logging import get_logger

logger = get_logger(__name__)


def local_runtime_installed() -> bool:
    """Whether sentence-transformers can be imported (checked without importing it)."""
    return find_spec("sentence_transformers") is not None


def _use_remote(backend: str, url: str | None, served: str, requested: str) -> bool:
    if backend != "remote" or not url:
        return False
    if requested == served:
        return True
    if not local_runtime_installed():
        logger.warning("remote_model_substituted", requested=requested, served=served)
        return True
    return False


def use_remote_embedder(requested_model: str) -> bool:
    """True when :func:`core.nlp.models.get_embedder` should go remote."""
    cfg = get_embedding_config()
    return _use_remote(cfg.backend, cfg.url, cfg.model, requested_model)


def use_remote_reranker(requested_model: str) -> bool:
    """True when the reranker factories should go remote."""
    cfg = get_rerank_config()
    return _use_remote(cfg.backend, cfg.url, cfg.model, requested_model)


class RemoteEmbeddingModel:
    """``SentenceTransformer``-shaped embedder backed by the core EmbeddingService."""

    def __init__(self) -> None:
        cfg = get_embedding_config()
        self.model_name = cfg.model
        self._dim = cfg.dim

    def get_sentence_embedding_dimension(self) -> int:
        """Vector size served by the remote model."""
        return self._dim

    def encode(
        self,
        sentences: str | Sequence[str],
        convert_to_numpy: bool = True,
        **_kwargs: Any,
    ) -> Any:
        """Embed one text or a list; mirrors ``SentenceTransformer.encode``'s shapes."""
        from core.services.inference import get_sync_inference

        single = isinstance(sentences, str)
        texts = [str(sentences)] if single else [str(t) for t in sentences]
        vectors = get_sync_inference().embed_documents(texts) if texts else []
        out: Any = (
            np.asarray(vectors, dtype=np.float32)
            if convert_to_numpy
            else [np.asarray(v, dtype=np.float32) for v in vectors]
        )
        return out[0] if single else out


class RemoteCrossEncoder:
    """``CrossEncoder``-shaped reranker backed by the core RerankService."""

    def __init__(self) -> None:
        self.model_name = get_rerank_config().model

    def predict(self, sentences: Sequence[tuple[str, str]], **_kwargs: Any) -> Any:
        """Score ``(query, passage)`` pairs in input order (one call per query)."""
        from core.services.inference import get_sync_inference

        pairs = list(sentences)
        scores = np.zeros(len(pairs), dtype=np.float32)
        by_query: dict[str, list[int]] = {}
        for idx, (query, _) in enumerate(pairs):
            by_query.setdefault(query, []).append(idx)
        bridge = get_sync_inference()
        for query, indices in by_query.items():
            passages = [pairs[i][1] for i in indices]
            for local, score in bridge.rerank(query, passages, top_k=len(passages)):
                scores[indices[local]] = score
        return scores


__all__ = [
    "RemoteCrossEncoder",
    "RemoteEmbeddingModel",
    "local_runtime_installed",
    "use_remote_embedder",
    "use_remote_reranker",
]
