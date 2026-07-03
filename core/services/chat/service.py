"""
Chat Service implementation.

This module provides the central entry point for conversational AI interactions.
It coordinates multiple internal components:
1. Orchestrator: The brain that selects agents and handles intent.
2. History Manager: Manages chat sessions and conversation context.
3. NLP Utilities: (Embedder/Reranker) used for semantic processing during RAG.
4. Observability: Tracks request metrics and latencies.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Iterator
from typing import TYPE_CHECKING, Any

from core.guardrails.input_guard import InputGuard
from core.models.chat import ChatRequest, ChatResponse
from core.observability.logging import get_logger
from core.services.chat.exceptions import ChatServiceError
from core.services.chat.utils.history import CacheProtocol, ChatHistoryManager

if TYPE_CHECKING:
    from sentence_transformers import (  # type: ignore[import-untyped]
        CrossEncoder,
        SentenceTransformer,
    )

logger = get_logger(__name__)

# Sentinel for signaling the end of a synchronous stream wrapped in an async iterator.
_STREAM_EOF = object()


def _next_stream_chunk(iterator: Iterator[str]) -> object:
    """
    Safely retrieve the next chunk from a standard iterator.

    Used to wrap synchronous generators in asynchronous flows.
    """
    try:
        return next(iterator)
    except StopIteration:
        return _STREAM_EOF


class ChatServiceConfig:
    """
    Configuration parameters for tuning the Chat Service behavior.
    """

    def __init__(
        self,
        *,
        initial_search_k: int = 40,
        final_top_k: int = 6,
        streaming_enabled: bool = True,
        embedder_model: str = "all-MiniLM-L6-v2",
        reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2",
        history_enabled: bool = True,
        history_max_turns: int = 10,
    ) -> None:
        """
        Args:
            initial_search_k: Number of candidates to fetch from vector DB.
            final_top_k: Number of results to keep after re-ranking.
            streaming_enabled: Global toggle for token-by-token output.
            embedder_model: ID of the sentence-transformer used for embeddings.
            reranker_model: ID of the cross-encoder used for semantic validation.
            history_enabled: If True, tracks conversation state across turns.
            history_max_turns: Maximum context window for historical memory.
        """
        self.initial_search_k = initial_search_k
        self.final_top_k = final_top_k
        self.streaming_enabled = streaming_enabled
        self.embedder_model = embedder_model
        self.reranker_model = reranker_model
        self.history_enabled = history_enabled
        self.history_max_turns = history_max_turns


class ChatService:
    """
    High-level service for managing conversational interactions.

    This class serves as a Facade over the complex orchestration, retrieval,
    and memory systems, providing a simple API for sync, async, and streaming chat.
    """

    def __init__(
        self,
        *,
        config: ChatServiceConfig | None = None,
        embedder: SentenceTransformer | None = None,
        reranker: CrossEncoder | None = None,
        response_cache: CacheProtocol | None = None,
        rerank_cache: CacheProtocol | None = None,
        history_manager: ChatHistoryManager | None = None,
        plugin_registry: Any | None = None,
    ) -> None:
        """
        Initialize ChatService with its core dependencies.

        Args:
            config: Operational configuration settings.
            embedder: Pre-initialized embedding model (lazy-loaded if None).
            reranker: Pre-initialized re-ranking model (lazy-loaded if None).
            response_cache: Optional cache interface for storing final chat answers.
            rerank_cache: Optional cache for intermediate re-ranking results.
            history_manager: Coordinator for conversation persistence.
            plugin_registry: Mandatory registry containing available tools/agents.
        """
        self.config = config or ChatServiceConfig()
        self.plugin_registry = plugin_registry

        # Internal state/dependencies.
        self._embedder = embedder
        self._reranker = reranker
        self.response_cache = response_cache
        self.rerank_cache = rerank_cache
        self._history_manager = history_manager

        # Layout constants.
        self.newline = "\n"
        self.double_newline = "\n\n"
        self.section_separator = "\n\n---\n\n"

        # Dynamically loaded components.
        self._agent: Any | None = None
        self._last_sources: dict[str, set[str]] = {}

    @property
    def embedder(self) -> SentenceTransformer:
        """
        Access the embedding model, initializing it from the framework context if missing.
        """
        if self._embedder is None:
            try:
                from core.nlp import get_embedder

                self._embedder = get_embedder(self.config.embedder_model)
            except ImportError as e:
                raise ChatServiceError(
                    "NLP module not reachable. Verify core.nlp installation."
                ) from e
        return self._embedder

    @property
    def reranker(self) -> CrossEncoder:
        """
        Access the re-ranking model with lazy initialization.
        """
        if self._reranker is None:
            try:
                from core.nlp import get_reranker

                self._reranker = get_reranker(self.config.reranker_model)
            except ImportError as e:
                raise ChatServiceError(
                    "Re-ranking module not reachable. Verify dependencies."
                ) from e
        return self._reranker

    @property
    def history_manager(self) -> ChatHistoryManager:
        """
        Access the history coordinator, ensuring a default instance exists.
        """
        if self._history_manager is None:
            self._history_manager = ChatHistoryManager(
                cache=None,
                max_turns=self.config.history_max_turns,
            )
        return self._history_manager

    @property
    def agent(self) -> Any:
        """
        Access the main Orchestrator 'agent'.

        This agent is responsible for breaking down the query into tasks
        and coordinating sub-agents. Requires `plugin_registry`.
        """
        if self._agent is None:
            if self.plugin_registry is None:
                raise ChatServiceError(
                    "Orchestrator requires active plugin_registry. "
                    "Initialize ChatService with a valid registry instance."
                )

            try:
                from core.orchestration import Orchestrator

                self._agent = Orchestrator(plugin_registry=self.plugin_registry)
                logger.info("ChatService bound to core.orchestration.Orchestrator")
            except ImportError as e:
                raise ChatServiceError(
                    f"Orchestration layer missing: {e}. Check core/orchestration path."
                ) from e

        return self._agent

    def handle_chat(self, req: ChatRequest) -> ChatResponse:
        """
        Process a standard conversational request synchronously.

        WARNING: Blocking call. Use `handle_chat_async` in high-concurrency
        environments (like FastAPI) to avoid event loop starvation.

        Args:
            req: Structured request containing query and conversation metadata.

        Returns:
            ChatResponse: Structured response with answer and citation sources.
        """
        start = time.perf_counter()
        try:
            self._record_metric("chat_requests_total", route="sync")

            # Defensive event loop check.
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop and loop.is_running():
                raise RuntimeError(
                    "Attempted synchronous chat execution from an async context. "
                    "Call `handle_chat_async` instead."
                )

            # Validate input using Guardrails
            guard_result = InputGuard().validate(req.query)
            if not guard_result.is_valid:
                raise ChatServiceError(
                    f"Blocked by InputGuard: {guard_result.blocked_reason or 'Potentially harmful content detected'}"
                )

            # Execution context for the orchestrator.
            context = {
                "conversation_id": req.conversation_id,
                "rag_only": req.rag_only,
                "kb_label": req.kb_label,
            }

            # Run the underlying async orchestrator in a temporary loop.
            result = asyncio.run(self.agent.process(req.query, context))

            return ChatResponse(
                answer=result.get("response", "")
                or result.get("error", "Generation failed"),
                metadata=result.get("metadata", {}),
                sources=result.get("sources"),
                conversation_id=req.conversation_id,
            )
        except Exception:
            self._record_metric("chat_request_errors_total", route="sync")
            raise
        finally:
            self._record_metric(
                "chat_request_latency", route="sync", value=time.perf_counter() - start
            )

    def handle_chat_stream(self, req: ChatRequest) -> Iterator[str]:
        """
        Process a conversational request as a synchronous stream.

        Args:
            req: Chat request payload.

        Returns:
            Iterator[str]: Generator yielding response tokens or segments.
        """
        start = time.perf_counter()
        try:
            self._record_metric("chat_requests_total", route="stream")

            if not self.config.streaming_enabled:
                response = self.handle_chat(req)
                return iter([response.answer])

            context = {
                "conversation_id": req.conversation_id,
                "rag_only": req.rag_only,
                "kb_label": req.kb_label,
            }

            # Delegate to the orchestrator's native streaming capability.
            return self.agent.process_stream(req.query, context)

        except Exception:
            self._record_metric("chat_request_errors_total", route="stream")
            self._record_metric(
                "chat_request_latency",
                route="stream",
                value=time.perf_counter() - start,
            )
            logger.exception("Chat streaming pipeline failed")
            return iter(["❌ Critical internal error during generation."])

    async def handle_chat_async(self, req: ChatRequest) -> ChatResponse:
        """
        Process a conversational request asynchronously.

        Preferred method for modern web servers.

        Args:
            req: Chat request payload.

        Returns:
            ChatResponse: Structured response model.
        """
        start = time.perf_counter()
        try:
            self._record_metric("chat_requests_total", route="async_sync")
            # Validate input using Guardrails
            guard_result = InputGuard().validate(req.query)
            if not guard_result.is_valid:
                raise ChatServiceError(
                    f"Blocked by InputGuard: {guard_result.blocked_reason or 'Potentially harmful content detected'}"
                )

            context = {
                "conversation_id": req.conversation_id,
                "rag_only": req.rag_only,
                "kb_label": req.kb_label,
            }

            # Async execution of the orchestration pipeline.
            result = await self.agent.process(req.query, context)

            return ChatResponse(
                answer=result.get("response", ""),
                metadata=result.get("metadata", {}),
                sources=result.get("sources"),
                conversation_id=req.conversation_id,
            )
        except Exception:
            self._record_metric("chat_request_errors_total", route="async_sync")
            raise
        finally:
            self._record_metric(
                "chat_request_latency",
                route="async_sync",
                value=time.perf_counter() - start,
            )

    async def handle_chat_stream_async(self, req: ChatRequest) -> AsyncIterator[str]:
        """
        Process a conversational request as an asynchronous stream.

        Uses the orchestrator's native async stream to avoid per-chunk
        executor hops on the hot path.
        """
        start = time.perf_counter()
        try:
            self._record_metric("chat_requests_total", route="stream")

            if not self.config.streaming_enabled:
                response = await self.handle_chat_async(req)

                async def _single_response() -> AsyncIterator[str]:
                    yield response.answer

                return _single_response()

            context = {
                "conversation_id": req.conversation_id,
                "rag_only": req.rag_only,
                "kb_label": req.kb_label,
            }

            return self.agent.process_stream(req.query, context)

        except Exception:
            self._record_metric("chat_request_errors_total", route="stream")
            self._record_metric(
                "chat_request_latency",
                route="stream",
                value=time.perf_counter() - start,
            )
            logger.exception("Async chat streaming pipeline failed")

            async def _error_stream() -> AsyncIterator[str]:
                yield "❌ Critical internal error during generation."

            return _error_stream()

    def _record_metric(
        self, name: str, route: str = "", value: float | None = None
    ) -> None:
        """
        Update Prometheus metrics for chat operations.

        Args:
            name: Metric identifier (e.g., 'chat_requests_total').
            route: Call context ('sync', 'async', 'stream').
            value: Optional numeric value (e.g., latency duration).
        """
        try:
            from core.observability.metrics import (
                CHAT_REQUEST_ERRORS_TOTAL,
                CHAT_REQUEST_LATENCY_SECONDS,
                CHAT_REQUESTS_TOTAL,
            )

            if name == "chat_requests_total":
                CHAT_REQUESTS_TOTAL.labels(route=route).inc()
            elif name == "chat_request_errors_total":
                CHAT_REQUEST_ERRORS_TOTAL.labels(route=route, reason="exception").inc()
            elif name == "chat_request_latency" and value is not None:
                CHAT_REQUEST_LATENCY_SECONDS.labels(route=route).observe(value)
        except ImportError:
            pass  # Fallback: ignore metrics if the module is unavailable or disabled.


__all__ = ["ChatService", "ChatServiceConfig"]
