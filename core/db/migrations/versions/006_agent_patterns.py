"""Create agent_patterns table (skill-evolution wiki layer)

Revision ID: 006_agent_patterns
Revises: 005_retention_timestamp_idx
Create Date: 2026-08-29 12:00:00.000000

Persistent pattern store for ``core/skill_evolution``: deduplicated units
of accumulated agent knowledge, keyed per tenant by failure fingerprint.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "006_agent_patterns"
down_revision: Union[str, None] = "005_retention_timestamp_idx"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_patterns (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL DEFAULT 'default',
            fingerprint TEXT NOT NULL,
            kind TEXT NOT NULL,
            title TEXT NOT NULL,
            summary TEXT NOT NULL,
            evidence JSONB NOT NULL DEFAULT '[]'::jsonb,
            occurrences INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'candidate',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_agent_patterns_tenant_fingerprint
                UNIQUE (tenant_id, fingerprint)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_agent_patterns_tenant_status_occ
            ON agent_patterns (tenant_id, status, occurrences DESC)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_agent_patterns_tenant_status_occ")
    op.execute("DROP TABLE IF EXISTS agent_patterns")
