"""A failed startup warm-up must not leave a dead pool behind.

``AsyncConnectionPool.wait()`` *closes* the pool when ``min_size`` connections
do not arrive in time, and psycopg_pool cannot reopen a closed pool
(``PoolClosed``). The singleton therefore stayed closed for the life of the
process: with PostgreSQL down at boot, every later request failed even after
the database came back.
"""

from __future__ import annotations

import pytest

from core.db import connection as conn_mod


class _TimingOutPool:
    """Mimics psycopg_pool: a timed-out ``open(wait=True)`` closes the pool."""

    def __init__(self) -> None:
        self.closed = True

    async def open(self, wait: bool = False, timeout: float = 30.0) -> None:
        self.closed = False
        if wait:
            self.closed = True
            raise TimeoutError(f"pool initialization incomplete after {timeout} sec")


@pytest.fixture
def timing_out_pool(monkeypatch) -> _TimingOutPool:
    pool = _TimingOutPool()
    monkeypatch.setattr(conn_mod, "POSTGRES_ENABLED", True)
    monkeypatch.setattr(conn_mod, "_ASYNC_POOL", pool)
    monkeypatch.setattr(conn_mod, "_ASYNC_POOL_OPENED", False)
    return pool


async def test_failed_warmup_discards_the_closed_pool(timing_out_pool) -> None:
    assert await conn_mod.warm_async_pool(timeout=0.1) is False
    # The next use builds a fresh pool instead of hitting PoolClosed forever.
    assert conn_mod._ASYNC_POOL is None
    assert conn_mod._ASYNC_POOL_OPENED is False


async def test_successful_warmup_keeps_the_pool(monkeypatch) -> None:
    class _Healthy(_TimingOutPool):
        async def open(self, wait: bool = False, timeout: float = 30.0) -> None:
            self.closed = False

    pool = _Healthy()
    monkeypatch.setattr(conn_mod, "POSTGRES_ENABLED", True)
    monkeypatch.setattr(conn_mod, "_ASYNC_POOL", pool)
    monkeypatch.setattr(conn_mod, "_ASYNC_POOL_OPENED", False)
    assert await conn_mod.warm_async_pool(timeout=0.1) is True
    assert conn_mod._ASYNC_POOL is pool
