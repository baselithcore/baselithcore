"""``system`` is a reserved tenant identifier, not just a convention.

Migration ``010_system_tenant_rls_exemption`` grants the ``system`` tenant
visibility of every tenant-scoped row, because the framework's maintenance work
(crash recovery, GDPR purge, boot, the worker, the CLI) is cross-tenant by
construction. That makes the string load-bearing in a way it was not before: any
path that could mint or accept a *principal* carrying it would hand that
principal the whole database.

Nothing in this repository can produce such a principal today — ``AuthUser``
takes its ``tenant_id`` from a verified credential, and the only provisioning
door (``TenantService.create_tenant``) now refuses the id. These tests pin the
defence in depth around that: one authority for the value, and the two places
that must check it.
"""

from __future__ import annotations

import pytest

from core.context import RESERVED_TENANT_IDS, is_reserved_tenant

pytestmark = [pytest.mark.unit]


def test_the_reserved_set_matches_the_identity_the_framework_binds():
    """``core.context`` cannot import ``core.db.session_setup`` (psycopg, app
    config), so the literal is duplicated. This is the seam that keeps the two
    copies honest."""
    from core.db.session_setup import SYSTEM_TENANT_ID

    assert SYSTEM_TENANT_ID in RESERVED_TENANT_IDS


def test_the_migration_exempts_exactly_the_reserved_identity():
    """The RLS escape and the reserved set are two halves of one decision."""
    import importlib.util
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[3]
        / "migrations"
        / "versions"
        / "010_system_tenant_rls_exemption.py"
    )
    spec = importlib.util.spec_from_file_location("mig_010_reserved", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    assert migration.SYSTEM_TENANT_ID in RESERVED_TENANT_IDS


@pytest.mark.parametrize(
    ("tenant_id", "expected"),
    [
        ("system", True),
        ("acme", False),
        ("default", False),
        ("", False),
        (None, False),
        # Equality, not a substring match: a real tenant may legitimately be
        # named after the word.
        ("system-integrators", False),
        ("subsystem", False),
        ("SYSTEM", False),
    ],
)
def test_is_reserved_tenant(tenant_id, expected):
    assert is_reserved_tenant(tenant_id) is expected


def test_the_case_sensitivity_is_deliberate():
    """``SYSTEM`` is not reserved because the binding is exact: the pool writes
    ``app.tenant_id`` verbatim and the policy compares it with ``=``, so only the
    exact string can ever match the exemption."""
    assert is_reserved_tenant("SYSTEM") is False


class TestBindPrincipalTenant:
    """The guarded binder: a reserved id is refused by the *call*, not by a
    check somebody remembered to write next to it.

    Round 2b reasoned that the two in-repo binding sites covered everything
    worth covering. True of this repository; false of a deployment. ``core/`` is
    shared with a sibling checkout whose own pure-ASGI context bridges
    authenticate a bearer themselves — including a WebSocket ``?token=`` — and
    bind the claim. Neither of this repo's guards reaches those files. A helper
    that refuses inside the binding is enforceable there; a convention is not.
    """

    def test_it_refuses_the_reserved_identity(self):
        from core.context import ReservedTenantError, bind_principal_tenant

        with pytest.raises(ReservedTenantError, match="reserved"):
            bind_principal_tenant("system")

    def test_nothing_is_bound_when_it_refuses(self):
        from core import context as core_context
        from core.context import ReservedTenantError, bind_principal_tenant

        before = core_context._tenant_context.get()
        with pytest.raises(ReservedTenantError):
            bind_principal_tenant("system")
        assert core_context._tenant_context.get() == before

    def test_an_ordinary_tenant_binds_and_restores(self):
        from core.context import (
            bind_principal_tenant,
            get_current_tenant_id,
            reset_tenant_context,
        )

        token = bind_principal_tenant("acme")
        try:
            assert get_current_tenant_id() == "acme"
        finally:
            reset_tenant_context(token)
        assert get_current_tenant_id() != "acme"

    def test_the_plain_setter_still_serves_the_framework(self):
        """``set_tenant_context`` stays unguarded on purpose: it binds values the
        framework already owns — the maintenance identity, a job's enqueued
        metadata, an event record, a checkpoint's stored tenant — and those may
        legitimately be ``system``. Guarding it would break replaying an event
        emitted inside a maintenance scope."""
        from core.context import get_current_tenant_id
        from core.db.connection import system_tenant_scope

        with system_tenant_scope():
            assert get_current_tenant_id() == "system"

    def test_it_is_exported_for_the_sibling_to_follow(self):
        """The point of the helper is that another checkout can import it."""
        from core import context as core_context

        assert callable(core_context.bind_principal_tenant)
