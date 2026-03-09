"""
Reranker service for Advanced RAG.
"""

from core.observability.logging import get_logger
from typing import List, Optional

try:
    from sentence_transformers import CrossEncoder  # type: ignore[import-untyped]
except ImportError:
    CrossEncoder = None

from core.models.domain import SearchResult
from core.config.services import get_chat_config

logger = get_logger(__name__)


class Reranker:
    """
    Reranks search results using a Cross-Encoder model.
    """

    def __init__(self, model_name: Optional[str] = None):
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
    def model(self):
        """
        Access the Cross-Encoder model, loading it into memory on first use.

        Returns:
            Optional[CrossEncoder]: The loaded model or None if initialization failed.
        """
        if self._model is None and self._enabled and CrossEncoder:
            try:
                logger.info(f"Loading CrossEncoder model: {self.model_name}")
                self._model = CrossEncoder(self.model_name)
            except Exception as e:
                logger.error(f"Failed to load CrossEncoder model: {e}")
                self._enabled = False
        return self._model

    def rerank(
        self, query: str, results: List[SearchResult], top_k: int = 5
    ) -> List[SearchResult]:
        """
        Rerank a list of SearchResults based on relevance to the query.

        Args:
            query: The search query.
            results: List of SearchResult objects (candidates).
            top_k: Number of top results to return.

        Returns:
            Reranked list of SearchResult objects (top_k).
        """
        if not self._enabled or not self.model or not results:
            return results[:top_k]

        try:
            # Prepare pairs for CrossEncoder: [[query, doc_text], ...]
            pairs = []
            valid_indices = []

            for i, res in enumerate(results):
                content = res.document.content
                if content:
                    pairs.append([query, content])
                    valid_indices.append(i)

            if not pairs:
                return results[:top_k]

            # Predict scores
            scores = self.model.predict(pairs)

            # Assign new scores
            for idx, score in zip(valid_indices, scores):
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
_reranker: Optional[Reranker] = None


def get_reranker() -> Reranker:
    """Get global reranker instance."""
    global _reranker
    if _reranker is None:
        _reranker = Reranker()
    return _reranker
