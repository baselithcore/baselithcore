"""
Static Source Code Knowledge Mapping.

Specializes in representing software architecture within the knowledge
graph. Maps file hierarchies, class definitions, and function
dependencies into a relational model for structural analysis and
impact assessment.
"""

from __future__ import annotations

from typing import Any, Mapping, Callable


def upsert_code_node(
    upsert_node_fn: Callable,
    node_id: str,
    label: str,
    name: str,
    file_path: str,
    *,
    properties: Mapping[str, Any] | None = None,
) -> None:
    """
    Create or update a node representing a code entity (File, Class, Function).

    Args:
        upsert_node_fn: Node upsert function
        node_id: Node identifier
        label: Primary label (e.g., "CodeFile", "CodeClass", "CodeFunction")
        name: Entity name
        file_path: Source file path
        properties: Optional additional properties
    """
    props = dict(properties or {})
    props["name"] = name
    props["file_path"] = file_path

    # Ensure label starts with Code to avoid collisions if generic names used
    # We can enforce specific labels like CodeFile, CodeClass, CodeFunction
    # But we trust the caller to pass correct labels as per plan.

    upsert_node_fn(node_id, labels=[label, "CodeComponent"], properties=props)


def upsert_code_relation(
    upsert_edge_fn: Callable,
    source_id: str,
    relation_type: str,
    target_id: str,
) -> None:
    """
    Create a relationship between two code components (e.g., DEFINES, CONTAINS).

    Args:
        upsert_edge_fn: Edge upsert function
        source_id: Source node identifier
        relation_type: Relationship type
        target_id: Target node identifier
    """
    upsert_edge_fn(source_id, relation_type, target_id)
