"""
Graph Service.

Coordinates document nodes, entity relationships, and graph-based reasoning.
"""

from __future__ import annotations
from core.observability.logging import get_logger
from typing import Any, Dict, Optional, List

from core.graph import GraphDb
from core.services.graph.ops.document_ops import DocumentOperations
from core.services.graph.ops.utils import current_timestamp

logger = get_logger(__name__)


class GraphService:
    """
    Core Graph Service for generic graph operations.
    Handles Documents, Generic Linking, and general Reasoning.
    Domain-specific operations (Code) handled by plugins.
    """

    def __init__(self, graph_client: GraphDb) -> None:
        """
        Initialize the Graph service.

        Args:
            graph_client: The active Graph database client instance.
        """
        self.client = graph_client
        self._doc_ops = DocumentOperations(graph_client)

    def upsert_document(
        self,
        doc_id: str,
        doc_type: str,
        path: str,
        category: Optional[str] = None,
        properties: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Create or update a Document node in the knowledge graph.

        Delegates the operation to the internal DocumentOperations handler.

        Args:
            doc_id: Unique identifier for the document.
            doc_type: Semantic type (e.g., 'pdf', 'text').
            path: Storage URI or file path.
            category: Optional lifecycle category.
            properties: Additional metadata for the node.
        """
        return self._doc_ops.upsert_document(
            doc_id, doc_type, path, category, properties
        )

    def transition_document_to_kb(
        self, doc_id: str, new_path: Optional[str] = None
    ) -> None:
        """
        Transition a document from an analysis state to the knowledge base.

        Args:
            doc_id: Unique identifier for the document.
            new_path: Optional new storage path for the KB version.
        """
        return self._doc_ops.transition_document_to_kb(doc_id, new_path)

    def link_entities(
        self,
        source_id: str,
        relationship: str,
        target_id: str,
        properties: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Create a directed relationship between two entities.

        Args:
            source_id: ID of the starting node.
            relationship: The semantic label for the link (e.g., 'MENTIONS').
            target_id: ID of the ending node.
            properties: Optional properties for the relationship edge.
        """
        if not self.client.is_enabled():
            return
        try:
            self.client.upsert_edge(source_id, relationship, target_id, properties)  # type: ignore[misc]
        except Exception:
            logger.warning(
                f"GraphService: failed link {source_id} -[{relationship}]-> {target_id}",
                exc_info=True,
            )

    def register_rag_usage(
        self, session_id: str, doc_sources: List[Dict[str, Any]]
    ) -> None:
        """
        Record that specific documents were used in a chat session.

        Creates or updates the Session node and links it to Document nodes
        via 'USED_DOCUMENT' relationships.

        Args:
            session_id: The active chat session ID.
            doc_sources: List of source dictionaries containing 'document_id'.
        """
        # Attempt to delegate to doc_ops if functionality there
        if hasattr(self._doc_ops, "register_rag_usage"):
            return self._doc_ops.register_rag_usage(session_id, doc_sources)

        if not self.client.is_enabled():
            return
        try:
            self.client.upsert_node(
                session_id, labels=["Session"], properties={"name": session_id}
            )
            for src in doc_sources:
                doc_id = src.get("document_id") or src.get("id")
                if doc_id:
                    self.client.upsert_edge(session_id, "USED_DOCUMENT", doc_id)
        except Exception:
            logger.warning("GraphService: register_rag_usage failed", exc_info=True)

    def reason(self, intent: str, entities: List[str]) -> Dict[str, Any]:
        """
        Perform a reasoning operation over the graph neighborhood.

        Finds related entities and their summaries to provide context
        for reasoning tasks.

        Args:
            intent: The reasoning goal (currently generalized).
            entities: List of entity names or IDs to search from.

        Returns:
            Dict[str, Any]: Result dictionary with 'reasoning_context'.
        """
        if not self.client.is_enabled():
            return {"status": "disabled", "reasoning_context": ""}

        if not entities:
            return {"status": "success", "reasoning_context": "No entities provided."}

        # Default: General Neighborhood Search
        context_parts = []
        cypher = """
        UNWIND $entities as entity
        MATCH (n)-[r]-(m)
        WHERE n.id CONTAINS entity OR n.name CONTAINS entity
        RETURN n.id, n.summary, type(r), m.summary
        LIMIT 20
        """
        try:
            results = self.client.query(cypher, {"entities": entities})
            if results:
                block = "Knowledge Context:\n"
                seen = set()
                for row in results:
                    n_id, n_sum, rel, m_sum = row[0], row[1], row[2], row[3]
                    line = f"- {n_sum or n_id} -[{rel}]-> {m_sum}"
                    if line not in seen:
                        block += line + "\n"
                        seen.add(line)
                context_parts.append(block)
        except Exception:
            logger.warning(f"GraphService: generic reasoning failed for {entities}")

        return {
            "status": "success",
            "reasoning_context": "\n\n".join(context_parts)
            if context_parts
            else "No specific graph context found.",
        }

    def _current_timestamp(self) -> str:
        """
        Get the current ISO timestamp.

        Returns:
            str: formatted timestamp.
        """
        return current_timestamp()
