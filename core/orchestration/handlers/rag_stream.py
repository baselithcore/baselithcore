"""Streaming twin of the standard RAG handler.

Same retrieval pipeline as :class:`~core.orchestration.handlers.rag.StandardRagHandler`
(delegated to it, so the two paths cannot drift), but the generation stage
streams tokens from ``LLMService.generate_response_stream`` instead of
awaiting the full completion — registered as the ``StreamHandler`` for the
default ``qa_docs`` intent so ``Orchestrator.process_stream`` delivers real
token streaming end-to-end.

Streaming trade-off: the chunk protocol carries text only, so ``sources`` and
retrieval metadata are exposed on the mutable orchestration context
(``context["sources"]``) rather than in the return value.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from core.observability.logging import get_logger
from core.orchestration.handlers.rag import (
    RAG_NOT_FOUND_MESSAGE,
    RAG_SYSTEM_PROMPT,
    StandardRagHandler,
    build_rag_user_prompt,
)

logger = get_logger(__name__)


class StandardRagStreamHandler:
    """StreamHandler for 'qa_docs': retrieve, then stream the LLM answer."""

    def __init__(
        self,
        vector_store: Any | None = None,
        llm_service: Any | None = None,
        config: Any | None = None,
        embedder: Any | None = None,
        rag_handler: StandardRagHandler | None = None,
    ) -> None:
        """
        Initialize the streaming RAG handler.

        Args:
            vector_store: Optional vector store service (lazy-loaded if None).
            llm_service: Optional LLM service (lazy-loaded if None).
            config: Optional chat configuration (lazy-loaded if None).
            embedder: Optional embedding service (lazy-loaded if None).
            rag_handler: Optional pre-built retrieval delegate; when given it
                wins over the individual service arguments.
        """
        self._rag = rag_handler or StandardRagHandler(
            vector_store=vector_store,
            llm_service=llm_service,
            config=config,
            embedder=embedder,
        )

    async def handle(
        self, query: str, context: dict[str, Any]
    ) -> AsyncGenerator[str, None]:
        """Stream the RAG answer for *query* chunk by chunk.

        Args:
            query: The user question.
            context: Execution context (``kb_label`` filters the collection);
                mutated with ``sources`` after retrieval.

        Yields:
            Response text chunks as the LLM produces them.
        """
        try:
            results, context_text, sources = await self._rag.retrieve(query, context)
        except Exception as exc:
            logger.error(f"Error in streaming RAG retrieval: {exc}")
            yield "An error occurred while searching for information."
            return

        if not results:
            yield RAG_NOT_FOUND_MESSAGE
            return

        # The stream protocol yields text only; surface citations to callers
        # that hold the (mutable) orchestration context.
        context["sources"] = list(set(sources))

        async for chunk in self._rag.llm_service.generate_response_stream(
            prompt=build_rag_user_prompt(context_text, query),
            system_prompt=RAG_SYSTEM_PROMPT,
        ):
            yield chunk


__all__ = ["StandardRagStreamHandler"]
