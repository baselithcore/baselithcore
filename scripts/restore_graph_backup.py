#!/usr/bin/env python3
"""
Graph Database Restore Script

Restores graph database from JSON backup created by backup_graph.py
"""

import json
import sys
from pathlib import Path
import redis
from redis.commands.graph import Graph

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.config import get_storage_config

_storage_config = get_storage_config()
GRAPH_DB_URL = _storage_config.graph_db_url
GRAPH_DB_NAME = _storage_config.graph_db_name


def restore_graph(backup_file: Path, clear_existing: bool = False):
    """Restore graph database from JSON backup."""
    if not backup_file.exists():
        print(f"❌ Backup file not found: {backup_file}")
        return False

    print(f"📂 Loading backup from {backup_file}")
    with open(backup_file, "r", encoding="utf-8") as f:
        backup_data = json.load(f)

    print("📊 Backup info:")
    print(f"   Timestamp: {backup_data['timestamp']}")
    print(f"   Graph: {backup_data['graph_name']}")
    print(f"   Nodes: {len(backup_data['nodes'])}")
    print(f"   Relationships: {len(backup_data['relationships'])}")

    print(f"\n🔄 Connecting to graph database: {GRAPH_DB_URL}")

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

    if clear_existing:
        print("⚠️  Clearing existing graph data...")
        try:
            graph.query("MATCH (n) DETACH DELETE n")
            print("✅ Existing data cleared")
        except Exception as e:
            print(f"❌ Failed to clear existing data: {e}")
            return False

    # Restore nodes
    print(f"\n📦 Restoring {len(backup_data['nodes'])} nodes...")
    node_map = {}  # Map old IDs to new IDs

    for i, node in enumerate(backup_data["nodes"], 1):
        labels = ":".join(node["labels"])
        props = node["properties"]

        # Build property string
        prop_parts = []
        for key, value in props.items():
            if isinstance(value, str):
                prop_parts.append(f"{key}: '{value}'")
            elif isinstance(value, list):
                # Convert list to string representation
                list_str = str(value).replace("'", "\\'")
                prop_parts.append(f"{key}: '{list_str}'")
            else:
                prop_parts.append(f"{key}: {value}")

        prop_string = "{" + ", ".join(prop_parts) + "}" if prop_parts else ""

        query = f"CREATE (n:{labels} {prop_string}) RETURN id(n)"

        try:
            result = graph.query(query)
            new_id = result.result_set[0][0]
            node_map[node["id"]] = new_id

            if i % 100 == 0:
                print(f"   Restored {i}/{len(backup_data['nodes'])} nodes...")
        except Exception as e:
            print(f"❌ Failed to restore node {i}: {e}")
            print(f"   Query: {query}")
            continue

    print(f"✅ Restored {len(node_map)} nodes")

    # Restore relationships
    print(f"\n🔗 Restoring {len(backup_data['relationships'])} relationships...")
    restored_rels = 0

    for i, rel in enumerate(backup_data["relationships"], 1):
        source_id = node_map.get(rel["source_id"])
        target_id = node_map.get(rel["target_id"])

        if source_id is None or target_id is None:
            print(f"⚠️  Skipping relationship {i}: node not found")
            continue

        rel_type = rel["type"]
        props = rel.get("properties", {})

        # Build property string
        prop_parts = []
        for key, value in props.items():
            if isinstance(value, str):
                prop_parts.append(f"{key}: '{value}'")
            else:
                prop_parts.append(f"{key}: {value}")

        prop_string = "{" + ", ".join(prop_parts) + "}" if prop_parts else ""

        query = f"""
        MATCH (a), (b)
        WHERE id(a) = {source_id} AND id(b) = {target_id}
        CREATE (a)-[r:{rel_type} {prop_string}]->(b)
        """

        try:
            graph.query(query)
            restored_rels += 1

            if i % 100 == 0:
                print(
                    f"   Restored {restored_rels}/{len(backup_data['relationships'])} relationships..."
                )
        except Exception as e:
            print(f"❌ Failed to restore relationship {i}: {e}")
            continue

    print(f"✅ Restored {restored_rels} relationships")
    print("\n✅ Restore complete!")

    return True


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Restore FalkorDB graph from JSON backup"
    )
    parser.add_argument("backup_file", type=Path, help="Path to backup JSON file")
    parser.add_argument(
        "--clear", action="store_true", help="Clear existing graph data before restore"
    )

    args = parser.parse_args()

    if args.clear:
        confirm = input(
            "⚠️  This will DELETE all existing graph data. Continue? (yes/no): "
        )
        if confirm.lower() != "yes":
            print("❌ Restore cancelled")
            sys.exit(1)

    success = restore_graph(args.backup_file, args.clear)
    sys.exit(0 if success else 1)
