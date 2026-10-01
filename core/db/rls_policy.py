"""Row-level-security policies for tables that live outside the core migrations.

Core's tenant-scoped tables get their ``tenant_isolation`` policy from Alembic
(``008_row_level_security``, widened for the maintenance identity by
``010_system_tenant_rls_exemption``). A plugin that owns a table with a
``tenant_id`` column builds it from its own idempotent ``init_schema()`` instead,
which ``baselith plugin schema-init`` runs as the table owner. This module gives
that DDL the **same** policy, so a plugin table is isolated by the database
exactly as a core one is, and the predicate has one source rather than one copy
per plugin drifting from the migrations.

Two pieces:

* :func:`tenant_isolation_ddl` — the statements that enable RLS and (re)create
  the policy. Inert where RLS does not apply (the table owner, a superuser, a
  ``BYPASSRLS`` role — i.e. the default single-role install), and skipped with a
  ``NOTICE`` when the connected role does not own the table, so running it from
  a least-privilege activation where runtime DDL happens to be allowed never
  fails the boot.
* :func:`row_tenant_scope` — binds the tenant a plugin *writes its rows under*
  for a block of database work. The pool sets ``app.tenant_id`` from the tenant
  context var on checkout; a plugin whose rows are keyed by
  :func:`core.context.resolve_plugin_tenant_key` (a ``personal`` override keys
  them by user id) must make the session carry that same key, or every read
  would be filtered to nothing and every write refused by ``WITH CHECK``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from contextlib import contextmanager

from core.db.session_setup import SYSTEM_TENANT_ID

__all__ = [
    "TENANT_POLICY_NAME",
    "TENANT_POLICY_PREDICATE",
    "row_tenant_scope",
    "tenant_isolation_ddl",
]

#: Same name core's migrations use, so ``pg_policies`` reads uniformly.
TENANT_POLICY_NAME = "tenant_isolation"

_TENANT_EXPR = "COALESCE(current_setting('app.tenant_id', true), 'default')"

#: Byte-identical to ``010_system_tenant_rls_exemption._PREDICATE``: a row is
#: visible and writable when its ``tenant_id`` is the session's, or when the
#: session is the framework's maintenance identity. An unset GUC reads as
#: ``'default'``, exactly as the pool's fallback does with RLS off.
TENANT_POLICY_PREDICATE = (
    f"(tenant_id = {_TENANT_EXPR} OR {_TENANT_EXPR} = '{SYSTEM_TENANT_ID}')"
)

_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def _table_ddl(table: str) -> str:
    """One owner-guarded ``DO`` block enabling RLS and the policy on *table*."""
    return f"""
DO $rls$
BEGIN
    IF to_regclass('{table}') IS NULL THEN
        RAISE NOTICE 'rls: table {table} does not exist, policy skipped';
    ELSIF NOT pg_has_role(
        (SELECT relowner FROM pg_class WHERE oid = to_regclass('{table}')),
        'MEMBER'
    ) THEN
        RAISE NOTICE 'rls: % does not own {table}, policy left to the owner',
            current_user;
    ELSE
        ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS {TENANT_POLICY_NAME} ON {table};
        CREATE POLICY {TENANT_POLICY_NAME} ON {table}
            USING {TENANT_POLICY_PREDICATE}
            WITH CHECK {TENANT_POLICY_PREDICATE};
    END IF;
END
$rls$;
"""  # noqa: S608  # nosec B608 — DDL; ``table`` is validated by tenant_isolation_ddl


def tenant_isolation_ddl(tables: Iterable[str]) -> str:
    """Idempotent DDL giving each of *tables* core's ``tenant_isolation`` policy.

    Run it right after the ``CREATE TABLE IF NOT EXISTS`` statements, from the
    same ``init_schema()``. Every table needs a ``tenant_id`` column. ``FORCE
    ROW LEVEL SECURITY`` is not set, matching the migrations: the owner stays
    exempt, which is what keeps a single-role install unchanged.

    Args:
        tables: Unqualified, lower-case table names.

    Returns:
        One ``DO`` block per table, joined into a single script.

    Raises:
        ValueError: A name is not a plain lower-case identifier (the names are
            interpolated into DDL, so anything else is refused outright).
    """
    names = list(tables)
    for name in names:
        if not _IDENTIFIER.fullmatch(name):
            raise ValueError(f"not a plain table identifier: {name!r}")
    return "".join(_table_ddl(name) for name in names)


@contextmanager
def row_tenant_scope(tenant_key: str) -> Iterator[None]:
    """Make database work in this block run under *tenant_key*.

    A no-op when *tenant_key* already is the bound tenant (the ``shared`` case,
    and the ``system`` maintenance scope), and when no tenant is bound at all —
    the pool then refuses the checkout under RLS rather than this helper
    inventing an identity. Otherwise *tenant_key* is a per-user key derived from
    the authenticated principal, and it is bound through
    :func:`core.context.bind_principal_tenant`, so a principal whose id is a
    reserved tenant is refused instead of gaining the maintenance identity.

    Args:
        tenant_key: The key the rows are written under — the caller's
            :func:`core.context.resolve_plugin_tenant_key` result.

    Yields:
        None. Use it purely for its scope.

    Raises:
        core.context.ReservedTenantError: *tenant_key* is a reserved id that is
            not the currently bound tenant.
    """
    from core.context import (
        bind_principal_tenant,
        get_tenant_or_default,
        reset_tenant_context,
        tenant_is_bound,
    )

    if not tenant_is_bound() or tenant_key == get_tenant_or_default():
        yield
        return
    token = bind_principal_tenant(tenant_key)
    try:
        yield
    finally:
        reset_tenant_context(token)
