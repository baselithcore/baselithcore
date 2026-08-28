"""Pre-retrieval cache probe for the RAG pipeline.

Sits between ``load_history`` and ``retrieve_documents``. On a hit the whole
retrieval pipeline — vector search, cross-encoder rerank, context build — is
skipped. See :mod:`core.chat.precheck` for the key scheme and the freshness
trade-off this layer deliberately accepts.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from core.chat.agent_state import AgentState
from core.chat.precheck import build_precheck_key
from core.observability import telemetry
from core.observability.logging import get_logger

logger = get_logger(__name__)

if TYPE_CHECKING:
    from core.chat.service import ChatService


class RetrievalPrecheckMixin:
    """Mixin adding the opt-in pre-retrieval answer cache probe."""

    service: ChatService

    if TYPE_CHECKING:
        # Supplied by RetrievalContextMixin on the composed pipeline.
        async def ensure_query_vector(self, state: AgentState) -> None: ...

    async def check_precheck_cache(self, state: AgentState) -> None:
        """Probe the pre-retrieval cache; on a hit, finish the request here.

        A no-op unless the pre-check cache was built (it is ``None`` whenever
        ``CHAT_RAG_PRECHECK_ENABLED`` is false, which is the default). In that
        no-op case ``state`` — ``next_action`` included — is left completely
        untouched, so the pipeline behaves exactly as it did before this layer
        existed.

        Args:
            state: Current agent state, after ``load_history``.
        """
        cache: Any = getattr(self.service, "precheck_cache", None)
        if cache is None:
            return

        key = build_precheck_key(state)
        if key is None:
            # No corpus version / no query: refuse to guess. Falling through
            # to full retrieval is always correct, just slower.
            await self._resume_retrieval(state)
            return

        state.precheck_cache_key = key

        try:
            cached_answer = await cache.get(key)
        except Exception:
            logger.warning("precheck_cache_get_failed", exc_info=True)
            await self._resume_retrieval(state)
            return

        if cached_answer is not None:
            telemetry.increment("precheck_cache.hit")
            telemetry.increment("answers.precheck_cached")
            state.answer = cached_answer
            state.log("precheck_cache:hit")
            state.done = True
            state.next_action = ""
            return

        telemetry.increment("precheck_cache.miss")
        await self._resume_retrieval(state)

    async def _resume_retrieval(self, state: AgentState) -> None:
        """Restore the state ``load_history`` deferred and hand off to search.

        With the pre-check enabled ``load_history`` skips the query embedding
        so a hit need not pay for it; every non-hit path must put it back
        before retrieval runs.
        """
        await self.ensure_query_vector(state)
        state.next_action = "retrieve_documents"


__all__ = ["RetrievalPrecheckMixin"]
