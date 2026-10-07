"""Item 9: a bounded per-key client pool (tenant, user, server) with idle expiry."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from core.mcp.client_errors import MCPClientError
from core.mcp.keyed_pool import KeyedMCPClientPool, MCPPoolExhaustedError


class FakeClient:
    def __init__(self, fail: bool = False) -> None:
        self.connected = False
        self.closed = False
        self.fail = fail

    async def connect(self, **_: Any) -> None:
        await asyncio.sleep(0.01)
        if self.fail:
            raise RuntimeError("boom")
        self.connected = True

    async def disconnect(self) -> None:
        self.closed = True


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _pool(**kw: Any) -> KeyedMCPClientPool:
    return KeyedMCPClientPool(**kw)


async def test_same_key_reuses_one_connected_client() -> None:
    pool = _pool(max_clients=4, idle_seconds=60)
    made: list[FakeClient] = []

    def build() -> Any:
        made.append(FakeClient())
        return made[-1]

    async with pool.lease(("t1", "u1", "notion"), build) as a:
        assert a.connected
    async with pool.lease(("t1", "u1", "notion"), build) as b:
        assert b is a
    assert len(made) == 1 and len(pool) == 1


async def test_different_tenant_user_server_get_different_clients() -> None:
    pool = _pool(max_clients=8, idle_seconds=60)
    seen = set()
    for key in [
        ("t1", "u1", "s"),
        ("t2", "u1", "s"),
        ("t1", "u2", "s"),
        ("t1", "u1", "s2"),
    ]:
        async with pool.lease(key, FakeClient) as c:
            seen.add(id(c))
    assert len(seen) == 4


async def test_concurrent_leases_connect_once() -> None:
    pool = _pool(max_clients=4, idle_seconds=60)
    made: list[FakeClient] = []

    def build() -> Any:
        made.append(FakeClient())
        return made[-1]

    async def use() -> Any:
        async with pool.lease("k", build) as c:
            await asyncio.sleep(0.02)
            return c

    results = await asyncio.gather(*(use() for _ in range(5)))
    assert len(made) == 1 and all(r is made[0] for r in results)


async def test_lru_idle_entry_is_evicted_and_closed() -> None:
    pool = _pool(max_clients=2, idle_seconds=60)
    clients: dict[str, FakeClient] = {}
    for key in ("a", "b", "c"):
        async with pool.lease(key, lambda k=key: clients.setdefault(k, FakeClient())):
            pass
    assert clients["a"].closed is True
    assert not clients["b"].closed and not clients["c"].closed
    assert len(pool) == 2 and set(pool.keys()) == {"b", "c"}


async def test_in_flight_clients_are_never_evicted() -> None:
    pool = _pool(max_clients=1, idle_seconds=60)
    first = FakeClient()
    async with pool.lease("a", lambda: first):
        with pytest.raises(MCPPoolExhaustedError):
            async with pool.lease("b", FakeClient):
                pass
        assert first.closed is False
    assert isinstance(MCPPoolExhaustedError("x"), MCPClientError)


async def test_idle_expiry_closes_unused_clients() -> None:
    clock = Clock()
    pool = _pool(max_clients=4, idle_seconds=10, clock=clock)
    c = FakeClient()
    async with pool.lease("a", lambda: c):
        pass
    clock.now = 5
    assert await pool.reap() == 0 and not c.closed
    clock.now = 11
    assert await pool.reap() == 1 and c.closed and len(pool) == 0


async def test_expired_entry_is_replaced_on_next_lease() -> None:
    clock = Clock()
    pool = _pool(max_clients=4, idle_seconds=10, clock=clock)
    made: list[FakeClient] = []

    def build() -> Any:
        made.append(FakeClient())
        return made[-1]

    async with pool.lease("a", build):
        pass
    clock.now = 50
    async with pool.lease("a", build) as again:
        assert again is made[1]
    assert made[0].closed


async def test_connect_failure_is_not_cached() -> None:
    pool = _pool(max_clients=4, idle_seconds=60)
    bad = FakeClient(fail=True)
    with pytest.raises(RuntimeError):
        async with pool.lease("a", lambda: bad):
            pass
    assert len(pool) == 0
    async with pool.lease("a", FakeClient) as ok:
        assert ok.connected


async def test_explicit_close_and_close_all() -> None:
    pool = _pool(max_clients=4, idle_seconds=60)
    cs = {k: FakeClient() for k in "ab"}
    for k in "ab":
        async with pool.lease(k, lambda k=k: cs[k]):
            pass
    await pool.close("a")
    assert cs["a"].closed and not cs["b"].closed
    await pool.close("missing")  # no-op
    await pool.close_all()
    assert cs["b"].closed and len(pool) == 0


async def test_defaults_come_from_config(mcp_env: Any) -> None:
    mcp_env(MCP_POOL_MAX_CLIENTS="3", MCP_POOL_IDLE_SECONDS="7")
    pool = KeyedMCPClientPool()
    assert pool.max_clients == 3 and pool.idle_seconds == 7.0
