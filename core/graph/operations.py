"""
Basic node and edge CRUD operations for FalkorDB/RedisGraph.

Provides fundamental graph database operations: create, read, update, delete nodes and edges.
"""

from __future__ import annotations

from core.observability import get_logger
from typing import Any, Mapping, Sequence, Optional, Dict, Callable

from .query_builder import format_labels, sanitize_label

logger = get_logger(__name__)


def get_node(
    query_fn: Callable,
    node_id: str,
) -> Optional[Dict[str, Any]]:
    """
    Retrieve node properties by ID.

    Args:
        query_fn: Query execution function
        node_id: Node identifier

    Returns:
        Node properties dict or None if not found
    """
    try:
        # Safe parameterized query
        query = "MATCH (n {id: $id, tenant_id: $tenant_id}) RETURN n"
        result = query_fn(query, {"id": node_id})

        if result and len(result) > 0:
            # result is usually [[Node(id=1, ...)]]
            # we need to be careful about unwrapping
            first_row = result[0]
            if isinstance(first_row, list) and len(first_row) > 0:
                node = first_row[0]
                if hasattr(node, "properties"):
                    return node.properties
                # Fallback if raw list/dict
                if isinstance(node, (dict, list)):
                    # If node is a list inside a list (raw format), might need further inspection
                    # but usually client returns Node object
                    return dict(node) if isinstance(node, dict) else {}
            elif hasattr(first_row, "properties"):  # If flattened
                return first_row.properties

        return None
    except Exception as exc:
        logger.warning(f"[graphdb] get_node failed for {node_id}: {exc}")
        return None


def upsert_node(
    query_fn: Callable,
    node_id: str,
    *,
    labels: Sequence[str] | None = None,
    properties: Mapping[str, Any] | None = None,
) -> None:
    """
    Create or update a node with stable ID and mergeable properties.

    Args:
        query_fn: Query execution function
        node_id: Node identifier
        labels: Optional list of node labels
        properties: Optional property dictionary
    """
    # Match by ID only to prevent duplicates if labels change
    # Then SET the new labels. Note: This adds labels, it does not remove old ones.
    label_clause = format_labels(labels or [])
    cypher = f"MERGE (n {{id: $id, tenant_id: $tenant_id}}) SET n += $props{', n' + label_clause if label_clause else ''} RETURN n.id"
    query_fn(cypher, {"id": node_id, "props": properties or {}})


def upsert_edge(
    query_fn: Callable,
    source_id: str,
    relationship: str,
    target_id: str,
    *,
    properties: Mapping[str, Any] | None = None,
) -> None:
    """
    Create or update a directed relationship between two nodes.

    Args:
        query_fn: Query execution function
        source_id: Source node identifier
        relationship: Relationship type
        target_id: Target node identifier
        properties: Optional relationship properties
    """
    rel = sanitize_label(relationship) or "RELATED"
    cypher = (
        f"MERGE (s {{id: $source_id, tenant_id: $tenant_id}}) "
        f"MERGE (t {{id: $target_id, tenant_id: $tenant_id}}) "
        f"MERGE (s)-[r:{rel}]->(t) "
        "SET r += $props "
        "RETURN type(r)"
    )
    query_fn(
        cypher,
        {
            "source_id": source_id,
            "target_id": target_id,
            "props": properties or {},
        },
    )


def delete_node(query_fn: Callable, node_id: str) -> None:
    """
    Delete a node and all its incident relationships.

    Args:
        query_fn: Query execution function
        node_id: Node identifier to delete
    """
    cypher = "MATCH (n {id: $id, tenant_id: $tenant_id}) DETACH DELETE n"
    query_fn(cypher, {"id": node_id})


def delete_orphan_nodes(query_fn: Callable) -> int:
    """
    Delete orphan nodes (without relationships) from the graph.
    Explicitly excludes Document nodes for safety.

    Args:
        query_fn: Query execution function

    Returns:
        Number of nodes deleted
    """
    # Query: Find nodes that have NO relationships (--) and are NOT Documents.
    cypher = "MATCH (n) WHERE NOT (n)--() AND NOT n:Document AND n.tenant_id = $tenant_id DELETE n RETURN count(n) as deleted"
    try:
        result = query_fn(cypher)
        if result and len(result) > 0:
            # result is usually [[deleted_count]]
            first_row = result[0]
            if isinstance(first_row, list) and len(first_row) > 0:
                return int(first_row[0])
        return 0
    except Exception as exc:
        logger.warning(f"[graphdb] delete_orphan_nodes failed: {exc}")
        return 0
