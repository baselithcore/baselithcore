"""Server-side session budgets are baked into every pool's startup options."""

from __future__ import annotations

from unittest.mock import MagicMock

from core.config.storage import StorageConfig
from core.db import connection


def test_storage_config_exposes_session_budgets():
    cfg = StorageConfig(
        DB_STATEMENT_TIMEOUT_MS=5_000, DB_IDLE_IN_TRANSACTION_TIMEOUT_MS=7_000
    )
    assert cfg.db_statement_timeout_ms == 5_000
    assert cfg.db_idle_in_transaction_timeout_ms == 7_000


def test_defaults_bound_both_budgets():
    cfg = StorageConfig()
    assert cfg.db_statement_timeout_ms == 30_000
    assert cfg.db_idle_in_transaction_timeout_ms == 60_000


def test_session_options_carry_both_guards():
    opts = StorageConfig(
        DB_STATEMENT_TIMEOUT_MS=5_000, DB_IDLE_IN_TRANSACTION_TIMEOUT_MS=7_000
    ).session_options
    assert "-c statement_timeout=5000" in opts
    assert "-c idle_in_transaction_session_timeout=7000" in opts


def _pool_factory(calls: list[dict], name: str):
    """A pool double that is complete enough for the constructor call.

    The pools are built with ``check=<PoolClass>.check_connection``, so a bare
    function is not a usable stand-in — it has no such attribute. That the
    check is wired at all is covered by ``test_pool_connection_check.py``;
    here it only has to exist so the constructor can be reached.
    """

    def _factory(**kwargs):
        calls.append(kwargs)
        return MagicMock()

    _factory.check_connection = f"{name}.check_connection"
    return _factory


def test_every_pool_uses_the_shared_options(monkeypatch):
    calls: list[dict] = []

    monkeypatch.setattr(
        connection, "ConnectionPool", _pool_factory(calls, "ConnectionPool")
    )
    monkeypatch.setattr(
        connection, "AsyncConnectionPool", _pool_factory(calls, "AsyncConnectionPool")
    )
    monkeypatch.setattr(connection, "POSTGRES_ENABLED", True)
    monkeypatch.setattr(connection, "DB_REPLICA_CONNINFO", "postgresql://replica")
    for name in ("_POOL", "_ASYNC_POOL", "_REPLICA_POOL", "_ASYNC_REPLICA_POOL"):
        monkeypatch.setattr(connection, name, None)

    connection._get_pool()
    connection._get_async_pool()
    connection._get_replica_pool()
    connection._get_async_replica_pool()

    assert len(calls) == 4
    expected = connection._storage_config.session_options
    assert all(c["kwargs"]["options"] == expected for c in calls)
