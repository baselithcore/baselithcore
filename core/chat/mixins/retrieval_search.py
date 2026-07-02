"""Search Mixin for RetrievalPipeline."""

import asyncio
from core.observability.logging import get_logger
from typing import Any, Dict, TYPE_CHECKING
from pathlib import Path
from qdrant_client.models import Filter, FieldCondition, MatchAny, MatchValue

from core.chat.agent_state import AgentState
from core.observability import telemetry
from core.services.vectorstore import get_vectorstore_service
from core.services.indexing import get_indexing_service
from core.config import get_vectorstore_config

logger = get_logger(__name__)

_vs_config = get_vectorstore_config()
COLLECTION = _vs_config.collection_name

# Bound concurrent per-doc fallback/scroll queries so a large index does not
# open an unbounded number of simultaneous Qdrant round-trips.
_FALLBACK_FANOUT = 8


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

        # Fallback recall: if we have few hits, complete with other indexed
        # documents (best chunk per doc). ONE grouped query returns the top
        # `need` unseen documents — a per-document query fan-out here scaled
        # O(corpus size) per chat request.
        try:
            indexed_items = _get_indexed_items()
            vector_store = get_vectorstore_service()
            need = max(0, self.service.FINAL_TOP_K - len(hits))
            qv = state.query_vector
            if need > 0 and indexed_items and qv is not None:
                seen_docs: list[str] = sorted(
                    {
                        doc
                        for doc in (
                            (getattr(hit, "payload", None) or {}).get("document_id")
                            for hit in hits
                        )
                        if isinstance(doc, str)
                    }
                )
                group_filter = None
                if seen_docs:
                    group_filter = Filter(
                        must_not=[
                            FieldCondition(
                                key="document_id",
                                match=MatchAny(any=seen_docs),
                            )
                        ]
                    )
                response = await vector_store.query_points_groups(
                    collection_name=COLLECTION,
                    query_vector=qv,  # type: ignore[arg-type]
                    group_by="document_id",
                    limit=need,
                    group_size=1,
                    with_payload=True,
                    with_vectors=False,
                    query_filter=group_filter,
                )
                groups = getattr(response, "groups", None) or []
                extra_hits = [group.hits[0] for group in groups if group.hits]
                if extra_hits:
                    # Groups arrive ordered by best-chunk score already.
                    hits = list(hits) + extra_hits[:need]
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

            # Query tokens depend only on the query, not the doc — compute once
            # instead of rebuilding the set for every indexed document.
            tokens = set(query_l.replace("_", " ").replace("-", " ").split())

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
            if candidates:
                semaphore = asyncio.Semaphore(_FALLBACK_FANOUT)

                async def _scroll_doc(doc_id: str) -> list[Any]:
                    async with semaphore:
                        try:
                            filt = Filter(
                                must=[
                                    FieldCondition(
                                        key="document_id",
                                        match=MatchValue(value=doc_id),
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
                            return list(points[:2]) if points else []
                        except Exception:
                            logger.debug(
                                "Failed to inject explicit match", exc_info=True
                            )
                            return []

                # Preserve candidate order: flatten per-doc results in order.
                per_doc = await asyncio.gather(
                    *(_scroll_doc(doc_id) for doc_id in candidates)
                )
                for points in per_doc:
                    extra_hits.extend(points)

            return hits + extra_hits if extra_hits else hits
        except Exception as e:
            logger.warning(f"Inject explicit doc matches failed: {e}")
            return hits
