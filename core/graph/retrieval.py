"""
Structural Knowledge Retrieval and Visualization.

Implements complex graph traversal and data extraction logic. Specializes
in subgraph discovery for interactive visualizations and relational
context injection for LLM prompts, with built-in support for
style-aware node processing.
"""

from __future__ import annotations

import re
from core.observability.logging import get_logger
from typing import Any, Dict, List, Optional, Callable

logger = get_logger(__name__)


def get_subgraph_for_node(
    query_fn: Callable,
    node_id: str,
    hops: int = 1,
    limit: int = 100,
) -> Dict[str, Any]:
    """
    Retrieve the immediate neighborhood (subgraph) of a specific node.

    Args:
        query_fn: The function to execute Cypher queries.
        node_id: The ID of the center node.
        hops: Number of hops to traverse (currently optimized for 1 hop).
        limit: Max number of relationships to return to avoid overfetching.

    Returns:
        A dictionary containing "nodes" and "links" suitable for force-graph visualization.
        Format:
        {
            "nodes": [{"id": "...", "label": "...", "group": "...", ...}],
            "links": [{"source": "...", "target": "...", "label": "..."}]
        }
    """
    # Cypher query to get the node and its immediate neighbors
    # We use OPTIONAL MATCH to still return the center node if it has no neighbors
    cypher = f"MATCH (n {{id: $id, tenant_id: $tenant_id}}) OPTIONAL MATCH (n)-[r]-(m {{tenant_id: $tenant_id}}) RETURN n, r, m LIMIT {limit}"

    try:
        results = query_fn(cypher, {"id": node_id})
        logger.debug(f"[get_subgraph_for_node] Raw results for {node_id}: {results}")
        logger.debug(
            f"[get_subgraph_for_node] Results type: {type(results)}, length: {len(results) if results else 0}"
        )
    except Exception as e:
        logger.error(f"[graphdb] Error fetching subgraph for {node_id}: {e}")
        return {"nodes": [], "links": [], "legend": NODE_STYLES}

    nodes_map: Dict[str, Any] = {}
    links: List[Dict[str, Any]] = []

    if not results:
        logger.warning(f"[get_subgraph_for_node] No results returned for {node_id}")
        return {"nodes": [], "links": [], "legend": NODE_STYLES}

    # RedisGraph compact format: [header, [data_rows...], stats]
    # Header contains schema information: [1, [label_strings...], [property_strings...]] (structure varies by client version)
    # We will try to extract label and property mappings from header if possible.

    header = results[0] if len(results) > 0 else []
    data_rows = results[1] if len(results) > 1 else []

    label_map = {}
    prop_map = {}

    try:
        # Relaxed check: ensure it's a list and has at least the labels array (index 1)
        # header[0] is usually simple int (1), we skip it
        if isinstance(header, list) and len(header) >= 2:
            raw_labels = header[1]
            raw_props = []
            if len(header) > 2:
                raw_props = header[2]

            if isinstance(raw_labels, list):
                for i, label in enumerate(raw_labels):
                    label_map[i] = str(label)

            if isinstance(raw_props, list):
                for i, prop in enumerate(raw_props):
                    prop_map[i] = str(prop)

            logger.debug(
                f"[get_subgraph_for_node] Extracted schema - Labels({len(label_map)}): {label_map}, Props({len(prop_map)})"
            )
        else:
            logger.warning(
                f"[get_subgraph_for_node] Header format unrecognized or short: {header}"
            )
    except Exception as e:
        logger.warning(
            f"[get_subgraph_for_node] Failed to extract schema from header: {e}"
        )

    if not data_rows:
        logger.warning(f"[get_subgraph_for_node] No data rows in results for {node_id}")
        return {"nodes": [], "links": [], "legend": NODE_STYLES}

    logger.debug(f"[get_subgraph_for_node] Processing {len(data_rows)} data rows")

    for row in data_rows:
        # Expected row format: [[n_data], [r_data], [m_data]]
        # where each element is the RedisGraph compact representation

        # Check if we have a valid result row
        if not isinstance(row, list) or len(row) < 1:
            continue

        # 1. Process Center Node (n)
        center_node = row[0]
        if center_node:
            # Depending on the client version/library, this might be a Node object or property map
            _process_node(center_node, nodes_map, label_map, prop_map, is_center=True)

        # If we have interactions
        if len(row) >= 3:
            rel = row[1]
            neighbor = row[2]

            if rel and neighbor:
                _process_node(neighbor, nodes_map, label_map, prop_map)

                # Process Relationship
                # Need source and target IDs to build the link
                # Note: valid link requires both nodes to be present.

                # In many RedisGraph clients, 'rel' object contains src_node and dest_node IDs
                # OR we might have to infer from the query structure if generic.
                # Here we assume standard FalkorDB/RedisGraph python client "Edge" or "Relationship" object request
                # However, since we returned (n)-[r]-(m), we know n and m are connected.
                # BUT we don't know the direction just from 'n' and 'm' unless we check relation properties.

                # Let's try to extract IDs from the objects.
                n_id = _get_id(center_node, prop_map)
                m_id = _get_id(neighbor, prop_map)

                if n_id and m_id:
                    # Determine relationship type
                    rel_type = "RELATED"
                    if hasattr(rel, "relation"):
                        rel_type = rel.relation
                    elif isinstance(rel, list) and len(rel) > 1:
                        # Raw format fallback? Unlikely with current client
                        rel_type = str(rel[1])
                        # If relation type is an ID, we might need relation mapping too, but let's stick to simple string fallback

                    # We can add a link. direction doesn't strictly matter for visual graph
                    # unless we want arrows.
                    # safer to just link n and m for visualization context.
                    links.append({"source": n_id, "target": m_id, "label": rel_type})

    # Convert nodes map to list
    return {"nodes": list(nodes_map.values()), "links": links, "legend": NODE_STYLES}


