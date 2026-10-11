"""Query cost tracking, the migration leader lock and the DDL policy switch."""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import psycopg
import pytest

from baselith.core.db import _tracking, ddl, migrate, schema
from baselith.core.db import connection as conn_mod
from baselith.core.middleware.cost_control import (
    BudgetExceededError,
    CostStats,
    _cost_context,
    cost_controller,
)


@pytest.fixture
def cost_scope():
    """Activate a request-scoped cost counter for the test, then drop it."""
    stats = CostStats()
    token = _cost_context.set(stats)
    try:
        yield stats
    finally:
        _cost_context.reset(token)


# --------------------------------------------------------------------------- #
# _track_db_query and the tracking cursors
# --------------------------------------------------------------------------- #


def test_untracked_context_never_touches_the_controller(monkeypatch) -> None:
    calls: list[Any] = []
    monkeypatch.setattr(
        _tracking, "cost_controller", SimpleNamespace(track_sql_query=calls.append)
    )
    token = _cost_context.set(None)
    try:
        _tracking._track_db_query("SELECT 1")
    finally:
        _cost_context.reset(token)
    assert calls == []


def test_tracked_query_is_counted(cost_scope) -> None:
    _tracking._track_db_query("SELECT 1")
    _tracking._track_db_query("SELECT 2")
    assert cost_scope.sql_queries == 2


def test_budget_errors_propagate(cost_scope, monkeypatch) -> None:
    monkeypatch.setattr(cost_controller, "sql_query_limit", 1)
    _tracking._track_db_query("SELECT 1")
    with pytest.raises(BudgetExceededError, match="SQL query limit"):
        _tracking._track_db_query("SELECT 2")


def test_tracking_failures_never_break_the_query(cost_scope, monkeypatch) -> None:
    def explode(_query: Any) -> None:
        raise ValueError("bookkeeping bug")

    monkeypatch.setattr(
        _tracking, "cost_controller", SimpleNamespace(track_sql_query=explode)
    )
    _tracking._track_db_query("SELECT 1")  # must not raise


def test_sync_tracking_cursor_counts_then_delegates(cost_scope, monkeypatch) -> None:
    delegated: list[tuple[str, Any]] = []

    def fake_execute(self, query, params=None, *, prepare=None, binary=None):
        delegated.append(("execute", (query, params, prepare, binary)))
        return self

    def fake_executemany(self, query, params_seq, *, returning=False):
        delegated.append(("executemany", (query, params_seq, returning)))

    monkeypatch.setattr(psycopg.Cursor, "execute", fake_execute)
    monkeypatch.setattr(psycopg.Cursor, "executemany", fake_executemany)
    cursor = object.__new__(_tracking.TrackingCursor)

    assert cursor.execute("SELECT %s", (1,), prepare=False, binary=True) is cursor
    cursor.executemany("INSERT %s", [(1,), (2,)], returning=True)

    assert delegated == [
        ("execute", ("SELECT %s", (1,), False, True)),
        ("executemany", ("INSERT %s", [(1,), (2,)], True)),
    ]
    assert cost_scope.sql_queries == 2


async def test_async_tracking_cursor_counts_then_delegates(
    cost_scope, monkeypatch
) -> None:
    delegated: list[tuple[str, Any]] = []

    async def fake_execute(self, query, params=None, *, prepare=None, binary=None):
        delegated.append(("execute", (query, params, prepare, binary)))
        return self

    async def fake_executemany(self, query, params_seq, *, returning=False):
        delegated.append(("executemany", (query, params_seq, returning)))

    monkeypatch.setattr(psycopg.AsyncCursor, "execute", fake_execute)
    monkeypatch.setattr(psycopg.AsyncCursor, "executemany", fake_executemany)
    cursor = object.__new__(_tracking.TrackingAsyncCursor)

    assert await cursor.execute("SELECT 1") is cursor
    await cursor.executemany("INSERT %s", [(1,)])

    assert delegated == [
        ("execute", ("SELECT 1", None, None, None)),
        ("executemany", ("INSERT %s", [(1,)], False)),
    ]
    assert cost_scope.sql_queries == 2


# --------------------------------------------------------------------------- #
# Session tenant memo (RLS session scope)
# --------------------------------------------------------------------------- #


class _MemoConn:
    def __init__(self) -> None:
        self.executed: list[tuple[str, Any]] = []

    @contextmanager
    def cursor(self):
        yield SimpleNamespace(execute=lambda q, p=None: self.executed.append((q, p)))


def test_sync_tenant_binding_is_memoized_per_connection(monkeypatch) -> None:
    monkeypatch.setattr(conn_mod, "_current_tenant_for_session", lambda: "t1")
    conn = _MemoConn()

    conn_mod._sync_apply_tenant(conn)
    conn_mod._sync_apply_tenant(conn)
    assert conn.executed == [("SELECT set_config('app.tenant_id', %s, false)", ("t1",))]

    monkeypatch.setattr(conn_mod, "_current_tenant_for_session", lambda: "t2")
    conn_mod._sync_apply_tenant(conn)
    assert conn.executed[-1][1] == ("t2",)


