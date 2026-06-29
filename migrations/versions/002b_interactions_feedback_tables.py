"""Create interactions and feedback tables

Revision ID: 002b_interactions_feedback_tables
Revises: 002_feedback_performance_indexes
Create Date: 2026-06-18 20:15:00.000000

The ``interactions`` and ``feedback`` tables were historically created at
runtime by ``core/storage/postgres.py`` (``_initialize_schema``), which runs
*after* Alembic migrations during startup (lifespan -> Postgres init ->
``ensure_schema`` -> ``alembic upgrade`` -> get_storage -> _initialize_schema).
Migration ``003`` indexes these two tables, so on a fresh database it failed
with ``UndefinedTable`` — the tables did not exist yet at migration time.

This migration brings the two tables under Alembic management so they exist
before ``003`` runs. The DDL mirrors ``_initialize_schema`` exactly; the runtime
``CREATE TABLE IF NOT EXISTS`` stays in place as an idempotent backup, so this
is safe on databases where the app already created the tables.
"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "002b_interactions_feedback"
down_revision: Union[str, None] = "002_feedback_performance_indexes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS interactions (
            id UUID PRIMARY KEY,
            session_id TEXT,
            user_id TEXT,
            agent_id TEXT,
            input_transcription TEXT,
            output_transcription TEXT,
            metadata JSONB DEFAULT '{}'::jsonb,
            timestamp TIMESTAMPTZ DEFAULT NOW()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS feedback (
            id UUID PRIMARY KEY,
            interaction_id UUID REFERENCES interactions(id),
            score FLOAT,
            label TEXT,
            comment TEXT,
            metadata JSONB DEFAULT '{}'::jsonb,
            timestamp TIMESTAMPTZ DEFAULT NOW()
        )
        """
    )


def downgrade() -> None:
    # Drop ``feedback`` first: it carries a FK to ``interactions``.
    op.execute("DROP TABLE IF EXISTS feedback")
    op.execute("DROP TABLE IF EXISTS interactions")