# Centralized styling configuration for Frontend consistency
# Centralized styling configuration for Frontend consistency
NODE_STYLES = {
    "Document": {"color": "#1e40af", "label": "Document"},
    "Unknown": {"color": "#6b7280", "label": "Unknown"},
}


# Priority order for labels (high priority first)
# Lower integer value = Higher priority
LABEL_PRIORITY = {
    "Document": 1,
    "Unknown": 10,
}


def _process_node(
    node_obj: Any,
    nodes_map: Dict[str, Any],
    label_map: Dict[int, str],
    prop_map: Dict[int, str],
    is_center: bool = False,
) -> None:
    """Helper to parse node object and add to map."""
    # logger.debug(
    #    f"[_process_node] node_obj type: {type(node_obj)}, value: {node_obj if not isinstance(node_obj, (list, dict)) or len(str(node_obj)) < 200 else str(node_obj)[:200]}"
    # )

    node_id = _get_id(node_obj, prop_map)
    if not node_id:
        logger.warning("[_process_node] Could not extract ID from node_obj")
        return

    if node_id not in nodes_map:
        # Extract properties from RedisGraph compact format
        props = _extract_properties(node_obj, prop_map)

        # Determine label/group from RedisGraph compact format
        labels = _extract_labels(node_obj, label_map)

        # --- FIXED: Use Priority Logic for Group ---
        # Sort labels by priority (lowest value first)
        if labels:
            sorted_labels = sorted(
                labels, key=lambda label: LABEL_PRIORITY.get(label, 99)
            )
            # Pick the highest priority label as the group
            group = sorted_labels[0]
        else:
            group = "Unknown"

        # --- Heuristic Fallback for Group/Label ---
        if group == "Unknown" or group.startswith("Label_") or group == "Document":
            if (
                "fingerprint" in props
                or "chunk_ids" in props
                or str(props.get("path", "")).endswith(".md")
                or str(props.get("path", "")).endswith(".pdf")
            ):
                group = "Document"

        # Find a suitable display label
        label_text = (
            props.get("label")
            or props.get("name")
            or props.get("title")
            or props.get("summary")
            or node_id
        )

        nodes_map[node_id] = {
            "id": node_id,
            "label": label_text,
            "group": group,
            "is_center": is_center,
            "properties": props,
        }


def _get_id(node_obj: Any, prop_map: Optional[Dict[int, str]] = None) -> Optional[str]:
    """Helper to reliably extract 'id' property from node in RedisGraph compact format."""
    # Handle object-based format (if client returns objects)
    if hasattr(node_obj, "properties") and "id" in node_obj.properties:
        return node_obj.properties["id"]
    if isinstance(node_obj, dict) and "id" in node_obj:
        return node_obj["id"]

    # Handle RedisGraph compact format: [node_type, [internal_id, [label_ids], [[prop_id, prop_type, prop_value], ...]]]
    if isinstance(node_obj, list) and len(node_obj) >= 2:
        node_data = node_obj[1]  # Get the node data part
        if isinstance(node_data, list) and len(node_data) >= 3:
            properties = node_data[2]  # Get properties array
            if isinstance(properties, list):
                # Properties are [[prop_id, prop_type, prop_value], ...]
                # We search for property name "id" using prop_map if available
                id_prop_idx = -1
                if prop_map:
                    for idx, name in prop_map.items():
                        if name == "id":
                            id_prop_idx = idx
                            break

                # Fallback: Property ID 0 is OFTEN the 'id' field, but dynamic mapping is safer
                target_idx = id_prop_idx if id_prop_idx != -1 else 0

                for prop in properties:
                    if isinstance(prop, list) and len(prop) >= 3:
                        prop_id, _, prop_value = prop[0], prop[1], prop[2]
                        if prop_id == target_idx:  # ID property
                            return prop_value

    return None


