"""Let the ``system`` tenant see and write every tenant-scoped row

Revision ID: 010_system_tenant_rls
Revises: 009_tool_invocations
Create Date: 2026-09-13 18:20:00.000000

Migration 008 gave every tenant-scoped table the policy
``USING (tenant_id = COALESCE(current_setting('app.tenant_id', true), 'default'))``,
symmetric in ``WITH CHECK``. Migration 009 repeated it for ``tool_invocations``.
Neither knew about the ``system`` tenant, which did not exist yet.

It does now. ``core.db.session_setup.system_tenant_scope`` binds
``app.tenant_id = 'system'`` for work that legitimately runs outside a request
and is **cross-tenant by construction** — it is looking at, or maintaining, rows
belonging to tenants it has not identified yet:

* ``core.orchestration.recovery`` — the boot-started crash-recovery sweep asks
  "whose runs were interrupted?" and then marks wedged ones failed;
* ``core.services.tenant.purge`` — GDPR erasure, which deletes *another*
  tenant's rows;
* ``core.bootstrap.lazy_init`` / ``core.db.schema`` — boot and schema work;
* the RQ worker and the CLI, for jobs and commands with no request behind them.

Without an exemption those callers bind an identity that matches nothing. On a
deployment that actually enforces RLS — a separate, ``NOSUPERUSER
NOBYPASSRLS`` runtime role, the configuration migration 008 documents — the
recovery sweep's discovery query returns zero rows for every real tenant, and
its write-back (``INSERT … ON CONFLICT`` re-sending ``tenant_id = 'acme'``)
violates ``WITH CHECK``. That turns "raises every interval" into the quieter and
worse "silently finds nothing", which is precisely the failure mode the scoping
work was closing.

So the requirement — *the deployment must grant the system tenant whatever its
maintenance work needs* — stops being a sentence in a code comment
(``core/db/session_setup.py``) that an operator had to read and implement, and
becomes part of the schema.

**Shape.** The predicate is widened, not replaced: a row matches when its
``tenant_id`` equals the session's, **or** when the session is the ``system``
tenant. Everything else is byte-identical to 008/009 — same policy name, same
permissive policy, same ``COALESCE(..., 'default')`` handling of an unset GUC,
still no ``FORCE ROW LEVEL SECURITY``.

The widening applies to the whole verb set, which matters because the two
clauses cover different ones. ``USING`` decides which *existing* rows a session
may see and therefore also which it may ``UPDATE`` or ``DELETE`` — a ``DELETE``
has no ``WITH CHECK`` at all, so a hidden row is not "refused", it simply is not
there and the statement reports zero. That is the shape of the worst failure
this fixes: ``core.services.tenant.purge`` issues
``DELETE … WHERE tenant_id = %s`` under the system identity, and against 008's
policy every one of those rows is invisible, so a GDPR erasure returned a
truthful ``0`` that read as success. ``WITH CHECK`` covers the ``INSERT`` /
``UPDATE`` side, which the stale-run sweep needs when it re-sends
``tenant_id = 'acme'`` through an ``INSERT … ON CONFLICT DO UPDATE``.

**Ordinary tenants are not widened.** The escape is a comparison on the
*session*, not on the row: for a session bound to ``acme`` the second disjunct
is ``'acme' = 'system'`` — constant false — so the predicate reduces to
``tenant_id = 'acme'`` exactly as before, in ``USING`` and in ``WITH CHECK``
alike. A tenant cannot read or write another tenant's rows through this, and
``WITH CHECK`` stays strict for everyone except the maintenance identity.

**Why this is not a hole.** ``app.tenant_id`` is set only by the pooled-checkout
hook in ``core.db.connection``, from the tenant contextvar, which is bound at
framework chokepoints (auth/tenant middleware, the worker's job metadata,
``system_tenant_scope``) and never from client input. A request cannot ask to be
``system``. A deployment that wants a harder boundary still has the two levers
008 describes: give the runtime role no access to the maintenance path, or run
maintenance under a separate role entirely.

``downgrade()`` restores migration 008/009's exact predicate, so the chain is
reversible; the code paths above then fail closed again (loudly under RLS),
which is the state this migration replaces.

This migration is, like 008, **inert in the default single-role deployment**:
Postgres does not apply RLS to a table's owner.
"""

from collections.abc import Sequence
from typing import Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "010_system_tenant_rls"
down_revision: Union[str, None] = "009_tool_invocations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: Every table protected by a ``tenant_isolation`` policy — the union of
#: migration 008's list and migration 009's. Compared against
#: ``core.db.ddl.RLS_PROTECTED_TABLES`` by ``tests/unit/test_schema_ownership.py``.
TENANT_SCOPED_TABLES: tuple[str, ...] = (
    "a2a_tasks",
    "agent_checkpoints",
    "agent_patterns",
    "chat_feedback",
    "feedback",
    "interactions",
    "tool_invocations",
)

POLICY_NAME = "tenant_isolation"

#: Must equal ``core.db.session_setup.SYSTEM_TENANT_ID``
#: (``tests/unit/test_system_tenant_rls_policy.py`` fails otherwise).
SYSTEM_TENANT_ID = "system"

_TENANT_EXPR = "COALESCE(current_setting('app.tenant_id', true), 'default')"

#: 008/009's predicate, widened with the system-tenant escape.
_PREDICATE = f"(tenant_id = {_TENANT_EXPR} OR {_TENANT_EXPR} = '{SYSTEM_TENANT_ID}')"

#: 008/009's predicate, verbatim — what ``downgrade()`` puts back.
_PREVIOUS_PREDICATE = f"(tenant_id = {_TENANT_EXPR})"


def _apply(predicate: str) -> None:
    """Recreate ``tenant_isolation`` on every protected table with *predicate*.

    ``ALTER TABLE … ENABLE ROW LEVEL SECURITY`` is re-issued because it is
    idempotent and because a table adopted since 008 (or one restored from a
    dump taken before it) may have arrived with RLS off; the policy without the
    enable is decoration.
    """
    for table in TENANT_SCOPED_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS {POLICY_NAME} ON {table}")
        op.execute(
            f"CREATE POLICY {POLICY_NAME} ON {table} "
            f"USING {predicate} "
            f"WITH CHECK {predicate}"
        )


def upgrade() -> None:
    _apply(_PREDICATE)


def downgrade() -> None:
    _apply(_PREVIOUS_PREDICATE)
