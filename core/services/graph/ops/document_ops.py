"""Document operations for graph database management."""

from __future__ import annotations
from core.observability.logging import get_logger
from typing import Any, Dict, List, Optional
from core.graph import GraphDb
from .utils import current_timestamp

logger = get_logger(__name__)


class DocumentOperations:
    """Handles document node operations in the knowledge graph."""

    def __init__(self, graph_client: GraphDb) -> None:
        """
        Initialize document operations.

        Args:
            graph_client: The active Graph database client.
        """
        self.client = graph_client

    def upsert_document(
        self,
        doc_id: str,
        doc_type: str,
        path: str,
        category: Optional[str] = None,
        properties: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Create or update a Document node in the graph.

        Handles label assignment (e.g., 'Analysis', 'KnowledgeBase'),
        metadata updates, and visual state management for the UI.

        Args:
            doc_id: Unique identifier for the document.
            doc_type: Semantic type (e.g., 'pdf', 'text').
            path: Storage URI or file path.
            category: Optional lifecycle category.
            properties: Additional metadata for the node.
        """
        if not self.client.is_enabled():
            return

        try:
            # Determine display name for visualization
            display_name = (
                properties.get("original_name")
                or properties.get("file_name")
                or path.split("/")[-1]
                if properties
                else path.split("/")[-1]
            )

            props = {
                "type": doc_type,
                "path": path,
                "last_updated": current_timestamp(),
                "name": display_name,
                "label_display": display_name,
            }
            labels: List[str] = []
            is_kb_transition = False
            if category:
                # Add capitalized category as a label for visualization (e.g. "Analysis", "Knowledgebase")
                # Normalize: knowledge-base -> KnowledgeBase
                clean_cat = category.replace("-", " ").title().replace(" ", "")
                # Prepend to make it PRIMARY label for visualization coloring
                labels.insert(0, clean_cat)
                props["category"] = category

                # Mark if this is a KB transition (will remove Analysis label after upsert)
                if clean_cat == "KnowledgeBase":
                    is_kb_transition = True

            # Risk handling
            risk_level = properties.get("risk_level") if properties else None
            if risk_level:
                props["risk_level"] = risk_level
                # Visual flag or special label for high risk
                if risk_level.lower() == "high":
                    labels.append("HighRisk")
                    props["label_display"] = f"⚠️ {props['label_display']}"
                elif risk_level.lower() == "medium":
                    labels.append("MediumRisk")

            # Author handling
            author = properties.get("author") if properties else None
            if author:
                props["author"] = author

            # Ensure Document label is always present (for GC safety)
            if "Document" not in labels:
                labels.append("Document")

            if properties:
                props.update(properties)

            self.client.upsert_node(doc_id, labels=labels, properties=props)

            # CRITICAL: Remove Analysis label AFTER upsert_node to ensure proper transition
            # This must happen after upsert_node to avoid race conditions
            if is_kb_transition:
                try:
                    self.client.query(
                        "MATCH (n {id: $id}) REMOVE n:Analysis", {"id": doc_id}
                    )
                    logger.info(
                        f"GraphAgent: transitioned document {doc_id} from Analysis to KnowledgeBase"
                    )
                except Exception as e:
                    logger.warning(
                        f"GraphAgent: failed to remove Analysis label from {doc_id}: {e}"
                    )

            logger.debug(
                f"GraphAgent: upsert_document {doc_id} (category={category}, labels={labels})"
            )

        except Exception:
            logger.warning(
                f"GraphAgent: failed to upsert document {doc_id}", exc_info=True
            )

    def transition_document_to_kb(
        self, doc_id: str, new_path: Optional[str] = None
    ) -> None:
        """
        Transition a document from Analysis to KnowledgeBase.
        Removes 'Analysis' label, adds 'KnowledgeBase', and updates category/path.
        """
        if not self.client.is_enabled():
            return

        try:
            # Explicitly remove Analysis label and set KnowledgeBase
            # This is cleaner than upsert_node which only adds labels
            cypher = (
                "MATCH (n {id: $id}) "
                "REMOVE n:Analysis "
                "SET n:KnowledgeBase, n.category='knowledge-base', "
                "n.last_updated=$now" + (", n.path=$path" if new_path else "")
            )
            params = {"id": doc_id, "now": current_timestamp(), "path": new_path}
            self.client.query(cypher, params)
            logger.info(f"GraphAgent: transitioned document {doc_id} to KnowledgeBase")
        except Exception:
            logger.warning(
                f"GraphAgent: failed to transition doc {doc_id} to KB", exc_info=True
            )
