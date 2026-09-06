"""Row-level security policies for every tenant-scoped table

Revision ID: 008_row_level_security
Revises: 007_adopt_runtime_tables
Create Date: 2026-09-06 12:30:00.000000

Tenant isolation was enforced only in Python (``core.tenancy.guard`` plus a
``WHERE tenant_id = %s`` a developer had to remember). One forgotten predicate
is a cross-tenant read. ``core.db.connection`` already binds ``app.tenant_id``
on every pooled connection when ``DB_RLS_ENABLED=true``; what was missing is the
other half — the policies themselves. This migration adds them.

**This migration changes nothing on its own.** Postgres does not apply RLS to a
table's owner, and in the default single-role deployment the runtime role *is*
the owner, so every query behaves exactly as before. Isolation becomes real
when the deployment separates the roles:

    CREATE ROLE baselith_runtime LOGIN PASSWORD '...' NOSUPERUSER NOBYPASSRLS;
    GRANT USAGE ON SCHEMA public TO baselith_runtime;
    GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public
        TO baselith_runtime;
    ALTER DEFAULT PRIVILEGES IN SCHEMA public
        GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO baselith_runtime;
    -- migrations keep running as the owner; the app connects as the runtime role

``NOSUPERUSER`` is not decoration: a superuser bypasses row-level security
outright, and ``POSTGRES_USER`` in the compose stack is one — so a deployment
that keeps using it sees no isolation no matter what policies exist. ``USAGE``
on the schema is not implicit either: without it the role cannot resolve a
table name at all.

``FORCE ROW LEVEL SECURITY`` is deliberately *not* set: it would apply the
policy to the owner too, and a background job that runs without a bound tenant
(the pool falls back to ``'default'``) would silently stop seeing its rows. That
is a deployment decision, documented in the multi-tenancy guide, not something a
migration should impose.

The policy is permissive and symmetric: a row is visible, and may be written,
only when its ``tenant_id`` equals the session's ``app.tenant_id``. The GUC is
read with ``current_setting(..., true)`` so an unset value yields NULL rather
than an error, and ``COALESCE`` maps it to ``'default'`` — matching
``core.db.connection._current_tenant_for_session``.
"""

from collections.abc import Sequence
from typing import Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "008_row_level_security"
down_revision: Union[str, None] = "007_adopt_runtime_tables"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: Keep in step with ``core.db.ddl.RLS_PROTECTED_TABLES``
#: (``tests/unit/test_schema_ownership.py`` fails otherwise).
TENANT_SCOPED_TABLES: tuple[str, ...] = (
    "a2a_tasks",
    "agent_checkpoints",
    "agent_patterns",
    "chat_feedback",
    "feedback",
    "interactions",
)

POLICY_NAME = "tenant_isolation"
_TENANT_EXPR = "COALESCE(current_setting('app.tenant_id', true), 'default')"


def upgrade() -> None:
    for table in TENANT_SCOPED_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS {POLICY_NAME} ON {table}")
        op.execute(
            f"CREATE POLICY {POLICY_NAME} ON {table} "
            f"USING (tenant_id = {_TENANT_EXPR}) "
            f"WITH CHECK (tenant_id = {_TENANT_EXPR})"
        )


def downgrade() -> None:
    for table in TENANT_SCOPED_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {POLICY_NAME} ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
