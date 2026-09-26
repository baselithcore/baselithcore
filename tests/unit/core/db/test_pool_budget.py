"""Pool sizing vs ``max_connections``, and the PgBouncer prepared-statement switch."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.config.storage import StorageConfig
from core.db import connection as conn_mod
from core.db import pool_budget
from core.db.pool_budget import ConnectionBudget, check_connection_budget

pytestmark = [pytest.mark.unit]


def _patch_connection(monkeypatch, row):
    cursor = MagicMock()
    cursor.execute = AsyncMock()
    cursor.fetchone = AsyncMock(return_value=row)

    @asynccontextmanager
    async def _cursor_ctx():
        yield cursor

    conn = MagicMock()
    conn.cursor = _cursor_ctx

    @asynccontextmanager
    async def _conn_ctx():
        yield conn

    monkeypatch.setattr(conn_mod, "POSTGRES_ENABLED", True)
    monkeypatch.setattr(conn_mod, "get_async_connection", _conn_ctx)
    return cursor


class TestConnectionBudget:
    def test_demand_is_per_worker(self):
        budget = ConnectionBudget(
            pool_max_size=20, workers=8, max_connections=100, reserved=3
        )
        assert budget.demand == 160
        assert budget.available == 97
        assert budget.exceeded

    def test_fits(self):
        budget = ConnectionBudget(
            pool_max_size=10, workers=4, max_connections=100, reserved=3
        )
        assert not budget.exceeded

    async def test_warns_when_workers_times_pool_exceed_the_server(self, monkeypatch):
        _patch_connection(monkeypatch, (100, 3))
        monkeypatch.setattr(conn_mod, "DB_POOL_MAX_SIZE", 20)
        monkeypatch.setattr(pool_budget, "get_web_concurrency", lambda: 8)
        warn = MagicMock()
        monkeypatch.setattr(pool_budget.logger, "warning", warn)

        budget = await check_connection_budget()

        assert budget is not None and budget.exceeded
        warn.assert_called_once()
        assert warn.call_args.kwargs["extra"]["demand"] == 160
        assert "at most 12 per worker" in warn.call_args.kwargs["extra"]["hint"]

    async def test_quiet_when_it_fits(self, monkeypatch):
        _patch_connection(monkeypatch, (200, 3))
        monkeypatch.setattr(conn_mod, "DB_POOL_MAX_SIZE", 20)
        monkeypatch.setattr(pool_budget, "get_web_concurrency", lambda: 4)
        warn = MagicMock()
        monkeypatch.setattr(pool_budget.logger, "warning", warn)

        budget = await check_connection_budget()

        assert budget is not None and not budget.exceeded
        warn.assert_not_called()

    async def test_never_raises(self, monkeypatch):
        @asynccontextmanager
        async def _boom():
            raise OSError("db down")
            yield  # pragma: no cover

        monkeypatch.setattr(conn_mod, "POSTGRES_ENABLED", True)
        monkeypatch.setattr(conn_mod, "get_async_connection", _boom)
        assert await check_connection_budget() is None

    async def test_disabled_postgres_is_a_noop(self, monkeypatch):
        monkeypatch.setattr(conn_mod, "POSTGRES_ENABLED", False)
        assert await check_connection_budget() is None


class TestPreparedStatements:
    def test_default_keeps_psycopgs_threshold(self, monkeypatch):
        monkeypatch.delenv("DB_PREPARED_STATEMENTS", raising=False)
        assert StorageConfig().prepare_threshold == 5

    def test_pgbouncer_mode_disables_them(self, monkeypatch):
        monkeypatch.setenv("DB_PREPARED_STATEMENTS", "false")
        assert StorageConfig().prepare_threshold is None

    def test_every_pool_gets_the_threshold(self, monkeypatch):
        config = MagicMock()
        config.prepare_threshold = None
        config.session_options = "-c statement_timeout=1"
        monkeypatch.setattr(conn_mod, "_storage_config", config)
        kwargs = conn_mod._connection_kwargs(conn_mod.TrackingAsyncCursor)
        assert kwargs["prepare_threshold"] is None
        assert kwargs["autocommit"] is True
        assert kwargs["options"] == "-c statement_timeout=1"
