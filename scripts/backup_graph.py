#!/usr/bin/env python3
"""
Graph Database Backup Script

Exports the entire FalkorDB graph to JSON for backup purposes.
This allows rollback if migration fails.
"""

import json
import sys
from pathlib import Path
from datetime import datetime
import redis
from redis.commands.graph import Graph

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.config import get_storage_config

_storage_config = get_storage_config()
GRAPH_DB_URL = _storage_config.graph_db_url
GRAPH_DB_NAME = _storage_config.graph_db_name


def backup_graph(output_file: Path = None):
    """Backup graph database to JSON file."""
    if output_file is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_file = Path(f"data/graph_backup_{timestamp}.json")

    output_file.parent.mkdir(parents=True, exist_ok=True)

    print(f"🔄 Connecting to graph database: {GRAPH_DB_URL}")

    # Parse Redis URL
    if GRAPH_DB_URL.startswith("redis://"):
        url_parts = GRAPH_DB_URL.replace("redis://", "").split(":")
        host = url_parts[0]
        port = int(url_parts[1]) if len(url_parts) > 1 else 6379
    else:
        host = "localhost"
        port = 6379

    try:
        r = redis.Redis(host=host, port=port, decode_responses=True)
        r.ping()
        print("✅ Connected to Redis")
    except Exception as e:
        print(f"❌ Failed to connect to Redis: {e}")
        return False

    graph = Graph(r, GRAPH_DB_NAME)

    backup_data = {
        "timestamp": datetime.now().isoformat(),
        "graph_name": GRAPH_DB_NAME,
        "nodes": [],
        "relationships": [],
    }

    # Export all nodes
    print("📦 Exporting nodes...")
    node_query = """
    MATCH (n)
    RETURN 
        id(n) as node_id,
        labels(n) as labels,
        properties(n) as properties
    """

    try:
        result = graph.query(node_query)
        for record in result.result_set:
            node_id, labels, properties = record
            backup_data["nodes"].append(
                {"id": node_id, "labels": labels, "properties": properties}
            )
        print(f"✅ Exported {len(backup_data['nodes'])} nodes")
    except Exception as e:
        print(f"❌ Failed to export nodes: {e}")
        return False

    # Export all relationships
    print("🔗 Exporting relationships...")
    rel_query = """
    MATCH (a)-[r]->(b)
    RETURN 
        id(a) as source_id,
        id(b) as target_id,
        type(r) as rel_type,
        properties(r) as properties
    """

    try:
        result = graph.query(rel_query)
        for record in result.result_set:
            source_id, target_id, rel_type, properties = record
            backup_data["relationships"].append(
                {
                    "source_id": source_id,
                    "target_id": target_id,
                    "type": rel_type,
                    "properties": properties,
                }
            )
        print(f"✅ Exported {len(backup_data['relationships'])} relationships")
    except Exception as e:
        print(f"❌ Failed to export relationships: {e}")
        return False

    # Write to file
    print(f"💾 Writing backup to {output_file}")
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(backup_data, f, indent=2, ensure_ascii=False)

    print(f"✅ Backup complete: {output_file}")
    print(f"   Nodes: {len(backup_data['nodes'])}")
    print(f"   Relationships: {len(backup_data['relationships'])}")

    return True


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Backup FalkorDB graph to JSON")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Output file path (default: data/graph_backup_TIMESTAMP.json)",
    )

    args = parser.parse_args()

    success = backup_graph(args.output)
    sys.exit(0 if success else 1)
