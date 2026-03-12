"""Initial schema

Revision ID: 001_initial_schema
Revises: 
Create Date: 2026-03-12 23:25:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '001_initial_schema'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Set up basic schemas for tenants and chat feedback
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS tenants (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            status TEXT DEFAULT 'active',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    
    op.execute(
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

    op.execute("CREATE INDEX IF NOT EXISTS idx_chat_feedback_timestamp ON chat_feedback(timestamp DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_chat_feedback_query ON chat_feedback(query)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_chat_feedback_conversation_id ON chat_feedback(conversation_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_chat_feedback_tenant_id ON chat_feedback(tenant_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS chat_feedback")
    op.execute("DROP TABLE IF EXISTS tenants")
