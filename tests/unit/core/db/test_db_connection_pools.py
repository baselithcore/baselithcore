"""Pool lifecycle and routing in :mod:`baselith.core.db.connection`.

psycopg_pool is mocked globally in the unit run, so these tests drive the
module against small recording fakes: they pin *which* pool a checkout uses,
when it is opened, how an open failure is classified, what the session setup
emits on the connection, and that shutdown/stats never touch a pool that was
never built.
"""

from __future__ import annotations

from contextlib import asynccontextmanager, contextmanager
from typing import Any

import pytest

from baselith.core.db import connection as conn_mod

_TZ_SQL = "SELECT set_config('TimeZone', %s, false)"


class _Cursor:
    def __init__(self, log: list[tuple[Any, Any]]) -> None:
        self._log = log

    def execute(self, query: Any, params: Any = None) -> None:
        self._log.append((query, params))


class _SyncConn:
    def __init__(self) -> None:
        self.executed: list[tuple[Any, Any]] = []
        self.cursor_kwargs: list[dict[str, Any]] = []

    @contextmanager
    def cursor(self, **kwargs: Any):
        self.cursor_kwargs.append(kwargs)
        yield _Cursor(self.executed)


class _AsyncCursor(_Cursor):
    async def execute(self, query: Any, params: Any = None) -> None:  # type: ignore[override]
        self._log.append((query, params))


class _AsyncConn(_SyncConn):
    @asynccontextmanager
    async def cursor(self, **kwargs: Any):  # type: ignore[override]
        self.cursor_kwargs.append(kwargs)
        yield _AsyncCursor(self.executed)


class _SyncPool:
    def __init__(self, *, open_error: Exception | None = None, closed: bool = False):
        self.open_calls = 0
        self.close_calls = 0
        self.open_error = open_error
        self.closed = closed
        self.conn = _SyncConn()
        self.checkout_timeouts: list[Any] = []

    def open(self, **_kwargs: Any) -> None:
        self.open_calls += 1
        if self.open_error is not None:
            raise self.open_error

    def close(self) -> None:
        self.close_calls += 1

    @contextmanager
    def connection(self, timeout: Any = None):
        self.checkout_timeouts.append(timeout)
        yield self.conn

    def get_stats(self) -> dict[str, int]:
        return {"pool_size": 3, "pool_available": 2}


class _AsyncPool(_SyncPool):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.conn = _AsyncConn()

    async def open(self, **_kwargs: Any) -> None:  # type: ignore[override]
        super().open()

    async def close(self) -> None:  # type: ignore[override]
        self.close_calls += 1

    @asynccontextmanager
    async def connection(self, timeout: Any = None):  # type: ignore[override]
        self.checkout_timeouts.append(timeout)
        yield self.conn