def _extract_properties(node_obj: Any, prop_map: Dict[int, str]) -> Dict[str, Any]:
    """Extract all properties from RedisGraph compact format node."""
    props = {}

    # Handle object-based format
    if hasattr(node_obj, "properties"):
        return node_obj.properties
    if isinstance(node_obj, dict):
        return node_obj

    # Handle RedisGraph compact format
    if isinstance(node_obj, list) and len(node_obj) >= 2:
        node_data = node_obj[1]
        if isinstance(node_data, list) and len(node_data) >= 3:
            properties = node_data[2]
            if isinstance(properties, list):
                for prop in properties:
                    if isinstance(prop, list) and len(prop) >= 3:
                        prop_id, _, prop_value = prop[0], prop[1], prop[2]
                        # Use dynamic mapping, or fallback to default map for backward compat or if mapping failed
                        prop_name = prop_map.get(prop_id)

                        # Fallback hardcoded map if header parsing failed
                        if not prop_name:
                            prop_names_fallback = {
                                0: "id",
                                1: "name",  # General name/title
                                2: "type",
                                3: "path",
                                5: "label",  # Display label (e.g. Risk: ...) - maps to node label
                                12: "content",
                                20: "description",
                                25: "title",
                                38: "summary",
                            }
                            prop_name = prop_names_fallback.get(
                                prop_id, f"prop_{prop_id}"
                            )

                        props[prop_name] = prop_value

    return props


def _extract_labels(node_obj: Any, label_map: Dict[int, str]) -> List[str]:
    """Extract labels from RedisGraph compact format node."""
    # Handle object-based format
    if hasattr(node_obj, "labels"):
        return node_obj.labels

    # Handle RedisGraph compact format: [node_type, [internal_id, [label_ids], ...]]
    if isinstance(node_obj, list) and len(node_obj) >= 2:
        node_data = node_obj[1]
        if isinstance(node_data, list) and len(node_data) >= 2:
            label_ids = node_data[1]
            if isinstance(label_ids, list):
                # FIXED: Merge dynamic map (if present) with Verified ID fallback
                # This ensures that even if label_map is partial, we don't get Label_5

                resolved_labels = []

                label_names_fallback = {
                    0: "Document",
                }

                for lid in label_ids:
                    l_val = None
                    # Try dynamic provided by header first
                    if label_map:
                        l_val = label_map.get(lid)

                    # If failed, try Verified fallback
                    if not l_val:
                        l_val = label_names_fallback.get(lid)

                    # Last resort
                    if not l_val:
                        l_val = f"Label_{lid}"

                    resolved_labels.append(l_val)

                return resolved_labels

    return []


def search_node_by_property(query_fn: Callable, prop: str, value: str) -> Optional[str]:
    """
    Search for a node ID by a specific property value.

    Args:
        query_fn: Function to execute Cypher queries.
        prop: The property name to search for (e.g. 'path', 'name').
        value: The property value to match.

    Returns:
        The node ID if found, else None.
    """
    # Note: We use dynamic property key in WHERE clause which standard Cypher parameters
    # might not support for keys. However, for VALUES it should be parameterized.
    # To be safe and avoid injection, we validate 'prop' is a valid identifier.
    # Valid identifiers: start with letter or underscore, followed by letters, numbers, underscores
    if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", prop):
        logger.warning(f"[search_node] Invalid property name: {prop}")
        return None

    # Cypher: MATCH (n {tenant_id: $tenant_id}) WHERE n.prop = $val RETURN n.id LIMIT 1
    cypher = (
        f"MATCH (n {{tenant_id: $tenant_id}}) WHERE n.{prop} = $val RETURN n.id LIMIT 1"
    )

    try:
        results = query_fn(cypher, {"val": value})
        if results and len(results) > 0:
            # Result format: [[id_value]]
            row = results[0]
            if isinstance(row, list) and len(row) > 0:
                return str(row[0])
    except Exception as e:
        logger.warning(f"[search_node] Error searching by {prop}={value}: {e}")

    return None
