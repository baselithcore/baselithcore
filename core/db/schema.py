"""
Database Schema Management.

Handles table creation, schema migrations, and index optimization for PostgreSQL.
"""

from __future__ import annotations

from typing import Final

from psycopg import AsyncCursor

from core.config import get_storage_config
from .connection import get_async_connection

_storage_config = get_storage_config()

POSTGRES_ENABLED = _storage_config.postgres_enabled

_EXTRA_COLUMNS: Final[tuple[tuple[str, str], ...]] = (
    ("conversation_id", "TEXT"),
    ("sources", "TEXT"),
    ("comment", "TEXT"),
)


async def ensure_schema(cursor: AsyncCursor[object]) -> None:
    """
    Ensures that the `feedback` table exists with all required columns.
    Also updates the schema by adding any missing columns and performance indexes.
    """

    await cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_feedback (
            id BIGSERIAL PRIMARY KEY,
            query TEXT NOT NULL,
            answer TEXT NOT NULL,
            feedback TEXT CHECK (feedback IN ('positive','negative')) NOT NULL,
            conversation_id TEXT,
            sources TEXT,
            comment TEXT,
            tenant_id TEXT DEFAULT 'default',
            timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )

    await cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS tenants (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            status TEXT DEFAULT 'active',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )

    # Ensure tenant_id exists for existing tables
    await cursor.execute(
        "ALTER TABLE chat_feedback ADD COLUMN IF NOT EXISTS tenant_id TEXT DEFAULT 'default'"
    )

    for column_name, definition in _EXTRA_COLUMNS:
        await cursor.execute(
            f"ALTER TABLE chat_feedback ADD COLUMN IF NOT EXISTS {column_name} {definition}"
        )

    # Performance indexes for common query patterns
    await cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_chat_feedback_timestamp ON chat_feedback(timestamp DESC)"
    )
    await cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_chat_feedback_query ON chat_feedback(query)"
    )
    await cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_chat_feedback_conversation_id ON chat_feedback(conversation_id)"
    )
    await cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_chat_feedback_tenant_id ON chat_feedback(tenant_id)"
    )


async def init_db() -> None:
    """
    Initializes the feedback database ensuring that the schema is updated.
    """

    if not POSTGRES_ENABLED:
        return

    async with get_async_connection() as conn:
        async with conn.cursor() as cursor:
            await ensure_schema(cursor)
        # conn.commit() usually handled by pool autocommit
