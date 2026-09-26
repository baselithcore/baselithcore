"""
Reranker service for Advanced RAG.
"""

import asyncio
import threading
from typing import TYPE_CHECKING, Any, cast

from core.observability.logging import get_logger

if TYPE_CHECKING:
    from sentence_transformers import CrossEncoder  # type: ignore[import-untyped]
else:
    # Runtime guarded import: mypy only ever sees the typed branch above, so
    # the None fallback never reads as "assigning to a type".
    try:
        from sentence_transformers import CrossEncoder
    except ImportError:
        CrossEncoder = None

from core.config.services import get_chat_config
from core.models.domain import SearchResult

logger = get_logger(__name__)


class Reranker:
    """
    Reranks search results using a Cross-Encoder model.
    """

    def __init__(self, model_name: str | None = None):
        """
        Initialize the Reranker.

        Args:
            model_name: Optional HuggingFace model path override.
        """
        self.config = get_chat_config()
        self.model_name = model_name or getattr(
            self.config, "reranker_model", "cross-encoder/ms-marco-MiniLM-L-6-v2"
        )
        self._model = None
        self._enabled = False
        # Serializes the first load: concurrent cold callers each wait in a
        # worker thread instead of each constructing a CrossEncoder.
        self._load_lock = threading.Lock()

        if CrossEncoder:
            try:
                # We load the model lazily or on init? Init is better for fail-fast, but lazy is better for startup.
                # Let's lazy load during first usage to speed up cli commands if not used.
                self._enabled = True
                logger.info(
                    f"Reranker initialized with model '{self.model_name}' (lazy load)"
                )
            except Exception as e:
                logger.warning(f"Failed to initialize Reranker: {e}")
        else:
            logger.warning("sentence-transformers not installed. Reranker disabled.")

    @property
    def model(self) -> "CrossEncoder | None":
        """
        Access the Cross-Encoder model, loading it into memory on first use.

        Returns:
            Optional[CrossEncoder]: The loaded model or None if initialization failed.
        """
        if self._model is None and self._enabled and CrossEncoder:
            with self._load_lock:
                # Re-checked under the lock: a racing loader may have won.
                if self._model is None and self._enabled:
                    try:
                        logger.info(f"Loading CrossEncoder model: {self.model_name}")
                        self._model = CrossEncoder(self.model_name)
                    except Exception as e:
                        logger.error(f"Failed to load CrossEncoder model: {e}")
                        self._enabled = False
        return self._model

    async def aload_model(self) -> "CrossEncoder | None":
        """Return the model, loading it in a worker thread on first use.

        Building a CrossEncoder is seconds of disk and CPU work; the ``model``
        property does it synchronously, which would stall the event loop for
        every in-flight request on the first rerank.

        Returns:
            The loaded model, or None if loading is disabled or failed.
        """
        if self._model is not None or not self._enabled:
            return self._model
        return await asyncio.to_thread(lambda: self.model)

    async def rerank(
        self, query: str, results: list[SearchResult], top_k: int = 5
    ) -> list[SearchResult]:
        """
        Rerank a list of SearchResults based on relevance to the query.

        Cross-encoder inference is synchronous, CPU/GPU-bound torch work, so it
        is offloaded to a worker thread to avoid blocking the event loop.

        Args:
            query: The search query.
            results: List of SearchResult objects (candidates).
            top_k: Number of top results to return.

        Returns:
            Reranked list of SearchResult objects (top_k).
        """
        # Bound ONCE, loaded off the loop: mypy cannot narrow a property
        # across two reads anyway.
        model = await self.aload_model()
        if not self._enabled or model is None or not results:
            return results[:top_k]

        try:
            # Prepare pairs for CrossEncoder: [[query, doc_text], ...]
            pairs: list[tuple[str, str]] = []
            valid_indices: list[int] = []

            for i, res in enumerate(results):
                content = res.document.content
                if content:
                    pairs.append((query, content))
                    valid_indices.append(i)

            if not pairs:
                return results[:top_k]

            # Predict scores (offloaded so blocking torch inference does not
            # stall the event loop).
            # Wrapped in a lambda rather than passed as `to_thread(model.predict,
            # pairs)`: CrossEncoder.predict is an overloaded function, and an
            # overload set cannot be matched against to_thread's single
            # Callable parameter. The closure gives it one concrete signature.
            #
            # The cast is list invariance, not a doubt about the value. From
            # sentence-transformers 5.x the parameter is `list[PairInput]`,
            # where `PairInput` is itself a union — so `list[tuple[str, str]]`
            # is rejected even though every element is a valid `PairInput`.
            # Widening here keeps `pairs` honestly typed above and avoids
            # importing the library's private alias to satisfy the checker.
            scores = await asyncio.to_thread(
                lambda: model.predict(cast("list[Any]", pairs))
            )

            # Assign new scores
            for idx, score in zip(valid_indices, scores, strict=True):
                results[idx].score = float(score)

            # Sort by new score descending
            results.sort(key=lambda x: x.score, reverse=True)

            logger.debug(f"Reranked {len(results)} results")
            return results[:top_k]

        except Exception as e:
            logger.error(f"Reranking failed: {e}")
            # Fallback to original order
            return results[:top_k]


# Global instance
_reranker: Reranker | None = None


def get_reranker() -> Reranker:
    """Get global reranker instance."""
    global _reranker
    if _reranker is None:
        _reranker = Reranker()
    return _reranker
