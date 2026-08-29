"""Cross-replica leader election for the startup index bootstrap.

Every uvicorn worker runs the lifespan, and the bootstrapper's asyncio.Lock
is per-process — N workers booting together each started a full re-index.
``ensure_startup_bootstrap`` now races on the Redis DistributedLock: losers
skip (the winner's sentinel makes later boots a no-op anyway).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import core.services.bootstrap as bootstrap_module
from core.services.bootstrap import ensure_startup_bootstrap


class _FakeLock:
    def __init__(self, acquirable: bool = True, fail: bool = False):
        self._acquirable = acquirable
        self._fail = fail

    async def acquire(self, *, blocking=True, timeout=None, retry_interval=0.1):
        if self._fail:
            raise RuntimeError("redis down")
        return self._acquirable


async def test_losing_replica_skips_bootstrap(monkeypatch):
    schedule = AsyncMock()
    monkeypatch.setattr(bootstrap_module.bootstrapper, "schedule", schedule)
    monkeypatch.setattr(
        bootstrap_module, "_build_bootstrap_lock", lambda: _FakeLock(acquirable=False)
    )
    await ensure_startup_bootstrap()
    schedule.assert_not_awaited()


async def test_winning_replica_bootstraps(monkeypatch):
    schedule = AsyncMock()
    monkeypatch.setattr(bootstrap_module.bootstrapper, "schedule", schedule)
    monkeypatch.setattr(
        bootstrap_module, "_build_bootstrap_lock", lambda: _FakeLock(acquirable=True)
    )
    await ensure_startup_bootstrap()
    schedule.assert_awaited_once()


async def test_no_lock_backend_keeps_single_node_behavior(monkeypatch):
    schedule = AsyncMock()
    monkeypatch.setattr(bootstrap_module.bootstrapper, "schedule", schedule)
    monkeypatch.setattr(bootstrap_module, "_build_bootstrap_lock", lambda: None)
    await ensure_startup_bootstrap()
    schedule.assert_awaited_once()


async def test_lock_error_fails_open(monkeypatch):
    """Bootstrap matters more than exclusion: an unreachable lock backend must
    not leave every replica without indices."""
    schedule = AsyncMock()
    monkeypatch.setattr(bootstrap_module.bootstrapper, "schedule", schedule)
    monkeypatch.setattr(
        bootstrap_module, "_build_bootstrap_lock", lambda: _FakeLock(fail=True)
    )
    await ensure_startup_bootstrap()
    schedule.assert_awaited_once()
