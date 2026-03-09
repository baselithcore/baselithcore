"""
Workflow Validation logic.

Implements early-exit checks, intent detection for meta-queries, and initial
normalization of user requests.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.chat.agent_state import AgentState
from core.services.indexing import get_indexing_service

if TYPE_CHECKING:
    from core.chat.service import ChatService


class InputValidator:
    """Handle input preprocessing and early exit checks."""

    def __init__(self, service: "ChatService") -> None:
        self.service = service

    def validate_input(self, state: AgentState) -> None:
        """
        Validates the incoming user query and determines if immediate response is needed.

        Checks for empty queries or system-level triggers (like document listing)
        to bypass the expensive RAG pipeline when possible.

        Args:
            state: The agent state to validate and potentially mark as done.
        """
        state.rag_only = bool(getattr(state.request, "rag_only", False))
        state.user_query = (state.request.query or "").strip()
        if not state.user_query:
            state.answer = "⚠️ The question is empty."
            state.done = True
            state.next_action = ""
            return

        state.normalized_query = " ".join(state.user_query.split())

        # Intent: lista documenti disponibili in KB
        if self._is_list_documents_query(state.normalized_query):
            indexing_service = get_indexing_service()
            docs = indexing_service.indexed_documents
            if not docs:
                state.answer = (
                    "Non risultano documenti indicizzati nella knowledge base."
                )
            else:
                lines = []
                for doc_id, doc in docs.items():
                    meta = doc.metadata if hasattr(doc, "metadata") else {}
                    title = meta.get("title") or meta.get("filename") or doc_id
                    lines.append(f"- {title} ({doc_id})")
                state.answer = "Documenti disponibili in KB:\n" + "\n".join(lines)
            state.done = True
            state.next_action = ""
            return

        state.next_action = "classify_intent"

    @staticmethod
    def _is_list_documents_query(query: str) -> bool:
        q = query.lower()
        triggers = [
            "che documenti hai",
            "quali documenti hai",
            "lista documenti",
            "elenca documenti",
            "documenti disponibili",
            "kb disponibili",
        ]
        return any(trigger in q for trigger in triggers)


__all__ = ["InputValidator"]
