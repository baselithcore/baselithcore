"""
Database Schema Management.

Handles table creation, schema migrations, and index optimization for PostgreSQL.
"""

from __future__ import annotations

from typing import Final

from core.config import get_storage_config

_storage_config = get_storage_config()

POSTGRES_ENABLED = _storage_config.postgres_enabled

_EXTRA_COLUMNS: Final[tuple[tuple[str, str], ...]] = (
    ("conversation_id", "TEXT"),
    ("sources", "TEXT"),
    ("comment", "TEXT"),
)


async def ensure_schema() -> None:
    """
    Ensures that the database schema is up-to-date by running Alembic migrations.
    This runs synchronously in an executor because Alembic's core is synchronous.
    """
    import asyncio
    from alembic.config import Config
    from alembic import command
    import os

    def run_upgrade():
        # Get absolute path to alembic.ini
        alembic_ini_path = os.path.join(os.getcwd(), "alembic.ini")
        alembic_cfg = Config(alembic_ini_path)
        command.upgrade(alembic_cfg, "head")

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, run_upgrade)


async def init_db() -> None:
    """
    Initializes the database ensuring that the schema is updated via Alembic.
    """

    if not POSTGRES_ENABLED:
        return

    await ensure_schema()
