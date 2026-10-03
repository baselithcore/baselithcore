"""Transaction-scoped RLS tenant binding (``DB_RLS_TENANT_SCOPE=transaction``).

Behind a transaction-mode pooler (PgBouncer ``pool_mode=transaction``) a
session GUC does not follow the checkout: the next statement may run on a
backend still carrying another tenant's ``app.tenant_id``. The transaction
scope makes every checkout one transaction and binds the tenant with
``set_config(..., true)`` inside it, so the GUC lives exactly as long as the
transaction the pooler pins to one backend. These tests pin that wiring on all
four checkout helpers with psycopg replaced by recording doubles.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from typing import Any

import pytest

from core.db import connection as db_connection

TENANT = "tenant-a"


class _Cursor:
    def __init__(self, log: list[tuple[str, Any]]) -> None:
        self._log = log

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    async def __aenter__(self) -> _Cursor:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> Any:
        self._log.append(("execute", (sql, params)))
        return _Awaitable()


class _Awaitable:
    def __await__(self) -> Iterator[None]:
        return iter(())


class _Conn:
    """Records the order of transaction boundaries and statements."""

    def __init__(self) -> None:
        self.log: list[tuple[str, Any]] = []

    def cursor(self) -> _Cursor:
        return _Cursor(self.log)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        self.log.append(("begin", None))
        try:
            yield
        except BaseException:
            self.log.append(("rollback", None))
            raise
        self.log.append(("commit", None))


class _AsyncConn(_Conn):
    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:  # type: ignore[override]
        self.log.append(("begin", None))
        try:
            yield
        except BaseException:
            self.log.append(("rollback", None))
            raise
        self.log.append(("commit", None))


class _Pool:
    def __init__(self, conn: _Conn) -> None:
        self._conn = conn
        self.closed = False

    @contextmanager
    def connection(self, timeout: float | None = None) -> Iterator[_Conn]:
        yield self._conn


class _AsyncPool(_Pool):
    @asynccontextmanager
    async def connection(  # type: ignore[override]
        self, timeout: float | None = None
    ) -> AsyncIterator[_Conn]:
        yield self._conn


async def _noop_async(_conn: object) -> None:
    return None


@pytest.fixture
def scope(monkeypatch: pytest.MonkeyPatch) -> Any:
    def _set(value: str, *, replica: bool = False) -> None:
        monkeypatch.setattr(db_connection, "DB_RLS_ENABLED", True)
        monkeypatch.setattr(db_connection, "DB_RLS_TENANT_SCOPE", value)
        monkeypatch.setattr(
            db_connection, "_current_tenant_for_session", lambda: TENANT
        )
        monkeypatch.setattr(db_connection, "_sync_apply_timezone", lambda _c: None)
        monkeypatch.setattr(db_connection, "_async_apply_timezone", _noop_async)
        monkeypatch.setattr(db_connection, "_POOL_OPENED", True)
        monkeypatch.setattr(db_connection, "_ASYNC_POOL_OPENED", True)
        monkeypatch.setattr(db_connection, "_REPLICA_POOL_OPENED", True)
        monkeypatch.setattr(db_connection, "_ASYNC_REPLICA_POOL_OPENED", True)
        monkeypatch.setattr(
            db_connection, "DB_REPLICA_CONNINFO", "host=replica" if replica else ""
        )

    return _set


def _install(
    monkeypatch: pytest.MonkeyPatch, *, is_async: bool, replica: bool
) -> _Conn:
    conn: _Conn = _AsyncConn() if is_async else _Conn()
    pool: _Pool = _AsyncPool(conn) if is_async else _Pool(conn)
    name = {
        (False, False): "_get_pool",
        (True, False): "_get_async_pool",
        (False, True): "_get_replica_pool",
        (True, True): "_get_async_replica_pool",
    }[(is_async, replica)]
    monkeypatch.setattr(db_connection, name, lambda: pool)
    return conn


def _expect_transaction_binding(conn: _Conn) -> None:
    assert conn.log[0] == ("begin", None)
    kind, (sql, params) = conn.log[1]
    assert kind == "execute"
    assert "set_config('app.tenant_id', %s, true)" in sql
    assert params == (TENANT,)
    assert conn.log[-1] == ("commit", None)
    assert not hasattr(conn, "_app_tenant_id"), "transaction scope must not memoize"


@pytest.mark.parametrize("replica", [False, True])
def test_sync_checkout_binds_inside_a_transaction(
    monkeypatch: pytest.MonkeyPatch, scope: Any, replica: bool
) -> None:
    scope("transaction", replica=replica)
    conn = _install(monkeypatch, is_async=False, replica=replica)
    helper = (
        db_connection.get_read_connection if replica else db_connection.get_connection
    )
    with helper() as got:
        assert got is conn
        conn.log.append(("work", None))
    _expect_transaction_binding(conn)
    assert ("work", None) in conn.log[2:-1]


@pytest.mark.parametrize("replica", [False, True])
async def test_async_checkout_binds_inside_a_transaction(
    monkeypatch: pytest.MonkeyPatch, scope: Any, replica: bool
) -> None:
    scope("transaction", replica=replica)
    conn = _install(monkeypatch, is_async=True, replica=replica)
    helper = (
        db_connection.get_async_read_connection
        if replica
        else db_connection.get_async_connection
    )
    async with helper() as got:
        assert got is conn
        conn.log.append(("work", None))
    _expect_transaction_binding(conn)


def test_an_error_rolls_the_checkout_back(
    monkeypatch: pytest.MonkeyPatch, scope: Any
) -> None:
    scope("transaction")
    conn = _install(monkeypatch, is_async=False, replica=False)
    with pytest.raises(RuntimeError), db_connection.get_connection():
        raise RuntimeError("boom")
    assert conn.log[-1] == ("rollback", None)


async def test_an_async_error_rolls_the_checkout_back(
    monkeypatch: pytest.MonkeyPatch, scope: Any
) -> None:
    scope("transaction")
    conn = _install(monkeypatch, is_async=True, replica=False)
    with pytest.raises(RuntimeError):
        async with db_connection.get_async_connection():
            raise RuntimeError("boom")
    assert conn.log[-1] == ("rollback", None)


def test_transaction_scope_ignores_a_stale_session_memo(
    monkeypatch: pytest.MonkeyPatch, scope: Any
) -> None:
    """A memo from session mode must never short-circuit the binding."""
    scope("transaction")
    conn = _install(monkeypatch, is_async=False, replica=False)
    conn._app_tenant_id = TENANT  # type: ignore[attr-defined]
    with db_connection.get_connection():
        pass
    assert any(kind == "execute" for kind, _ in conn.log)


def test_session_scope_is_unchanged(
    monkeypatch: pytest.MonkeyPatch, scope: Any
) -> None:
    scope("session")
    conn = _install(monkeypatch, is_async=False, replica=False)
    with db_connection.get_connection():
        pass
    assert ("begin", None) not in conn.log
    kind, (sql, params) = conn.log[0]
    assert "set_config('app.tenant_id', %s, false)" in sql
    assert params == (TENANT,)
    assert conn._app_tenant_id == TENANT  # type: ignore[attr-defined]


def test_rls_off_binds_nothing(monkeypatch: pytest.MonkeyPatch, scope: Any) -> None:
    scope("transaction")
    monkeypatch.setattr(db_connection, "DB_RLS_ENABLED", False)
    conn = _install(monkeypatch, is_async=False, replica=False)
    with db_connection.get_connection():
        pass
    assert conn.log == []
