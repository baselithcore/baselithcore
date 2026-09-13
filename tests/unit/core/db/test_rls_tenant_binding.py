"""RLS session binding must never invent a tenant.

``_current_tenant_for_session`` swallowed ``TenantContextError`` and bound
``"default"``. With row-level security switched on that is the worst possible
degradation: every RLS policy then matches the ``default`` tenant's rows, so a
background job with no tenant bound reads and writes another tenant's data
while the database believes isolation is enforced. Out-of-request callers now
have to say so, via :func:`core.db.connection.system_tenant_scope`.
"""

from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest

from core import context as core_context
from core.context import (
    TenantContextError,
    get_current_tenant_id,
    reset_tenant_context,
    set_tenant_context,
)
from core.db import connection as db_connection


@contextmanager
def no_tenant():
    """Unbind the tenant contextvar (the autouse fixture binds ``default``)."""
    token = core_context._tenant_context.set(None)
    try:
        yield
    finally:
        core_context._tenant_context.reset(token)


@pytest.fixture
def rls_on(monkeypatch):
    monkeypatch.setattr(db_connection, "DB_RLS_ENABLED", True)


@pytest.fixture
def rls_off(monkeypatch):
    monkeypatch.setattr(db_connection, "DB_RLS_ENABLED", False)


def test_unbound_tenant_raises_when_rls_is_enabled(rls_on):
    with no_tenant(), pytest.raises(TenantContextError) as excinfo:
        db_connection._current_tenant_for_session()

    message = str(excinfo.value)
    assert "system_tenant_scope" in message
    assert "RLS" in message or "row-level security" in message.lower()


def test_unbound_tenant_still_degrades_to_default_without_rls(rls_off):
    with no_tenant():
        assert db_connection._current_tenant_for_session() == "default"


def test_bound_tenant_is_used_verbatim(rls_on):
    token = set_tenant_context("acme")
    try:
        assert db_connection._current_tenant_for_session() == "acme"
    finally:
        reset_tenant_context(token)


def test_system_tenant_scope_binds_the_system_tenant(rls_on):
    with no_tenant(), db_connection.system_tenant_scope():
        assert db_connection._current_tenant_for_session() == "system"
        assert get_current_tenant_id() == "system"


def test_system_tenant_scope_restores_the_previous_tenant(rls_on):
    token = set_tenant_context("acme")
    try:
        with db_connection.system_tenant_scope():
            assert db_connection._current_tenant_for_session() == "system"
        assert db_connection._current_tenant_for_session() == "acme"
    finally:
        reset_tenant_context(token)


def test_system_tenant_scope_restores_on_error(rls_on):
    with no_tenant():
        with pytest.raises(RuntimeError), db_connection.system_tenant_scope():
            raise RuntimeError("boom")
        with pytest.raises(TenantContextError):
            db_connection._current_tenant_for_session()


def test_sync_apply_tenant_propagates_the_error(rls_on):
    connection = MagicMock()

    with no_tenant(), pytest.raises(TenantContextError):
        db_connection._sync_apply_tenant(connection)

    connection.cursor.assert_not_called()


async def test_async_apply_tenant_propagates_the_error(rls_on):
    connection = MagicMock()

    with no_tenant(), pytest.raises(TenantContextError):
        await db_connection._async_apply_tenant(connection)

    connection.cursor.assert_not_called()


def test_system_tenant_id_is_exported():
    assert db_connection.SYSTEM_TENANT_ID == "system"
    assert "system_tenant_scope" in db_connection.__all__


async def test_schema_init_runs_under_the_system_tenant(monkeypatch):
    """Migrations are the canonical out-of-request DB work."""
    from core.db import schema

    observed: list[str] = []

    async def _fake_ensure_schema() -> None:
        observed.append(get_current_tenant_id())

    monkeypatch.setattr(schema, "POSTGRES_ENABLED", True)
    monkeypatch.setattr(schema._storage_config, "db_migrations_on_startup", True)
    monkeypatch.setattr(schema, "ensure_schema", _fake_ensure_schema)

    with no_tenant():
        await schema.init_db()

    assert observed == [db_connection.SYSTEM_TENANT_ID]


async def test_postgres_bootstrap_runs_under_the_system_tenant(monkeypatch):
    """``initialize_postgres`` builds the storage layer before any request."""
    from core import storage as storage_module
    from core.bootstrap import lazy_init

    observed: list[str] = []

    async def _fake_init_db() -> None:
        observed.append(get_current_tenant_id())

    async def _fake_get_storage() -> object:
        observed.append(get_current_tenant_id())
        return object()

    monkeypatch.setattr(storage_module, "init_db", _fake_init_db)
    monkeypatch.setattr(storage_module, "get_storage", _fake_get_storage)

    with no_tenant():
        await lazy_init.initialize_postgres()

    assert observed == [db_connection.SYSTEM_TENANT_ID] * 2


async def test_vectorstore_bootstrap_runs_under_the_system_tenant(monkeypatch):
    """The pgvector backend creates its extension/table/indexes through the
    *shared Postgres pool*, so collection setup is DDL on the same connection
    the fail-closed reader guards — not a remote call to another service.

    Unscoped, the ``TenantContextError`` was rewrapped as ``VectorStoreError``
    by ``VectorStoreService.create_collection`` and sailed past the
    ``except ImportError`` in ``core.api.lifespan``, so a deployment running
    ``DB_RLS_ENABLED=true`` with ``VECTORSTORE_PROVIDER=pgvector`` did not boot.
    """
    from core.bootstrap import lazy_init

    observed: list[str] = []

    class _Service:
        async def create_collection(self, *args, **kwargs) -> None:
            observed.append(get_current_tenant_id())

    monkeypatch.setattr(
        "core.services.vectorstore.get_vectorstore_service", lambda: _Service()
    )

    with no_tenant():
        await lazy_init.initialize_vectorstore()

    assert observed == [db_connection.SYSTEM_TENANT_ID]


async def test_vectorstore_bootstrap_restores_the_previous_tenant(monkeypatch):
    from core.bootstrap import lazy_init

    class _Service:
        async def create_collection(self, *args, **kwargs) -> None:
            return None

    monkeypatch.setattr(
        "core.services.vectorstore.get_vectorstore_service", lambda: _Service()
    )

    with no_tenant():
        await lazy_init.initialize_vectorstore()
        assert core_context._tenant_context.get() is None


class TestTenantIsBound:
    """``core.context.tenant_is_bound`` is the public form of the question.

    The RLS check used to reach into ``core.context._tenant_context`` because
    ``get_current_tenant_id`` conflates "unbound" with the ``"default"``
    fallback unless ``strict_tenant_isolation`` happens to be on — an unrelated
    switch that RLS must not depend on.
    """

    def test_false_when_nothing_is_bound(self):
        from core.context import tenant_is_bound

        with no_tenant():
            assert tenant_is_bound() is False

    def test_true_when_a_tenant_is_bound(self):
        from core.context import tenant_is_bound

        token = set_tenant_context("acme")
        try:
            assert tenant_is_bound() is True
        finally:
            reset_tenant_context(token)

    def test_true_inside_the_system_scope(self):
        from core.context import tenant_is_bound

        with no_tenant(), db_connection.system_tenant_scope():
            assert tenant_is_bound() is True

    def test_the_rls_check_no_longer_reads_the_private_contextvar(self):
        import inspect

        source = inspect.getsource(db_connection._current_tenant_for_session)
        assert "_tenant_context" not in source
        assert "tenant_is_bound()" in source
