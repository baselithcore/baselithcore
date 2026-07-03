"""Feedback performance indexes

Revision ID: 002_feedback_performance_indexes
Revises: 001_initial_schema
Create Date: 2026-03-30 21:00:00.000000

Adds composite indexes to chat_feedback to speed up the most common
analytics query patterns:
  - Filtering by tenant + feedback type (dashboard aggregations)
  - Sorting by tenant + timestamp (recent entries, time-series)
  - Filtering rows with sources (document stats)
"""

from typing import Sequence, Union

from alembic import op

revision: str = "002_feedback_performance_indexes"
down_revision: Union[str, None] = "001_initial_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Composite index for per-tenant feedback-type aggregations
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_chat_feedback_tenant_feedback "
        "ON chat_feedback(tenant_id, feedback)"
    )
    # Composite index for per-tenant time-range scans and ORDER BY timestamp
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_chat_feedback_tenant_timestamp "
        "ON chat_feedback(tenant_id, timestamp DESC)"
    )
    # Partial index: only rows that carry source references (document stats)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_chat_feedback_tenant_sources "
        "ON chat_feedback(tenant_id) WHERE sources IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_chat_feedback_tenant_sources")
    op.execute("DROP INDEX IF EXISTS idx_chat_feedback_tenant_timestamp")
    op.execute("DROP INDEX IF EXISTS idx_chat_feedback_tenant_feedback")
