"""Tests for opt-in read-replica routing in core.db.connection."""

import pytest

from core.config.storage import StorageConfig
from core.db import connection as conn


class TestReplicaConfig:
    def test_replica_conninfo_none_by_default(self):
        cfg = StorageConfig(DB_REPLICA_URL=None)
        assert cfg.replica_conninfo is None

    def test_replica_conninfo_set(self):
        cfg = StorageConfig(DB_REPLICA_URL="postgresql://r/db")
        assert cfg.replica_conninfo == "postgresql://r/db"


class TestReadRoutingFallback:
    def test_sync_read_falls_back_to_primary(self, monkeypatch):
        # No replica configured -> get_read_connection delegates to get_connection.
        monkeypatch.setattr(conn, "DB_REPLICA_CONNINFO", None)
        sentinel = object()

        from contextlib import contextmanager

        @contextmanager
        def fake_primary():
            yield sentinel

        monkeypatch.setattr(conn, "get_connection", fake_primary)
        with conn.get_read_connection() as c:
            assert c is sentinel

    async def test_async_read_falls_back_to_primary(self, monkeypatch):
        monkeypatch.setattr(conn, "DB_REPLICA_CONNINFO", None)
        sentinel = object()

        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def fake_primary():
            yield sentinel

        monkeypatch.setattr(conn, "get_async_connection", fake_primary)
        async with conn.get_async_read_connection() as c:
            assert c is sentinel

    def test_replica_pool_requires_config(self, monkeypatch):
        monkeypatch.setattr(conn, "DB_REPLICA_CONNINFO", None)
        monkeypatch.setattr(conn, "_REPLICA_POOL", None)
        with pytest.raises(RuntimeError, match="No read replica configured"):
            conn._get_replica_pool()
