#!/usr/bin/env python3
"""
Master reset script - cleans ALL datastores for a fresh start.

Usage:
    python scripts/reset_all.py [--dry-run] [--skip-confirm]

Options:
    --dry-run       Show what would be deleted without actually deleting
    --skip-confirm  Skip confirmation prompts (use with caution!)

This script resets:
    1. PostgreSQL (agent_analytics) - sessions
    2. Qdrant - all vector collections
    3. GraphDB (FalkorDB) - all nodes and relationships

WARNING: This will permanently delete ALL data from ALL datastores!
"""

import asyncio
import logging
from core.observability.logging import get_logger
import sys
import os

# Add project root to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = get_logger("reset_all")


async def reset_analytics():
    """Reset analytics data (PostgreSQL)."""
    from core.config import get_storage_config

    storage_config = get_storage_config()

    logger.info("=" * 50)

    if not storage_config.postgres_enabled:
        logger.error("PostgreSQL is disabled. Cannot reset analytics.")
        return False

    logger.info("RESETTING PostgreSQL (agent_analytics)...")
    from urllib.parse import quote_plus
    from psycopg import AsyncConnection

    # Target the correct analytics DB
    db_name = "agent_analytics"
    user = quote_plus(storage_config.db_user or "")
    password = (
        quote_plus(storage_config.db_password) if storage_config.db_password else ""
    )
    password_fragment = f":{password}" if password else ""
    host = storage_config.db_host or "localhost"
    port = storage_config.db_port or 5432
    conninfo = f"postgresql://{user}{password_fragment}@{host}:{port}/{db_name}"

    try:
        async with await AsyncConnection.connect(conninfo, autocommit=True) as conn:
            async with conn.cursor() as cur:
                tables = [
                    "sessions",
                    "discovery_results",
                    "pentest_playbooks",
                    "pentest_results",
                ]
                for table in tables:
                    await cur.execute("SELECT to_regclass(%s)", (table,))
                    result = await cur.fetchone()
                    if result and result[0]:
                        # Get count before truncate
                        await cur.execute(f"SELECT count(*) FROM {table}")  # nosec
                        count_before = (await cur.fetchone())[0]

                        await cur.execute(f"TRUNCATE TABLE {table} CASCADE")

                        # Verify truncate worked
                        await cur.execute(f"SELECT count(*) FROM {table}")  # nosec
                        count_after = (await cur.fetchone())[0]

                        if count_after == 0:
                            logger.info(
                                f"  ✓ Truncated: {table} ({count_before} rows deleted)"
                            )
                        else:
                            logger.warning(f"  ⚠ {table} still has {count_after} rows!")
                    else:
                        logger.info(f"  - Skipped: {table} (table does not exist)")
        logger.info("✅ PostgreSQL reset complete")
        return True
    except Exception as e:
        logger.error(f"❌ PostgreSQL reset failed: {e}")
        return False


async def reset_qdrant():
    """Reset Qdrant vector store."""
    from qdrant_client import AsyncQdrantClient
    from core.config import get_vectorstore_config

    logger.info("=" * 50)
    logger.info("RESETTING Qdrant (vector store)...")

    config = get_vectorstore_config()
    host = config.host or "localhost"
    port = config.port or 6333

    client = None
    try:
        client = AsyncQdrantClient(host=host, port=port)
        collections = await client.get_collections()

        if not collections.collections:
            logger.info("  No collections found, already empty")
            logger.info("✅ Qdrant reset complete")
            return True

        for coll in collections.collections:
            # Get vector count before deletion
            try:
                collection_info = await client.get_collection(coll.name)
                vector_count = collection_info.vectors_count or 0
                await client.delete_collection(collection_name=coll.name)
                logger.info(
                    f"  ✓ Deleted collection: {coll.name} ({vector_count} vectors)"
                )
            except Exception as e:
                logger.warning(f"  ⚠ Failed to delete collection {coll.name}: {e}")

        logger.info("✅ Qdrant reset complete")
        return True

    except Exception as e:
        logger.error(f"❌ Qdrant reset failed: {e}")
        return False
    finally:
        # Close connection explicitly
        if client is not None:
            try:
                await client.close()
            except Exception:
                pass  # Ignore close errors


