"""Adopt the tables four stores used to create at runtime

Revision ID: 007_adopt_runtime_tables
Revises: 006_agent_patterns
Create Date: 2026-09-06 12:00:00.000000

``core.a2a.task_store_postgres``, ``core.orchestration.checkpoint_postgres``
and ``core.prompts.store_postgres`` created their own tables on the shared pool
at first use. That made the *runtime* role need DDL privileges in production and
left five tables with no migration history and no rollback.

``core.storage.postgres`` went further: ``interactions`` and ``feedback`` are
created by migration 002b, but their ``tenant_id`` **column** was added only by
that store's runtime ``ALTER TABLE`` — the gap migration 004 documents when it
explains why it cannot create the tenant covering index. This migration adopts
those columns and indexes too, so the tenant-scoped schema is complete before
the row-level-security policies in 008 reference it.

Every statement is ``IF NOT EXISTS``, so this migration is a no-op against a
deployment where those stores already ran — it exists so a **fresh** install
gets the schema from the migrations Job, and so ``DB_RUNTIME_DDL=false`` (the
production default from this release on) is safe.

The DDL is a verbatim snapshot of the constants in those modules;
``tests/unit/test_schema_ownership.py`` fails if a module ever creates a
Postgres table no migration owns.
"""

from collections.abc import Sequence
from typing import Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "007_adopt_runtime_tables"
down_revision: Union[str, None] = "006_agent_patterns"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- core.a2a.task_store_postgres ---------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS a2a_tasks (
            task_id     TEXT PRIMARY KEY,
            tenant_id   TEXT NOT NULL DEFAULT 'default',
            status      TEXT NOT NULL,
            data        JSONB NOT NULL,
            updated_at  DOUBLE PRECISION NOT NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_a2a_tasks_tenant ON a2a_tasks (tenant_id)"
    )

    # --- core.orchestration.checkpoint_postgres -----------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_checkpoints (
            run_id      TEXT PRIMARY KEY,
            tenant_id   TEXT NOT NULL DEFAULT 'default',
            status      TEXT NOT NULL DEFAULT 'running',
            data        JSONB NOT NULL,
            version     INTEGER NOT NULL DEFAULT 0,
            updated_at  TIMESTAMPTZ DEFAULT NOW()
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_agent_checkpoints_resumable
            ON agent_checkpoints (tenant_id, status)
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_checkpoint_history (
            run_id     TEXT NOT NULL,
            version    INTEGER NOT NULL,
            status     TEXT NOT NULL DEFAULT 'running',
            step       INTEGER NOT NULL DEFAULT 0,
            data       JSONB NOT NULL,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            PRIMARY KEY (run_id, version)
        )
        """
    )

    # --- core.storage.postgres: columns, not tables --------------------------
    # ``interactions`` and ``feedback`` are created by 002b, but their
    # ``tenant_id`` column was only ever added by the store's runtime
    # ``ALTER TABLE ... ADD COLUMN IF NOT EXISTS`` — the gap migration 004
    # documents when it explains why it could not create the covering index
    # here. With the runtime DDL now off in production, Alembic has to own the
    # column, and the row-level-security policies in 008 depend on it existing.
    # ADD COLUMN with a non-volatile DEFAULT does not rewrite the table.
    op.execute(
        "ALTER TABLE interactions "
        "ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT 'default'"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_interactions_tenant ON interactions (tenant_id)"
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_interactions_tenant_session_ts
            ON interactions (tenant_id, session_id, timestamp DESC)
        """
    )
    op.execute(
        "ALTER TABLE feedback "
        "ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT 'default'"
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_feedback_tenant ON feedback (tenant_id)")

    # --- core.prompts.store_postgres ----------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS prompt_versions (
            name        TEXT NOT NULL,
            version     TEXT NOT NULL,
            template    TEXT NOT NULL,
            description TEXT,
            variables   JSONB NOT NULL DEFAULT '[]',
            metadata    JSONB NOT NULL DEFAULT '{}',
            created_at  DOUBLE PRECISION NOT NULL,
            PRIMARY KEY (name, version)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS prompt_labels (
            name    TEXT NOT NULL,
            label   TEXT NOT NULL,
            version TEXT NOT NULL,
            PRIMARY KEY (name, label)
        )
        """
    )


def downgrade() -> None:
    # Data-bearing tables: dropping them on a downgrade would destroy in-flight
    # agent runs, A2A tasks and the prompt catalog. The tables predate this
    # migration on every existing deployment (the stores created them), so the
    # honest inverse of "start owning these" is "stop owning them", not "drop".
    pass
