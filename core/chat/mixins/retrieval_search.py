"""Search Mixin for RetrievalPipeline."""

from core.observability.logging import get_logger
from typing import Any, Dict, TYPE_CHECKING
from pathlib import Path
from qdrant_client.models import Filter, FieldCondition, MatchValue

from core.chat.agent_state import AgentState
from core.observability import telemetry
from core.services.vectorstore import get_vectorstore_service
from core.services.indexing import get_indexing_service
from core.config import get_vectorstore_config

logger = get_logger(__name__)

_vs_config = get_vectorstore_config()
COLLECTION = _vs_config.collection_name


def _get_indexed_items():
    """Get indexed items from IndexingService."""
    service = get_indexing_service()
    return service.indexed_documents


if TYPE_CHECKING:
    from core.chat.service import ChatService


class RetrievalSearchMixin:
    """Mixin for retrieval search operations."""

    service: "ChatService"
    search_fn: Any

    async def retrieve_documents(self, state: AgentState) -> None:
        """
        Retrieve documents from the vector store based on the query vector.

        Args:
            state: Current agent state.
        """
        telemetry.increment("retrieval.requests")
        if not state.query_vector:
            logger.warning("Query vector is missing")
            return

        # 1. Initial Retrieval
        try:
            hits = await self.search_fn(
                state.query_vector, limit=self.service.INITIAL_SEARCH_K
            )
        except Exception as e:
            logger.error(f"Error during initial retrieval: {e}", exc_info=True)
            state.clarification_reason = "retrieval_failed"
            state.next_action = "request_clarification"
            return

        preferred = set()
        if state.conversation_id and state.conversation_id in getattr(
            self.service, "_last_sources", {}
        ):
            preferred = self.service._last_sources.get(state.conversation_id, set())

        if preferred:

            def _doc_id(hit: Any) -> str:
                payload = getattr(hit, "payload", None) or {}
                for key in ("document_id", "relative_path", "path", "source", "url"):
                    val = payload.get(key)
                    if isinstance(val, str) and val.strip():
                        return val.strip()
                return str(getattr(hit, "id", ""))

            # Boost documents already used in the conversation, but do not exclude others.
            hits = sorted(
                hits,
                key=lambda h: 0 if _doc_id(h) in preferred else 1,
            )

        hits = self._limit_chunks_per_doc(list(hits))

        # Fallback recall: if we have few hits, complete with other indexed documents (one per doc), choosing the most relevant chunk
        try:
            indexed_items = _get_indexed_items()
            vector_store = get_vectorstore_service()
            need = max(0, self.service.FINAL_TOP_K - len(hits))
            if need > 0 and indexed_items:
                seen = {
                    (getattr(hit, "payload", None) or {}).get("document_id")
                    for hit in hits
                }
                seen = {doc for doc in seen if isinstance(doc, str)}
                extra_hits: list[tuple[Any, float]] = []
                for doc_id in indexed_items.keys():
                    if need <= 0:
                        break
                    if doc_id in seen:
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
                        # search for the best chunk for that document relative to the current query
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
                            seen.add(doc_id)
                            need -= 1
                    except Exception:
                        logger.debug("Failed to process fallback hit", exc_info=True)
                        continue
                if extra_hits:
                    ordered = [
                        item
                        for item, _ in sorted(
                            extra_hits, key=lambda it: it[1], reverse=True
                        )
                    ]
                    hits = list(hits) + ordered
        except Exception as e:
            logger.warning(f"Fallback Qdrant query failed: {e}")

        # Attach documents cited in the text (title/filename) even if similarity doesn't pick them up
        hits = await self._inject_explicit_doc_matches(state.user_query, hits)
        hits = self._limit_chunks_per_doc(list(hits))
        state.hits = hits
        if not state.hits:
            telemetry.increment("retrieval.empty")
            state.log("retrieval:empty")
            state.clarification_reason = "no_hits"
            state.next_action = "request_clarification"
            return
        telemetry.increment("retrieval.success")
        state.next_action = "score_documents"

    @staticmethod
    def _limit_chunks_per_doc(all_hits: list[Any], max_per_doc: int = 2) -> list[Any]:
        """Keep at most `max_per_doc` chunks per document_id, preserving order."""
        kept: list[Any] = []
        seen_counts: Dict[str, int] = {}
        for hit in all_hits:
            payload = getattr(hit, "payload", None) or {}
            doc_id = payload.get("document_id")
            if not isinstance(doc_id, str):
                kept.append(hit)
                continue
            count = seen_counts.get(doc_id, 0)
            if count < max_per_doc:
                kept.append(hit)
                seen_counts[doc_id] = count + 1
        return kept

    @staticmethod
    async def _inject_explicit_doc_matches(query: str, hits: list[Any]) -> list[Any]:
        """
        If the user explicitly mentions an already indexed title/filename, insert at least
        one chunk of that document even if similarity does not pick it up.
        """

        try:
            query_l = query.lower()
            doc_ids_in_hits = {
                (getattr(hit, "payload", None) or {}).get("document_id") for hit in hits
            }
            doc_ids_in_hits = {
                doc_id for doc_id in doc_ids_in_hits if isinstance(doc_id, str)
            }

            candidates: list[str] = []
            indexed_items = _get_indexed_items()

            vector_store = get_vectorstore_service()
            for doc_id, meta in indexed_items.items():
                md = meta.get("metadata") or {}
                title = str(md.get("title") or "").lower()
                filename = str(md.get("filename") or "").lower()
                rel = str(md.get("relative_path") or "").lower()
                stems = {
                    Path(rel).stem.lower() if rel else "",
                    Path(filename).stem.lower() if filename else "",
                    Path(title).stem.lower() if title else "",
                }
                stems = {s for s in stems if s}
                tokens = set(query_l.replace("_", " ").replace("-", " ").split())

                if (
                    (title and title in query_l)
                    or (filename and filename in query_l)
                    or (rel and rel in query_l)
                    or any(stem in tokens for stem in stems)
                ):
                    if doc_id not in doc_ids_in_hits:
                        candidates.append(doc_id)

            extra_hits: list[Any] = []
            logger.debug("Explicit doc match candidates: %s", candidates)
            for doc_id in candidates:
                try:
                    filt = Filter(
                        must=[
                            FieldCondition(
                                key="document_id", match=MatchValue(value=doc_id)
                            )
                        ]
                    )
                    points, _ = await vector_store.scroll(
                        collection_name=COLLECTION,
                        limit=2,
                        offset=None,
                        with_payload=True,
                        with_vectors=False,
                        scroll_filter=filt,
                    )
                    if points:
                        extra_hits.append(points[0])
                        if len(points) > 1:
                            extra_hits.append(points[1])
                except Exception:
                    logger.debug("Failed to inject explicit match", exc_info=True)
                    continue

            return hits + extra_hits if extra_hits else hits
        except Exception as e:
            logger.warning(f"Inject explicit doc matches failed: {e}")
            return hits