@pytest.fixture(autouse=True)
def isolated_pools(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every pool singleton starts empty and RLS binding is off."""
    for name in ("_POOL", "_ASYNC_POOL", "_REPLICA_POOL", "_ASYNC_REPLICA_POOL"):
        monkeypatch.setattr(conn_mod, name, None)
    for name in (
        "_POOL_OPENED",
        "_ASYNC_POOL_OPENED",
        "_REPLICA_POOL_OPENED",
        "_ASYNC_REPLICA_POOL_OPENED",
    ):
        monkeypatch.setattr(conn_mod, name, False)
    monkeypatch.setattr(conn_mod, "POSTGRES_ENABLED", True)
    monkeypatch.setattr(conn_mod, "DB_RLS_ENABLED", False)
    monkeypatch.setattr(conn_mod, "DB_REPLICA_CONNINFO", None)
    monkeypatch.setattr(conn_mod, "DB_POOL_TIMEOUT", 7.5)


# --------------------------------------------------------------------------- #
# Primary sync pool
# --------------------------------------------------------------------------- #


def test_disabled_postgres_refuses_to_build_any_primary_pool(monkeypatch) -> None:
    monkeypatch.setattr(conn_mod, "POSTGRES_ENABLED", False)
    with pytest.raises(RuntimeError, match="POSTGRES_ENABLED=false"):
        conn_mod._get_pool()
    with pytest.raises(RuntimeError, match="POSTGRES_ENABLED=false"):
        conn_mod._get_async_pool()
    assert conn_mod._POOL is None and conn_mod._ASYNC_POOL is None


def test_get_connection_opens_once_and_applies_the_timezone(monkeypatch) -> None:
    pool = _SyncPool()
    monkeypatch.setattr(conn_mod, "_POOL", pool)

    with conn_mod.get_connection() as first:
        assert first is pool.conn
    with conn_mod.get_connection():
        pass

    assert pool.open_calls == 1
    assert conn_mod._POOL_OPENED is True
    assert pool.checkout_timeouts == [7.5, 7.5]
    # The session GUC is memoized on the physical connection: one round trip.
    assert pool.conn.executed == [(_TZ_SQL, (conn_mod.APP_TIMEZONE_NAME,))]


def test_open_failure_on_a_live_pool_is_tolerated(monkeypatch) -> None:
    """A concurrent open races us: the pool is open, the error is noise."""
    pool = _SyncPool(open_error=RuntimeError("already open"), closed=False)
    monkeypatch.setattr(conn_mod, "_POOL", pool)

    with conn_mod.get_connection() as conn:
        assert conn is pool.conn
    assert conn_mod._POOL_OPENED is True


def test_open_failure_on_a_closed_pool_propagates(monkeypatch) -> None:
    pool = _SyncPool(open_error=ConnectionError("db down"), closed=True)
    monkeypatch.setattr(conn_mod, "_POOL", pool)

    with pytest.raises(ConnectionError, match="db down"):
        with conn_mod.get_connection():
            pytest.fail("must not yield a connection from a closed pool")
    assert conn_mod._POOL_OPENED is False
    assert pool.checkout_timeouts == []


@pytest.mark.parametrize(
    ("row_factory", "expected_kwargs"),
    [(None, {}), ("dict_row", {"row_factory": "dict_row"})],
)
def test_get_cursor_only_forwards_a_row_factory_when_given(
    monkeypatch, row_factory, expected_kwargs
) -> None:
    pool = _SyncPool()
    monkeypatch.setattr(conn_mod, "_POOL", pool)

    with conn_mod.get_cursor(row_factory=row_factory) as cursor:
        cursor.execute("SELECT 1")

    # [0] is the timezone setup cursor; [1] is the caller's.
    assert pool.conn.cursor_kwargs[1] == expected_kwargs
    assert pool.conn.executed[-1] == ("SELECT 1", None)


# --------------------------------------------------------------------------- #
# Primary async pool
# --------------------------------------------------------------------------- #


async def test_async_connection_opens_once_and_applies_the_timezone(
    monkeypatch,
) -> None:
    pool = _AsyncPool()
    monkeypatch.setattr(conn_mod, "_ASYNC_POOL", pool)

    async with conn_mod.get_async_connection() as conn:
        assert conn is pool.conn
    async with conn_mod.get_async_connection():
        pass

    assert pool.open_calls == 1
    assert pool.conn.executed == [(_TZ_SQL, (conn_mod.APP_TIMEZONE_NAME,))]


async def test_async_open_failure_on_a_live_pool_is_tolerated(monkeypatch) -> None:
    pool = _AsyncPool(open_error=RuntimeError("racing open"), closed=False)
    monkeypatch.setattr(conn_mod, "_ASYNC_POOL", pool)

    async with conn_mod.get_async_connection() as conn:
        assert conn is pool.conn
    assert conn_mod._ASYNC_POOL_OPENED is True


async def test_async_open_failure_on_a_closed_pool_propagates(monkeypatch) -> None:
    pool = _AsyncPool(open_error=ConnectionError("db down"), closed=True)
    monkeypatch.setattr(conn_mod, "_ASYNC_POOL", pool)

    with pytest.raises(ConnectionError):
        async with conn_mod.get_async_connection():
            pytest.fail("must not yield")
    assert conn_mod._ASYNC_POOL_OPENED is False


@pytest.mark.parametrize(
    ("row_factory", "expected_kwargs"),
    [(None, {}), ("dict_row", {"row_factory": "dict_row"})],
)
async def test_async_cursor_only_forwards_a_row_factory_when_given(
    monkeypatch, row_factory, expected_kwargs
) -> None:
    pool = _AsyncPool()
    monkeypatch.setattr(conn_mod, "_ASYNC_POOL", pool)

    async with conn_mod.get_async_cursor(row_factory=row_factory) as cursor:
        await cursor.execute("SELECT 2")

    assert pool.conn.cursor_kwargs[1] == expected_kwargs
    assert pool.conn.executed[-1] == ("SELECT 2", None)


# --------------------------------------------------------------------------- #
# Read replica routing
# --------------------------------------------------------------------------- #


def test_replica_factories_refuse_without_a_replica_url() -> None:
    with pytest.raises(RuntimeError, match="DB_REPLICA_URL"):
        conn_mod._get_replica_pool()
    with pytest.raises(RuntimeError, match="DB_REPLICA_URL"):
        conn_mod._get_async_replica_pool()


def test_read_connection_falls_back_to_the_primary(monkeypatch) -> None:
    primary = _SyncPool()
    monkeypatch.setattr(conn_mod, "_POOL", primary)

    with conn_mod.get_read_connection() as conn:
        assert conn is primary.conn
    assert conn_mod._REPLICA_POOL is None


def test_read_connection_routes_to_the_replica_when_configured(monkeypatch) -> None:
    primary, replica = _SyncPool(), _SyncPool()
    monkeypatch.setattr(conn_mod, "DB_REPLICA_CONNINFO", "host=replica")
    monkeypatch.setattr(conn_mod, "_POOL", primary)
    monkeypatch.setattr(conn_mod, "_REPLICA_POOL", replica)

    with conn_mod.get_read_connection() as conn:
        assert conn is replica.conn
    with conn_mod.get_read_connection():
        pass

    assert replica.open_calls == 1
    assert primary.open_calls == 0
    assert replica.checkout_timeouts == [7.5, 7.5]
    assert replica.conn.executed == [(_TZ_SQL, (conn_mod.APP_TIMEZONE_NAME,))]


@pytest.mark.parametrize("closed", [False, True])
def test_replica_open_failure_follows_the_pool_state(monkeypatch, closed) -> None:
    replica = _SyncPool(open_error=OSError("replica down"), closed=closed)
    monkeypatch.setattr(conn_mod, "DB_REPLICA_CONNINFO", "host=replica")
    monkeypatch.setattr(conn_mod, "_REPLICA_POOL", replica)

    if closed:
        with pytest.raises(OSError):
            with conn_mod.get_read_connection():
                pass
    else:
        with conn_mod.get_read_connection() as conn:
            assert conn is replica.conn
    assert conn_mod._REPLICA_POOL_OPENED is (not closed)


async def test_async_read_connection_falls_back_to_the_primary(monkeypatch) -> None:
    primary = _AsyncPool()
    monkeypatch.setattr(conn_mod, "_ASYNC_POOL", primary)

    async with conn_mod.get_async_read_connection() as conn:
        assert conn is primary.conn
    assert conn_mod._ASYNC_REPLICA_POOL is None


async def test_async_read_connection_routes_to_the_replica(monkeypatch) -> None:
    replica = _AsyncPool()
    monkeypatch.setattr(conn_mod, "DB_REPLICA_CONNINFO", "host=replica")
    monkeypatch.setattr(conn_mod, "_ASYNC_REPLICA_POOL", replica)

    async with conn_mod.get_async_read_connection() as conn:
        assert conn is replica.conn
    async with conn_mod.get_async_read_connection():
        pass
    assert replica.open_calls == 1
    assert replica.conn.executed == [(_TZ_SQL, (conn_mod.APP_TIMEZONE_NAME,))]


@pytest.mark.parametrize("closed", [False, True])
async def test_async_replica_open_failure_follows_the_pool_state(
    monkeypatch, closed
) -> None:
    replica = _AsyncPool(open_error=OSError("replica down"), closed=closed)
    monkeypatch.setattr(conn_mod, "DB_REPLICA_CONNINFO", "host=replica")
    monkeypatch.setattr(conn_mod, "_ASYNC_REPLICA_POOL", replica)

    if closed:
        with pytest.raises(OSError):
            async with conn_mod.get_async_read_connection():
                pass
    else:
        async with conn_mod.get_async_read_connection() as conn:
            assert conn is replica.conn
    assert conn_mod._ASYNC_REPLICA_POOL_OPENED is (not closed)


# --------------------------------------------------------------------------- #
# Shutdown, warm-up shortcuts and stats
# --------------------------------------------------------------------------- #


def test_close_pool_closes_primary_and_replica(monkeypatch) -> None:
    primary, replica = _SyncPool(), _SyncPool()
    monkeypatch.setattr(conn_mod, "_POOL", primary)
    monkeypatch.setattr(conn_mod, "_REPLICA_POOL", replica)
    monkeypatch.setattr(conn_mod, "_POOL_OPENED", True)
    monkeypatch.setattr(conn_mod, "_REPLICA_POOL_OPENED", True)

    conn_mod.close_pool()

    assert (primary.close_calls, replica.close_calls) == (1, 1)
    assert conn_mod._POOL_OPENED is False
    assert conn_mod._REPLICA_POOL_OPENED is False


def test_close_pool_without_pools_is_a_noop() -> None:
    conn_mod.close_pool()
    assert conn_mod._POOL is None and conn_mod._REPLICA_POOL is None


async def test_close_async_pool_closes_primary_and_replica(monkeypatch) -> None:
    primary, replica = _AsyncPool(), _AsyncPool()
    monkeypatch.setattr(conn_mod, "_ASYNC_POOL", primary)
    monkeypatch.setattr(conn_mod, "_ASYNC_REPLICA_POOL", replica)
    monkeypatch.setattr(conn_mod, "_ASYNC_POOL_OPENED", True)
    monkeypatch.setattr(conn_mod, "_ASYNC_REPLICA_POOL_OPENED", True)

    await conn_mod.close_async_pool()

    assert (primary.close_calls, replica.close_calls) == (1, 1)
    assert conn_mod._ASYNC_POOL_OPENED is False
    assert conn_mod._ASYNC_REPLICA_POOL_OPENED is False


async def test_warmup_short_circuits(monkeypatch) -> None:
    monkeypatch.setattr(conn_mod, "POSTGRES_ENABLED", False)
    assert await conn_mod.warm_async_pool() is False
    assert conn_mod._ASYNC_POOL is None

    monkeypatch.setattr(conn_mod, "POSTGRES_ENABLED", True)
    monkeypatch.setattr(conn_mod, "_ASYNC_POOL_OPENED", True)
    assert await conn_mod.warm_async_pool() is True
    assert conn_mod._ASYNC_POOL is None  # an open pool is never rebuilt


def test_pool_stats_report_only_built_pools_and_skip_failures(monkeypatch) -> None:
    class _Broken(_SyncPool):
        def get_stats(self) -> dict[str, int]:
            raise RuntimeError("stats unavailable")

    monkeypatch.setattr(conn_mod, "_POOL", _SyncPool())
    monkeypatch.setattr(conn_mod, "_ASYNC_REPLICA_POOL", _Broken())

    stats = conn_mod.get_pool_stats()

    assert stats == {"primary": {"pool_size": 3, "pool_available": 2}}


def test_pool_stats_never_build_a_pool() -> None:
    assert conn_mod.get_pool_stats() == {}
    assert conn_mod._POOL is None and conn_mod._ASYNC_POOL is None
