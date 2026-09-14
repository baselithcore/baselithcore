"""The startup DB probe must declare a tenant, not run unbound.

``run_startup_health_checks`` opens a pooled connection during lifespan, where
no request — and therefore no tenant — exists. With ``DB_RLS_ENABLED=true`` the
session binding now refuses to invent one, so the probe has to say what it is:
out-of-request system work. Without that it reports "PostgreSQL unreachable" on
a perfectly healthy database, at ERROR level in production.
"""

from contextlib import asynccontextmanager, contextmanager

import pytest

from core import context as core_context
from core.api import startup_checks
from core.context import TenantContextError, get_current_tenant_id
from core.db import connection as db_connection

UNBOUND = "<unbound>"


@contextmanager
def no_tenant():
    """Unbind the tenant contextvar (the autouse fixture binds ``default``)."""
    token = core_context._tenant_context.set(None)
    try:
        yield
    finally:
        core_context._tenant_context.reset(token)


class _FakeConn:
    async def execute(self, _sql: str) -> None:
        return None


@pytest.fixture
def probe(monkeypatch) -> list[str]:
    """Record the tenant visible inside the health check's DB block."""
    observed: list[str] = []

    monkeypatch.setattr(startup_checks, "POSTGRES_ENABLED", True)
    monkeypatch.setattr(startup_checks, "CACHE_REDIS_URL", "")
    monkeypatch.setattr(startup_checks, "is_production_env", lambda: False)

    async def _no_warm() -> None:
        return None

    monkeypatch.setattr(startup_checks, "warm_db_pool", _no_warm)

    @asynccontextmanager
    async def _fake_connection():
        try:
            observed.append(get_current_tenant_id())
        except TenantContextError:
            observed.append(UNBOUND)
        yield _FakeConn()

    monkeypatch.setattr(db_connection, "get_async_connection", _fake_connection)
    return observed


async def test_health_check_runs_under_the_system_tenant(probe, monkeypatch):
    monkeypatch.setattr(db_connection, "DB_RLS_ENABLED", True)

    with no_tenant():
        await startup_checks.run_startup_health_checks()

    assert probe == [db_connection.SYSTEM_TENANT_ID]


async def test_health_check_does_not_leak_the_system_tenant(probe, monkeypatch):
    """The scope is released, so nothing after startup inherits ``system``."""
    monkeypatch.setattr(db_connection, "DB_RLS_ENABLED", True)

    with no_tenant():
        await startup_checks.run_startup_health_checks()
        with pytest.raises(TenantContextError):
            get_current_tenant_id()


async def test_bound_request_tenant_is_untouched_by_the_probe(probe, monkeypatch):
    """A tenant bound by the caller is restored after the check."""
    monkeypatch.setattr(db_connection, "DB_RLS_ENABLED", False)

    from core.context import reset_tenant_context, set_tenant_context

    token = set_tenant_context("acme")
    try:
        await startup_checks.run_startup_health_checks()
        assert get_current_tenant_id() == "acme"
    finally:
        reset_tenant_context(token)

    assert probe == [db_connection.SYSTEM_TENANT_ID]
