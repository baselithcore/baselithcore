"""Create mcp_tasks (durable MCP task handles)

Revision ID: 015_mcp_tasks
Revises: 014_checkpoint_leases
Create Date: 2026-10-11 16:00:00.000000

Durable backing for
:class:`baselith.core.mcp.task_store_postgres.PostgresTaskBackend`
(``MCP_TASK_STORE_BACKEND=postgres``, or ``auto`` with Postgres enabled): one
row per ``io.modelcontextprotocol/tasks`` handle, written on every status
transition, so ``tasks/get`` after a restart — or on another replica — answers
with the stored status and result instead of ``Unknown task``.

``owner`` / ``lease_expires_at`` name the process driving a live task and how
long its heartbeat-renewed lease runs; a ``working`` or ``input_required`` row
whose lease lapsed has no driver left and is failed as orphaned by the next
reader. ``expires_at`` is the handle's wall-clock TTL, indexed for the
best-effort prune. Like 011 and 013, nothing creates this table at runtime,
and its row-level-security policy is born here with the system-tenant escape
that 010 added.
"""

from collections.abc import Sequence
from typing import Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "015_mcp_tasks"
down_revision: Union[str, None] = "014_checkpoint_leases"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: Tables this migration protects with a policy. Merged with the earlier
#: migrations' lists and compared against ``baselith.core.db.ddl.RLS_PROTECTED_TABLES``
#: by ``tests/unit/test_schema_ownership.py``.
TENANT_SCOPED_TABLES: tuple[str, ...] = ("mcp_tasks",)

POLICY_NAME = "tenant_isolation"
SYSTEM_TENANT_ID = "system"
_TENANT_EXPR = "COALESCE(current_setting('app.tenant_id', true), 'default')"
_PREDICATE = f"(tenant_id = {_TENANT_EXPR} OR {_TENANT_EXPR} = '{SYSTEM_TENANT_ID}')"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS mcp_tasks (
            task_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL DEFAULT 'default',
            status TEXT NOT NULL,
            data JSONB NOT NULL DEFAULT '{}'::jsonb,
            owner TEXT,
            lease_expires_at DOUBLE PRECISION,
            expires_at DOUBLE PRECISION NOT NULL,
            updated_at DOUBLE PRECISION NOT NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_mcp_tasks_tenant_expires "
        "ON mcp_tasks (tenant_id, expires_at)"
    )

    for table in TENANT_SCOPED_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS {POLICY_NAME} ON {table}")
        op.execute(
            f"CREATE POLICY {POLICY_NAME} ON {table} "
            f"USING {_PREDICATE} "
            f"WITH CHECK {_PREDICATE}"
        )


def downgrade() -> None:
    for table in TENANT_SCOPED_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {POLICY_NAME} ON {table}")
    op.execute("DROP INDEX IF EXISTS ix_mcp_tasks_tenant_expires")
    op.execute("DROP TABLE IF EXISTS mcp_tasks")
