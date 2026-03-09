#!/usr/bin/env python3
"""
Reset Qdrant vector store - deletes all collections.

Usage:
    python scripts/reset_qdrant.py [--dry-run]

Options:
    --dry-run   Show what would be deleted without actually deleting

WARNING: This will permanently delete ALL vector collections!
"""

import asyncio
import logging
from core.observability.logging import get_logger
import sys
import os

# Add project root to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qdrant_client import AsyncQdrantClient
from core.config import get_storage_config

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = get_logger("reset_qdrant")


async def reset_qdrant(dry_run: bool = False):
    """
    Reset Qdrant by deleting all collections.

    Args:
        dry_run: If True, only show what would be deleted
    """
    config = get_storage_config()

    host = config.qdrant_host or "localhost"
    port = config.qdrant_port or 6333

    logger.info(f"Connecting to Qdrant at {host}:{port}...")

    try:
        client = AsyncQdrantClient(host=host, port=port)

        # Get all collections
        collections_response = await client.get_collections()
        collections = [c.name for c in collections_response.collections]

        if not collections:
            logger.info("No collections found. Qdrant is already empty.")
            return

        logger.info(f"Found {len(collections)} collection(s): {', '.join(collections)}")

        if dry_run:
            logger.info("DRY RUN - No changes will be made.")
            for name in collections:
                logger.info(f"  Would delete: {name}")
            return

        # Confirm before deletion
        print(f"\n⚠️  WARNING: This will delete {len(collections)} collection(s)!")
        print("Collections to delete:")
        for name in collections:
            print(f"  - {name}")

        confirm = input("\nType 'yes' to confirm deletion: ")
        if confirm.lower() != "yes":
            logger.info("Operation cancelled.")
            return

        # Delete each collection
        for name in collections:
            logger.info(f"Deleting collection: {name}")
            await client.delete_collection(collection_name=name)
            logger.info(f"  ✓ Deleted: {name}")

        logger.info(f"✅ Successfully deleted {len(collections)} collection(s).")

    except Exception as e:
        logger.error(f"❌ Failed to reset Qdrant: {e}")
        logger.info("Tip: Make sure Qdrant is running and accessible.")
        sys.exit(1)


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv or "-n" in sys.argv

    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__)
        sys.exit(0)

    try:
        asyncio.run(reset_qdrant(dry_run=dry_run))
    except KeyboardInterrupt:
        logger.info("Operation cancelled by user.")
