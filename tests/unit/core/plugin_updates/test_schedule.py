"""The periodic check shares one GitHub round per interval across workers."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from baselith.core.plugin_updates import _schedule
from baselith.core.plugin_updates.cache import UpdateCache
from baselith.core.plugin_updates.models import CheckReport

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)


def _cache_with(tmp_path: Path, checked_at: datetime | None) -> UpdateCache:
    cache = UpdateCache(tmp_path)
    if checked_at is not None:
        cache.save(CheckReport(checked_at=checked_at, candidates=[]))
    return cache


def test_no_report_is_never_fresh(tmp_path: Path) -> None:
    assert not _schedule.fresh_enough(_cache_with(tmp_path, None), 900, NOW)


def test_a_sibling_report_within_half_an_interval_is_reused(tmp_path: Path) -> None:
    cache = _cache_with(tmp_path, NOW - timedelta(seconds=300))
    assert _schedule.fresh_enough(cache, 900, NOW)


def test_a_report_older_than_half_an_interval_is_stale(tmp_path: Path) -> None:
    cache = _cache_with(tmp_path, NOW - timedelta(seconds=451))
    assert not _schedule.fresh_enough(cache, 900, NOW)


def test_a_report_from_the_future_is_stale(tmp_path: Path) -> None:
    cache = _cache_with(tmp_path, NOW + timedelta(seconds=60))
    assert not _schedule.fresh_enough(cache, 900, NOW)


def test_first_run_delay_stays_within_the_spread() -> None:
    delays = [_schedule.first_run_delay(60) for _ in range(200)]
    assert all(60 <= d <= 60 * _schedule.FIRST_RUN_SPREAD for d in delays)
    assert _schedule.first_run_delay(0) == 0


async def test_workers_sharing_a_cache_make_one_check(tmp_path: Path) -> None:
    cache = UpdateCache(tmp_path)
    checks = 0
    reuses = 0

    async def check() -> None:
        nonlocal checks
        checks += 1
        cache.save(CheckReport(checked_at=datetime.now(UTC), candidates=[]))

    def reuse() -> None:
        nonlocal reuses
        reuses += 1

    async def worker(delay: float) -> None:
        await asyncio.sleep(delay)
        await _schedule.run_periodic(check, cache, 3600, first_delay=0, on_reuse=reuse)

    tasks = [asyncio.create_task(worker(i * 0.01)) for i in range(4)]
    await asyncio.sleep(0.1)
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

    assert checks == 1
    assert reuses == 3


async def test_a_failing_check_does_not_stop_the_loop(tmp_path: Path) -> None:
    calls = 0

    async def check() -> None:
        nonlocal calls
        calls += 1
        raise RuntimeError("github down")

    task = asyncio.create_task(
        _schedule.run_periodic(
            check, UpdateCache(tmp_path), 0.01, first_delay=0, on_reuse=lambda: None
        )
    )
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert calls >= 2
