"""Remote stand-ins for the sentence-transformers models in :mod:`core.nlp.models`.

``get_embedder`` / ``get_reranker`` used to build a sentence-transformers model
in-process, unconditionally. With the core inference services configured
(``BASELITH_EMBEDDING_BACKEND=remote`` + ``BASELITH_EMBEDDING_URL``, and the
``BASELITH_RERANK_*`` pair), they return the classes below instead: the same
surface the callers use (``encode`` / ``get_sentence_embedding_dimension`` /
``predict``), backed by TEI through the blocking bridge. That is what lets an
image built without torch (``ML_RUNTIME=remote``) still serve the core's own
retrieval, memory and chat.

A remote embedder is used when the configured server serves the model the
caller asked for. A *different* served model is never substituted silently:
its vectors do not share the index's geometry, so with no local runtime to
serve the requested model the call fails with :class:`InferenceConfigError`
unless ``BASELITH_EMBEDDING_ALLOW_MODEL_SUBSTITUTION=true`` (then a warning is
logged). Either way ``BASELITH_EMBEDDING_DIM`` must equal
``VECTORSTORE_EMBEDDING_DIM`` — checked at first use, before any vector is
written or searched. A reranker produces scores, not stored vectors, so a
served reranker still substitutes (with a warning) when no local runtime
exists.

The embedder mirrors sentence-transformers' role convention: ``encode`` /
``encode_document`` embed documents (``..._DOCUMENT_PREFIX``),
``encode_query`` / ``encode(prompt_name="query")`` embed queries
(``..._QUERY_PREFIX``); see :mod:`core.nlp.roles`. All methods are blocking
and are called from worker threads (``run_inference`` /
``asyncio.to_thread``), never from the event loop.
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


def _index_dim() -> int:
    """``VECTORSTORE_EMBEDDING_DIM``: the size the vector index is built for."""
    from core.config import get_vectorstore_config

    return int(get_vectorstore_config().embedding_dim)


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
    """True when :func:`core.nlp.models.get_embedder` should go remote.

    Raises:
        InferenceConfigError: the remote model's dimension differs from the
            index's, or it would substitute the requested model without the
            ``BASELITH_EMBEDDING_ALLOW_MODEL_SUBSTITUTION`` opt-in.
    """
    from core.services.inference.errors import InferenceConfigError

    cfg = get_embedding_config()
    if cfg.backend != "remote" or not cfg.url:
        return False
    substitute = requested_model != cfg.model
    if substitute and local_runtime_installed():
        return False  # the local runtime serves the requested model itself
    index_dim = _index_dim()
    if cfg.dim != index_dim:
        raise InferenceConfigError(
            f"remote embedding model '{cfg.model}' is configured for "
            f"{cfg.dim}-dim vectors (BASELITH_EMBEDDING_DIM) but the index "
            f"expects {index_dim} (VECTORSTORE_EMBEDDING_DIM): align the two "
            "(and re-index if the model changed)."
        )
    if substitute:
        if not cfg.allow_model_substitution:
            raise InferenceConfigError(
                f"'{requested_model}' was requested but the embedding server "
                f"serves '{cfg.model}', and no local runtime can serve the "
                "requested one. Point VECTORSTORE_EMBEDDING_MODEL at the "
                "served model, or set BASELITH_EMBEDDING_ALLOW_MODEL_"
                "SUBSTITUTION=true to accept its vectors in this index."
            )
        logger.warning(
            "remote_model_substituted", requested=requested_model, served=cfg.model
        )
    return True


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
        #: Same shape as ``SentenceTransformer.prompts``; the cache keys on it.
        self.prompts = {"query": cfg.query_prefix, "document": cfg.document_prefix}

    def get_sentence_embedding_dimension(self) -> int:
        """Vector size served by the remote model."""
        return self._dim

    def encode(
        self,
        sentences: str | Sequence[str],
        convert_to_numpy: bool = True,
        prompt_name: str | None = None,
        **_kwargs: Any,
    ) -> Any:
        """Embed documents (or queries with ``prompt_name="query"``)."""
        return self._embed(sentences, convert_to_numpy, prompt_name == "query")

    def encode_query(
        self,
        sentences: str | Sequence[str],
        convert_to_numpy: bool = True,
        **_kwargs: Any,
    ) -> Any:
        """Embed the search side (``BASELITH_EMBEDDING_QUERY_PREFIX`` applies)."""
        return self._embed(sentences, convert_to_numpy, True)

    def encode_document(
        self,
        sentences: str | Sequence[str],
        convert_to_numpy: bool = True,
        **_kwargs: Any,
    ) -> Any:
        """Embed the indexed side (``BASELITH_EMBEDDING_DOCUMENT_PREFIX``)."""
        return self._embed(sentences, convert_to_numpy, False)

    def _embed(
        self, sentences: str | Sequence[str], convert_to_numpy: bool, is_query: bool
    ) -> Any:
        """One text or a list; mirrors ``SentenceTransformer.encode``'s shapes."""
        from core.services.inference import get_sync_inference

        single = isinstance(sentences, str)
        texts = [str(sentences)] if single else [str(t) for t in sentences]
        bridge = get_sync_inference()
        vectors: list[list[float]] = []
        if texts:
            embed = bridge.embed_queries if is_query else bridge.embed_documents
            vectors = embed(texts)
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
        """Score ``(query, passage)`` pairs in input order (calls grouped by query)."""
        from core.services.inference import get_sync_inference

        pairs = list(sentences)
        scores = np.zeros(len(pairs), dtype=np.float32)
        by_query: dict[str, list[int]] = {}
        for idx, (query, _) in enumerate(pairs):
            by_query.setdefault(query, []).append(idx)
        bridge = get_sync_inference()
        # The service scores at most max_candidates texts per call and drops
        # the rest; a CrossEncoder scores every pair, so chunk past the cap.
        cap = get_rerank_config().max_candidates
        for query, indices in by_query.items():
            for start in range(0, len(indices), cap):
                chunk = indices[start : start + cap]
                passages = [pairs[i][1] for i in chunk]
                for local, score in bridge.rerank(query, passages, len(passages)):
                    scores[chunk[local]] = score
        return scores


__all__ = [
    "RemoteCrossEncoder",
    "RemoteEmbeddingModel",
    "local_runtime_installed",
    "use_remote_embedder",
    "use_remote_reranker",
]
