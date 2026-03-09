#!/usr/bin/env python3
"""
Reset PostgreSQL analytics database - truncates all tables.

Usage:
    python scripts/reset_analytics_db.py [--dry-run]

Options:
    --dry-run   Show what would be deleted without actually deleting

This resets tables:
    - sessions

WARNING: This will permanently delete ALL analytics data!
"""

import sys
import os
import asyncio
import logging
from core.observability.logging import get_logger
from urllib.parse import quote_plus

# Add project root to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from psycopg import AsyncConnection
from core.config import get_storage_config

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = get_logger("reset_db")


async def get_table_counts(cur, tables):
    """Get row counts for each table."""
    counts = {}
    for table in tables:
        await cur.execute("SELECT to_regclass(%s)", (table,))
        result = await cur.fetchone()
        if result and result[0]:
            await cur.execute(f"SELECT COUNT(*) FROM {table}")  # nosec
            row = await cur.fetchone()
            counts[table] = row[0] if row else 0
        else:
            counts[table] = None  # Table doesn't exist
    return counts


async def reset_analytics_db(dry_run: bool = False):
    """
    Resets the analytics database by truncating all tables.

    Args:
        dry_run: If True, only show what would be deleted
    """
    storage_config = get_storage_config()
    db_name = "agent_analytics"

    # Build connection string
    user = quote_plus(storage_config.db_user or "")
    password = (
        quote_plus(storage_config.db_password) if storage_config.db_password else ""
    )
    password_fragment = f":{password}" if password else ""
    host = storage_config.db_host or "localhost"
    port = storage_config.db_port or 5432

    # Connect directly to analytics DB
    conninfo = f"postgresql://{user}{password_fragment}@{host}:{port}/{db_name}"

    logger.info(f"Connecting to database '{db_name}' on {host}:{port}...")

    # Tables to clean (order: events first due to FK to sessions)
    tables = [
        "sessions",
    ]

    try:
        # NOTE: AsyncConnection.connect returns a coroutine that must be awaited
        connection = await AsyncConnection.connect(conninfo, autocommit=True)
        async with connection as conn:
            async with conn.cursor() as cur:
                # Get current counts
                counts = await get_table_counts(cur, tables)

                total_rows = sum(c for c in counts.values() if c is not None)
                existing_tables = [t for t, c in counts.items() if c is not None]

                if not existing_tables:
                    logger.info("No tables found. Database is empty.")
                    return

                logger.info("Current table contents:")
                for table in tables:
                    count = counts[table]
                    if count is not None:
                        logger.info(f"  - {table}: {count} rows")
                    else:
                        logger.info(f"  - {table}: (not found)")

                if total_rows == 0:
                    logger.info("All tables are already empty.")
                    return

                if dry_run:
                    logger.info("DRY RUN - No changes will be made.")
                    return

                # Confirm before deletion
                print(f"\n⚠️  WARNING: This will delete {total_rows} total rows!")
                confirm = input("Type 'yes' to confirm deletion: ")
                if confirm.lower() != "yes":
                    logger.info("Operation cancelled.")
                    return

                logger.info("Starting database cleanup...")

                for table in tables:
                    if counts[table] is not None:
                        logger.info(f"Truncating table: {table}")
                        await cur.execute(f"TRUNCATE TABLE {table} CASCADE")  # nosec
                        logger.info(f"  ✓ Truncated: {table}")

        logger.info("✅ Analytics database reset successfully.")

    except Exception as e:
        logger.error(f"❌ Failed to reset database: {e}")
        logger.info(
            "Tip: Make sure the database 'agent_analytics' exists and is accessible."
        )
        sys.exit(1)


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv or "-n" in sys.argv

    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__)
        sys.exit(0)

    try:
        asyncio.run(reset_analytics_db(dry_run=dry_run))
    except KeyboardInterrupt:
        logger.info("Operation cancelled by user.")