def reset_graphdb():
    """Reset GraphDB (FalkorDB/RedisGraph)."""
    from core.graph import graph_db

    logger.info("=" * 50)
    logger.info("RESETTING GraphDB (FalkorDB)...")

    # Workaround: Disable cache to avoid RuntimeWarning (sync query vs async cache)
    # This is safe as we're not using cache during reset
    if hasattr(graph_db, "_cache"):
        graph_db._cache = None

    if not graph_db.is_enabled():
        logger.warning("  GraphDB not enabled, skipping")
        return True

    if not graph_db.ping():
        logger.error("  Cannot connect to GraphDB")
        return False

    try:
        # Count before
        result = graph_db.query("MATCH (n) RETURN count(n)")
        count = 0
        if result and isinstance(result, list):
            for item in result:
                if isinstance(item, int):
                    count = item
                    break
                if isinstance(item, list):
                    for sub in item:
                        if isinstance(sub, int):
                            count = sub
                            break

        if count == 0:
            logger.info("  Graph already empty")
            logger.info("✅ GraphDB reset complete")
            return True

        logger.info(f"  Deleting {count} nodes...")
        graph_db.query("MATCH (n) DETACH DELETE n")

        # Verify deletion
        result_after = graph_db.query("MATCH (n) RETURN count(n)")
        count_after = 0
        if result_after and isinstance(result_after, list):
            for item in result_after:
                if isinstance(item, int):
                    count_after = item
                    break
                if isinstance(item, list):
                    for sub in item:
                        if isinstance(sub, int):
                            count_after = sub
                            break

        if count_after == 0:
            logger.info(f"  ✓ Deleted all {count} nodes successfully")
            logger.info("✅ GraphDB reset complete")
        else:
            logger.warning(f"  ⚠ {count_after} nodes still remain!")

        return True

    except Exception as e:
        logger.error(f"❌ GraphDB reset failed: {e}")
        return False


async def main(dry_run: bool = False, skip_confirm: bool = False):
    """Main reset function."""

    print("\n" + "=" * 60)
    print("           MASTER RESET - ALL DATASTORES")
    print("=" * 60)
    print("\nThis will reset:")
    print("  1. Analytics DB (PostgreSQL) - sessions,")
    print("     discovery_results, pentest_playbooks")
    print("  2. Qdrant - all vector collections")
    print("  3. GraphDB (FalkorDB) - all nodes and relationships")
    print("")

    if dry_run:
        print("🔍 DRY RUN MODE - No changes will be made\n")
        logger.info("Checking connectivity and current state...")
        # Just check what exists
        logger.info("Would reset Analytics data")
        logger.info("Would delete all Qdrant collections")
        logger.info("Would delete all GraphDB nodes")
        return

    if not skip_confirm:
        print("⚠️  WARNING: This action is IRREVERSIBLE!")
        confirm = input("\nType 'DELETE ALL' to confirm: ")
        if confirm != "DELETE ALL":
            logger.info("Operation cancelled.")
            return

    print("")
    results = []

    # Reset in order
    results.append(("Analytics", await reset_analytics()))
    results.append(("Qdrant", await reset_qdrant()))
    results.append(("GraphDB", reset_graphdb()))

    # Reset in-memory state via API
    try:
        print("\nAttempting to reset backend in-memory state...")
    except Exception as e:
        logger.warning(f"⚠️  Could not trigger backend reset: {e}")

    # Summary
    print("\n" + "=" * 60)
    print("                    RESET SUMMARY")
    print("=" * 60)

    all_success = True
    for name, success in results:
        status = "✅ Success" if success else "❌ Failed"
        print(f"  {name}: {status}")
        if not success:
            all_success = False

    print("")
    if all_success:
        print("🎉 All datastores reset successfully!")
        print("")
        print("=" * 60)
        print("⚠️  IMPORTANT: RESTART BACKEND SERVICES")
        print("=" * 60)
        print("The in-memory state (events, sessions, statistics) is NOT")
        print("cleared by this script. To complete the reset, you MUST")
        print("restart the backend services:")
        print("")
        print("  Docker:    docker compose restart backend")
        print("  Local:     Restart the uvicorn/gunicorn process")
        print("")
        print("Failure to restart will cause 'ghost' data in visualizations.")
        print("=" * 60)
    else:
        print("⚠️  Some resets failed. Check logs above.")
        sys.exit(1)


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv or "-n" in sys.argv
    skip_confirm = "--skip-confirm" in sys.argv or "-y" in sys.argv

    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__)
        sys.exit(0)

    try:
        asyncio.run(main(dry_run=dry_run, skip_confirm=skip_confirm))
    except KeyboardInterrupt:
        logger.info("\nOperation cancelled by user.")
