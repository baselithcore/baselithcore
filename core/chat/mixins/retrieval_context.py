"""Context Mixin for RetrievalPipeline."""

import hashlib
from core.observability.logging import get_logger
from typing import Any, Dict, List, Optional, TYPE_CHECKING
from core.chat.agent_state import AgentState
from core.observability import telemetry

logger = get_logger(__name__)

if TYPE_CHECKING:
    from core.chat.service import ChatService


class RetrievalContextMixin:
    """Mixin for context building and cache operations."""

    service: "ChatService"
    build_context_fn: Any

    async def load_history(self, state: AgentState) -> None:
        """
        Load conversation history and prepare the retrieval query.

        Args:
            state: Current agent state.
        """
        conversation_id = (state.request.conversation_id or "").strip() or None
        state.conversation_id = conversation_id

        history_turns, history_text = await self.service.history_manager.load(
            conversation_id
        )
        state.history_turns = history_turns
        state.history_text = history_text

        # Include the conversation context for more precise follow-up responses.
        # For retrieval/reranking, we use only the last turns to avoid
        # old context (e.g. another document) from dominating the search vector.
        retrieval_history = ""
        if history_turns:
            # Take the last 2 turns (4 exchanges max: U, A, U, A)
            recent_turns = history_turns[-2:]
            lines = []
            for turn in recent_turns:
                q = turn.get("query", "")
                a = turn.get("answer", "")
                if q:
                    lines.append(f"User: {q}")
                if a:
                    # Truncate the assistant answer if too long for retrieval
                    if len(a) > 200:
                        a = a[:200] + "..."
                    lines.append(f"Assistant: {a}")
            retrieval_history = "\n".join(lines)

        query_text = state.user_query
        if retrieval_history:
            query_text = f"{state.user_query}\nRecent context:\n{retrieval_history}"

        state.rerank_query = query_text
        state.normalized_query = " ".join(query_text.split())
        state.query_vector = (await self.service.embedder.encode([query_text]))[0]
        state.next_action = "retrieve_documents"

    async def build_context(self, state: AgentState) -> None:
        """
        Build the context block from ranked hits and update source metrics.

        Args:
            state: Current agent state.
        """
        state.ranked_hits = state.ranked_hits[: self.service.FINAL_TOP_K]
        context, doc_sources = self.build_context_fn(
            state.ranked_hits,
            final_top_k=self.service.FINAL_TOP_K,
            newline=self.service.newline,
            double_newline=self.service.double_newline,
            section_separator=self.service.section_separator,
        )
        state.context = context
        state.doc_sources = list(doc_sources)
        if state.doc_sources:
            source_metrics: Dict[str, Any] = {}
            ratios: List[float] = []
            scores: List[float] = []
            top_ratio: Optional[float] = None

            for idx, source in enumerate(state.doc_sources):
                ratio_value = source.get("context_ratio")
                if isinstance(ratio_value, (int, float)):
                    ratios.append(ratio_value)
                    if idx == 0:
                        top_ratio = ratio_value
                score_value = source.get("score_avg")
                if not isinstance(score_value, (int, float)):
                    score_value = source.get("score")
                if isinstance(score_value, (int, float)):
                    scores.append(score_value)

            if ratios:
                total_ratio = sum(ratios)
                source_metrics["total_context_ratio"] = total_ratio
                source_metrics["mean_context_ratio"] = total_ratio / len(ratios)
            if scores:
                source_metrics["mean_score"] = sum(scores) / len(scores)
                source_metrics["max_score"] = max(scores)
            if top_ratio is not None:
                source_metrics["top_context_ratio"] = top_ratio
                low_coverage = top_ratio < 0.25
                source_metrics["low_coverage"] = low_coverage
                state.log(f"sources:top_ratio={top_ratio:.2f}")
                if low_coverage:
                    telemetry.increment("sources.low_coverage")
            else:
                source_metrics["low_coverage"] = False
            state.source_metrics = source_metrics
        else:
            state.source_metrics = {}

        if not state.context.strip() and not state.history_text:
            state.log("context:empty_without_history")
            state.clarification_reason = "empty_context"
            state.next_action = "request_clarification"
            return

        state.next_action = "check_cache"

    async def check_cache(self, state: AgentState) -> None:
        """
        Check the response cache for a matching context and query.

        Args:
            state: Current agent state.
        """
        if self.service.response_cache is None:
            state.next_action = (
                "plan_backlog" if not state.rag_only else "generate_answer"
            )
            return

        cache_context_repr = state.context
        if state.history_text:
            cache_context_repr = f"{state.history_text}\n\n====\n\n{state.context}"
        if state.rag_only:
            cache_context_repr = f"[RAG_ONLY]\n{cache_context_repr}"

        context_hash = hashlib.sha256(cache_context_repr.encode("utf-8")).hexdigest()
        state.cache_key = (state.normalized_query, context_hash)
        cached_answer = await self.service.response_cache.get(state.cache_key)  # type: ignore[arg-type]

        if cached_answer is not None:
            telemetry.increment("response_cache.hit")
            telemetry.increment("answers.cached")
            state.answer = cached_answer
            state.done = True
            state.next_action = ""
            return

        telemetry.increment("response_cache.miss")
        state.next_action = "plan_backlog" if not state.rag_only else "generate_answer"
