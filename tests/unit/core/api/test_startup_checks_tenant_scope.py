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

    async def _no_warm() -> bool:
        return True

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

    assert probe and set(probe) == {db_connection.SYSTEM_TENANT_ID}


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

    assert probe and set(probe) == {db_connection.SYSTEM_TENANT_ID}


async def test_connection_budget_runs_under_the_system_tenant(probe, monkeypatch):
    """The budget read used to run outside the scope: with DB_RLS_ENABLED its
    checkout raised TenantContextError, swallowed — the check never ran."""
    from core.db import pool_budget

    monkeypatch.setattr(db_connection, "DB_RLS_ENABLED", True)
    seen: list[str] = []

    async def _budget(timeout: float = 5.0) -> None:
        try:
            seen.append(get_current_tenant_id())
        except TenantContextError:
            seen.append(UNBOUND)

    monkeypatch.setattr(pool_budget, "check_connection_budget", _budget)
    with no_tenant():
        await startup_checks.run_startup_health_checks()
    assert seen == [db_connection.SYSTEM_TENANT_ID]


async def test_unreachable_database_is_bounded_and_skips_the_budget(monkeypatch):
    """PostgreSQL down: the probe gives up after the startup bound (not a full
    DB_POOL_TIMEOUT), is reported, and the budget read is not attempted."""
    import asyncio
    from unittest.mock import MagicMock

    from core.db import pool_budget

    monkeypatch.setattr(startup_checks, "POSTGRES_ENABLED", True)
    monkeypatch.setattr(startup_checks, "CACHE_REDIS_URL", "")
    monkeypatch.setattr(startup_checks, "is_production_env", lambda: False)
    monkeypatch.setattr(startup_checks, "STARTUP_DB_PROBE_TIMEOUT_S", 0.2)

    async def _cold() -> bool:
        return False

    monkeypatch.setattr(startup_checks, "warm_db_pool", _cold)

    @asynccontextmanager
    async def _hanging():
        await asyncio.sleep(60)
        yield _FakeConn()

    monkeypatch.setattr(db_connection, "get_async_connection", _hanging)
    budget_calls: list[float] = []

    async def _budget(timeout: float = 5.0) -> None:
        budget_calls.append(timeout)

    monkeypatch.setattr(pool_budget, "check_connection_budget", _budget)

    async def _rls() -> None:
        await asyncio.sleep(60)

    import core.db.rls_posture as rls_posture

    monkeypatch.setattr(rls_posture, "enforce_rls_posture", _rls)
    mock_logger = MagicMock()
    monkeypatch.setattr(startup_checks, "logger", mock_logger)

    loop = asyncio.get_running_loop()
    started = loop.time()
    await startup_checks.run_startup_health_checks()
    assert loop.time() - started < 5
    assert budget_calls == []
    reported = [str(c.args[0]) for c in mock_logger.warning.call_args_list]
    assert any("PostgreSQL unreachable" in line for line in reported)
    assert any("posture check skipped" in line for line in reported)


async def test_health_check_reuses_a_failed_boot_probe(probe, monkeypatch):
    """With the boot probe already failed, no pool wait: one cheap re-probe."""
    from unittest.mock import AsyncMock

    from core.db import reachability

    warm = AsyncMock(return_value=True)
    reprobe = AsyncMock(return_value=False)
    monkeypatch.setattr(startup_checks, "warm_db_pool", warm)
    monkeypatch.setattr(reachability, "probe_postgres", reprobe)
    monkeypatch.setattr(reachability, "_last_outcome", False)

    await startup_checks.run_startup_health_checks()

    reprobe.assert_awaited_once()
    warm.assert_not_awaited()
    assert probe == []  # the pool was never asked for a connection