# --------------------------------------------------------------------------- #
# Migration leader lock / upgrade_head / init_db
# --------------------------------------------------------------------------- #


class _LockConn:
    def __init__(self, *, fail_on_lock: bool = False) -> None:
        self.statements: list[tuple[str, Any]] = []
        self.closed = False
        self.fail_on_lock = fail_on_lock

    def execute(self, query: str, params: Any = None) -> None:
        if self.fail_on_lock and "pg_advisory_lock" in query:
            raise psycopg.OperationalError("lock refused")
        self.statements.append((query, params))

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def lock_conn(monkeypatch):
    def _install(**kwargs: Any) -> tuple[_LockConn, list[tuple[Any, Any]]]:
        conn = _LockConn(**kwargs)
        connects: list[tuple[Any, Any]] = []

        def fake_connect(conninfo: Any, autocommit: bool = False) -> _LockConn:
            connects.append((conninfo, autocommit))
            return conn

        monkeypatch.setattr(psycopg, "connect", fake_connect)
        return conn, connects

    return _install


def test_leader_lock_wraps_the_body_and_always_unlocks(lock_conn) -> None:
    conn, connects = lock_conn()
    key = schema._MIGRATION_ADVISORY_LOCK_KEY

    with pytest.raises(RuntimeError, match="migration failed"):
        with schema._migration_leader_lock():
            assert conn.statements == [("SELECT pg_advisory_lock(%s)", (key,))]
            raise RuntimeError("migration failed")

    assert connects == [(schema._storage_config.conninfo, True)]
    assert conn.statements[-1] == ("SELECT pg_advisory_unlock(%s)", (key,))
    assert conn.closed is True


def test_leader_lock_degrades_to_unlocked_when_the_lock_fails(lock_conn) -> None:
    conn, _ = lock_conn(fail_on_lock=True)
    ran: list[bool] = []

    with schema._migration_leader_lock():
        ran.append(True)
        # The half-open connection is discarded before the body runs.
        assert conn.closed is True

    assert ran == [True]
    assert not any("unlock" in q for q, _ in conn.statements)


def test_upgrade_head_runs_alembic_inside_the_lock(lock_conn, monkeypatch) -> None:
    from alembic import command

    from baselith.core.db import migration_config

    conn, _ = lock_conn()
    sentinel_cfg = object()
    seen: list[Any] = []

    def fake_upgrade(cfg: Any, target: str) -> None:
        held = [q for q, _ in conn.statements]
        seen.append((cfg, target, held))

    monkeypatch.setattr(migration_config, "build_alembic_config", lambda: sentinel_cfg)
    monkeypatch.setattr(command, "upgrade", fake_upgrade)

    schema.upgrade_head()

    assert seen == [(sentinel_cfg, "head", ["SELECT pg_advisory_lock(%s)"])]
    assert conn.statements[-1][0] == "SELECT pg_advisory_unlock(%s)"


async def test_init_db_is_a_noop_when_postgres_is_disabled(monkeypatch) -> None:
    called: list[bool] = []

    async def fake_ensure() -> None:
        called.append(True)

    monkeypatch.setattr(schema, "POSTGRES_ENABLED", False)
    monkeypatch.setattr(schema, "ensure_schema", fake_ensure)

    await schema.init_db()

    assert called == []


async def test_ensure_schema_runs_upgrade_off_the_event_loop(monkeypatch) -> None:
    import threading

    threads: list[str] = []
    monkeypatch.setattr(
        schema, "upgrade_head", lambda: threads.append(threading.current_thread().name)
    )

    await schema.ensure_schema()

    assert len(threads) == 1
    assert threads[0] != threading.main_thread().name


def test_migrate_main_reports_success_and_missing_scripts(monkeypatch, capsys) -> None:
    from baselith.core.db.migration_config import MigrationsNotFoundError

    calls: list[bool] = []
    monkeypatch.setattr(schema, "upgrade_head", lambda: calls.append(True))
    assert migrate.main() == 0
    assert calls == [True]

    def missing() -> None:
        raise MigrationsNotFoundError("no scripts here")

    monkeypatch.setattr(schema, "upgrade_head", missing)
    assert migrate.main() == 1
    assert "no scripts here" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# Runtime DDL policy
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("configured", "production", "allowed"),
    [
        (True, True, True),
        (False, False, False),
        (None, True, False),
        (None, False, True),
    ],
)
def test_runtime_ddl_policy(monkeypatch, configured, production, allowed) -> None:
    import baselith.core.config as config
    from baselith.core.utils import runtime_env

    monkeypatch.setattr(
        config, "get_storage_config", lambda: SimpleNamespace(db_runtime_ddl=configured)
    )
    monkeypatch.setattr(runtime_env, "is_production_env", lambda: production)

    assert ddl.runtime_ddl_allowed() is allowed
    assert ddl.skip_runtime_ddl("store", "t1, t2") is (not allowed)
