#!/usr/bin/env python3
"""
Reset GraphDB (FalkorDB/RedisGraph) - clears the entire graph.

Usage:
    python scripts/reset_graphdb.py [--dry-run]

Options:
    --dry-run   Show graph stats without actually deleting

WARNING: This will permanently delete ALL nodes and relationships!
"""

import logging
from core.observability.logging import get_logger
import sys
import os

# Add project root to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.graph import graph_db

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = get_logger("reset_graphdb")


def get_graph_stats():
    """Get current graph statistics."""
    stats = {"nodes": 0, "relationships": 0, "labels": []}

    try:
        # Count nodes
        result = graph_db.query("MATCH (n) RETURN count(n)")
        if result and result[0]:
            # Extract count from nested list
            def extract_int(x):
                if isinstance(x, int):
                    return x
                if isinstance(x, list):
                    for item in x:
                        val = extract_int(item)
                        if val is not None:
                            return val
                return None

            stats["nodes"] = extract_int(result) or 0

        # Count relationships
        result = graph_db.query("MATCH ()-[r]->() RETURN count(r)")
        if result:
            stats["relationships"] = extract_int(result) or 0

        # Get labels (node types)
        result = graph_db.query("CALL db.labels()")
        if result and isinstance(result, list):
            # Labels typically come as [[label1], [label2], ...]
            labels = []
            for item in result:
                if isinstance(item, list):
                    for sub in item:
                        if isinstance(sub, str):
                            labels.append(sub)
                        elif isinstance(sub, list):
                            labels.extend([s for s in sub if isinstance(s, str)])
            stats["labels"] = list(set(labels))

    except Exception as e:
        logger.warning(f"Could not get full stats: {e}")

    return stats


def reset_graphdb(dry_run: bool = False):
    """
    Reset GraphDB by deleting all nodes and relationships.

    Args:
        dry_run: If True, only show stats without deleting
    """
    if not graph_db.is_enabled():
        logger.error("❌ GraphDB is not enabled. Check GRAPH_DB_ENABLED in .env")
        sys.exit(1)

    # Check connectivity
    if not graph_db.ping():
        logger.error("❌ Cannot connect to GraphDB. Is Redis/FalkorDB running?")
        sys.exit(1)

    logger.info(f"Connected to graph: {graph_db.graph_name}")

    # Get current stats
    stats = get_graph_stats()
    logger.info("Current graph contains:")
    logger.info(f"  - Nodes: {stats['nodes']}")
    logger.info(f"  - Relationships: {stats['relationships']}")
    if stats["labels"]:
        logger.info(f"  - Labels: {', '.join(stats['labels'])}")

    if stats["nodes"] == 0 and stats["relationships"] == 0:
        logger.info("Graph is already empty. Nothing to delete.")
        return

    if dry_run:
        logger.info("DRY RUN - No changes will be made.")
        return

    # Confirm before deletion
    print(
        f"\n⚠️  WARNING: This will delete {stats['nodes']} nodes and {stats['relationships']} relationships!"
    )
    confirm = input("Type 'yes' to confirm deletion: ")
    if confirm.lower() != "yes":
        logger.info("Operation cancelled.")
        return

    try:
        logger.info("Deleting all nodes and relationships...")

        # Use DETACH DELETE to remove nodes and their relationships
        graph_db.query("MATCH (n) DETACH DELETE n")

        logger.info("✅ Graph reset successfully.")

        # Verify deletion
        new_stats = get_graph_stats()
        logger.info(f"Graph now contains {new_stats['nodes']} nodes.")

    except Exception as e:
        logger.error(f"❌ Failed to reset graph: {e}")
        sys.exit(1)


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv or "-n" in sys.argv

    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__)
        sys.exit(0)

    try:
        reset_graphdb(dry_run=dry_run)
    except KeyboardInterrupt:
        logger.info("Operation cancelled by user.")
