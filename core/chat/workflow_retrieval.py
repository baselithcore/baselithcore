"""
Retrieval Pipeline Orchestration.

Combines search, reranking, and context building into a single unified step
for RAG workflows.
"""

from __future__ import annotations

from core.observability.logging import get_logger
from typing import TYPE_CHECKING

from core.chat.context import build_context_and_sources
from core.chat.reranking import rerank_hits
from core.services.vectorstore import get_vectorstore_service

from .mixins.retrieval_search import RetrievalSearchMixin
from .mixins.retrieval_scoring import RetrievalScoringMixin
from .mixins.retrieval_context import RetrievalContextMixin

logger = get_logger(__name__)


async def _search_wrapper(query_vector, **kwargs):
    """Wrapper for vectorstore search to maintain compatibility."""
    service = get_vectorstore_service()
    return await service.search(query_vector=query_vector, **kwargs)


if TYPE_CHECKING:
    from core.chat.service import ChatService


class RetrievalPipeline(
    RetrievalSearchMixin, RetrievalScoringMixin, RetrievalContextMixin
):
    """Orchestrates retrieval, reranking, context building, and caching."""

    def __init__(
        self,
        service: "ChatService",
        *,
        search_fn=_search_wrapper,
        rerank_fn=rerank_hits,
        build_context_fn=build_context_and_sources,
    ) -> None:
        self.service = service
        self.search_fn = search_fn
        self.rerank_fn = rerank_fn
        self.build_context_fn = build_context_fn


__all__ = ["RetrievalPipeline"]
