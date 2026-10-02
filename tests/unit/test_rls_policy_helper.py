"""``core.db.rls_policy``: plugin tables get exactly core's RLS policy.

Pinned without a database: the predicate is byte-identical to the one
migration 010 ships (so a plugin table and a core table isolate the same way,
system-tenant exemption included), the DDL refuses anything but a plain table
name, and :func:`row_tenant_scope` binds only a principal-derived key that
differs from the bound tenant. The live behaviour under a ``NOSUPERUSER
NOBYPASSRLS`` role is ``tests/integration/test_plugin_rls_policies.py``.
"""

from __future__ import annotations

import contextvars
import importlib.util
from pathlib import Path

import pytest

from core.context import (
    ReservedTenantError,
    get_current_tenant_id,
    reset_tenant_context,
    set_tenant_context,
    tenant_is_bound,
)
from core.db.rls_policy import (
    TENANT_POLICY_NAME,
    TENANT_POLICY_PREDICATE,
    row_tenant_scope,
    tenant_isolation_ddl,
)

pytestmark = [pytest.mark.unit]

_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "migrations"
    / "versions"
    / "010_system_tenant_rls_exemption.py"
)


def _migration_010():
    spec = importlib.util.spec_from_file_location("rls_helper_010", _MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_predicate_is_the_one_core_ships() -> None:
    migration = _migration_010()
    assert TENANT_POLICY_PREDICATE == migration._PREDICATE
    assert TENANT_POLICY_NAME == migration.POLICY_NAME


def test_ddl_enables_rls_and_both_clauses_per_table() -> None:
    ddl = tenant_isolation_ddl(("plugin_a", "plugin_b"))
    for table in ("plugin_a", "plugin_b"):
        assert f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY" in ddl
        assert f"DROP POLICY IF EXISTS tenant_isolation ON {table}" in ddl
        assert f"CREATE POLICY tenant_isolation ON {table}" in ddl
    assert ddl.count(f"USING {TENANT_POLICY_PREDICATE}") == 2
    assert ddl.count(f"WITH CHECK {TENANT_POLICY_PREDICATE}") == 2
    # The owner stays exempt, as with the migrations.
    assert "FORCE ROW LEVEL SECURITY" not in ddl
    # A role that does not own the table skips instead of failing activation.
    assert "pg_has_role" in ddl and "RAISE NOTICE" in ddl


@pytest.mark.parametrize(
    "name", ["", "Runs", "public.runs", "runs; DROP TABLE x", "a-b", "x" * 64]
)
def test_ddl_refuses_anything_but_a_plain_identifier(name: str) -> None:
    with pytest.raises(ValueError):
        tenant_isolation_ddl((name,))


def test_scope_is_a_no_op_with_no_tenant_bound() -> None:
    def body() -> None:
        assert not tenant_is_bound()
        with row_tenant_scope("u-1"):
            # Unbound stays unbound: the pool refuses the checkout under RLS
            # instead of this helper inventing an identity.
            assert not tenant_is_bound()

    # A fresh, empty context: the suite's fixtures bind a tenant globally.
    contextvars.Context().run(body)


def test_scope_is_a_no_op_for_the_bound_tenant() -> None:
    token = set_tenant_context("org-1")
    try:
        with row_tenant_scope("org-1"):
            assert get_current_tenant_id() == "org-1"
    finally:
        reset_tenant_context(token)


def test_scope_binds_a_differing_key_and_restores() -> None:
    from core.context import reset_user_context, set_user_context

    token = set_tenant_context("org-1")
    user = set_user_context("u-1")  # the per-user key under personal tenancy
    try:
        with row_tenant_scope("u-1"):
            assert get_current_tenant_id() == "u-1"
        assert get_current_tenant_id() == "org-1"
    finally:
        reset_user_context(user)
        reset_tenant_context(token)


def test_scope_refuses_to_bind_the_maintenance_identity() -> None:
    token = set_tenant_context("org-1")
    try:
        with pytest.raises(ReservedTenantError), row_tenant_scope("system"):
            pass
        assert get_current_tenant_id() == "org-1"
    finally:
        reset_tenant_context(token)


def test_system_scope_itself_passes_through() -> None:
    from core.db.connection import system_tenant_scope

    with system_tenant_scope(), row_tenant_scope("system"):
        assert get_current_tenant_id() == "system"


def test_scope_binds_only_a_key_derived_from_the_principal() -> None:
    """The per-user key must be the bound user's id, never a caller's string."""
    from core.context import reset_user_context, set_user_context
    from core.db.rls_policy import ForeignTenantKeyError

    token = set_tenant_context("org-1")
    user = set_user_context("u-1")
    try:
        with row_tenant_scope("u-1"):
            assert get_current_tenant_id() == "u-1"
        with pytest.raises(ForeignTenantKeyError), row_tenant_scope("u-2"):
            pass
        assert get_current_tenant_id() == "org-1"
    finally:
        reset_user_context(user)
        reset_tenant_context(token)
