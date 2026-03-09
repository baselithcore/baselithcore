"""Scoring Mixin for RetrievalPipeline."""

from core.observability.logging import get_logger
from typing import Any, TYPE_CHECKING
from qdrant_client.models import Filter, FieldCondition, MatchValue

from core.chat.agent_state import AgentState
from core.chat.feedback import apply_feedback_boost
from core.db import get_document_feedback_summary
from core.observability import telemetry
from core.services.vectorstore import get_vectorstore_service
from core.services.indexing import get_indexing_service
from core.config import get_app_config, get_chat_config, get_vectorstore_config

logger = get_logger(__name__)

_app_config = get_app_config()
_chat_config = get_chat_config()
_vs_config = get_vectorstore_config()

COLLECTION = _vs_config.collection_name
FEEDBACK_BOOST_ENABLED = _app_config.feedback_boost_enabled
FEEDBACK_NEGATIVE_WEIGHT = _app_config.feedback_negative_weight
FEEDBACK_POSITIVE_WEIGHT = _app_config.feedback_positive_weight
FEEDBACK_SCORE_MIN_TOTAL = _app_config.feedback_score_min_total
RERANK_MAX_CANDIDATES = _chat_config.rerank_max_candidates


def _get_indexed_items():
    """Get indexed items from IndexingService."""
    service = get_indexing_service()
    return service.indexed_documents


if TYPE_CHECKING:
    from core.chat.service import ChatService


class RetrievalScoringMixin:
    """Mixin for retrieval scoring operations."""

    service: "ChatService"
    rerank_fn: Any

    async def score_documents(self, state: AgentState) -> None:
        """
        Score and rerank retrieved documents using the cross-encoder.

        Args:
            state: Current agent state.
        """
        telemetry.increment("rerank.requests")
        rerank_query = state.rerank_query or state.user_query
        normalized_query = state.normalized_query or " ".join(rerank_query.split())
        max_candidates = min(
            len(state.hits), self.service.INITIAL_SEARCH_K, RERANK_MAX_CANDIDATES
        )
        candidates = state.hits[:max_candidates]
        state.ranked_hits = await self.rerank_fn(
            rerank_query,
            normalized_query,
            candidates,
            reranker=self.service.reranker,
            cache=self.service.rerank_cache,
        )
        # Diversità per documento: tieni il miglior chunk per doc, poi ordina per score
        best_per_doc: dict[str, tuple[Any, float]] = {}
        for hit, score in state.ranked_hits:
            payload = getattr(hit, "payload", None) or {}
            doc_id = payload.get("document_id") or getattr(hit, "id", None)
            if not isinstance(doc_id, str):
                continue
            existing = best_per_doc.get(doc_id)
            if existing is None or score > existing[1]:
                best_per_doc[doc_id] = (hit, score)
        if best_per_doc:
            diverse = sorted(
                best_per_doc.values(), key=lambda item: item[1], reverse=True
            )
            state.ranked_hits = diverse[: self.service.FINAL_TOP_K]
        # Fallback: se abbiamo meno documenti del previsto, aggiungi altri doc indicizzati (un chunk a testa)
        if len(state.ranked_hits) < self.service.FINAL_TOP_K:
            try:
                indexed_items = _get_indexed_items()
                vector_store = get_vectorstore_service()

                seen_docs = {
                    (getattr(hit, "payload", None) or {}).get("document_id")
                    for hit, _ in state.ranked_hits
                }
                seen_docs = {doc for doc in seen_docs if isinstance(doc, str)}
                to_fill = self.service.FINAL_TOP_K - len(state.ranked_hits)
                extra_hits: list[tuple[Any, float]] = []
                for doc_id in indexed_items.keys():
                    if to_fill <= 0:
                        break
                    if doc_id in seen_docs:
                        continue
                    try:
                        filt = Filter(
                            must=[
                                FieldCondition(
                                    key="document_id", match=MatchValue(value=doc_id)
                                )
                            ]
                        )
                        qv = state.query_vector
                        if qv is None:
                            continue
                        response = await vector_store.query_points(
                            collection_name=COLLECTION,
                            query_vector=qv,  # type: ignore[arg-type]
                            limit=1,
                            with_payload=True,
                            with_vectors=False,
                            query_filter=filt,
                        )
                        points = response.points
                        if points:
                            score = getattr(points[0], "score", 0.0) or 0.0
                            extra_hits.append((points[0], float(score)))
                            seen_docs.add(doc_id)
                            to_fill -= 1
                    except Exception:
                        logger.debug(
                            "Failed to process rerank fallback hit", exc_info=True
                        )
                        continue
                if extra_hits:
                    extra_hits = sorted(extra_hits, key=lambda it: it[1], reverse=True)
                    state.ranked_hits.extend(extra_hits)  # type: ignore[attr-defined, union-attr]
            except Exception as e:
                logger.warning(f"Fallback Qdrant rerank query failed: {e}")
        if not state.ranked_hits:
            telemetry.increment("rerank.empty")
            state.log("rerank:empty")
            state.clarification_reason = "no_reranked_hits"
            state.next_action = "request_clarification"
            return

        if FEEDBACK_BOOST_ENABLED:
            state.next_action = "apply_feedback"
        else:
            state.next_action = "build_context"

    async def apply_feedback(self, state: AgentState) -> None:
        """
        Apply feedback-based boosts to the ranked hits.

        Args:
            state: Current agent state.
        """
        feedback_stats = await get_document_feedback_summary(
            min_total=FEEDBACK_SCORE_MIN_TOTAL
        )
        state.ranked_hits = apply_feedback_boost(
            state.ranked_hits,
            feedback_stats,
            min_total=FEEDBACK_SCORE_MIN_TOTAL,
            positive_weight=FEEDBACK_POSITIVE_WEIGHT,
            negative_weight=FEEDBACK_NEGATIVE_WEIGHT,
        )
        state.next_action = "build_context"
