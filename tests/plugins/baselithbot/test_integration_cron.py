"""Integration tests for CronScheduler.

Marked ``@pytest.mark.slow`` because they drive the real asyncio event loop
with 1-second minimum intervals (the scheduler rejects sub-second intervals).
Run with ``pytest -m slow`` in nightly CI; the default PR pipeline skips
them via ``-m "not slow"``.
"""

from __future__ import annotations

import asyncio

import pytest

from plugins.baselithbot.cron import CronScheduler

pytestmark = pytest.mark.slow


class TestCronSchedulerLifecycle:
    async def test_registered_job_fires_on_trigger(self) -> None:
        scheduler = CronScheduler()
        hits: list[int] = []

        async def _job() -> None:
            hits.append(1)

        scheduler.add_interval("manual", _job, seconds=3600.0)
        await scheduler.start()
        try:
            scheduler.trigger("manual")
            await asyncio.sleep(1.5)
            assert hits == [1]
        finally:
            await scheduler.stop()

    async def test_set_enabled_pauses_job(self) -> None:
        scheduler = CronScheduler()
        hits: list[int] = []

        async def _job() -> None:
            hits.append(1)

        scheduler.add_interval("toggle", _job, seconds=3600.0)
        await scheduler.start()
        try:
            scheduler.set_enabled("toggle", False)
            scheduler.trigger("toggle")
            await asyncio.sleep(1.5)
            assert hits == [], "disabled job must not fire on trigger"

            scheduler.set_enabled("toggle", True)
            scheduler.trigger("toggle")
            await asyncio.sleep(1.5)
            assert hits == [1]
        finally:
            await scheduler.stop()

    async def test_job_error_is_captured_without_crashing_scheduler(self) -> None:
        scheduler = CronScheduler()
        counter = {"good": 0}

        async def _bad() -> None:
            raise RuntimeError("kaboom")

        async def _good() -> None:
            counter["good"] += 1

        scheduler.add_interval("bad", _bad, seconds=3600.0)
        scheduler.add_interval("good", _good, seconds=3600.0)
        await scheduler.start()
        try:
            scheduler.trigger("bad")
            scheduler.trigger("good")
            await asyncio.sleep(1.5)
        finally:
            await scheduler.stop()

        bad_job = scheduler.get("bad")
        assert bad_job is not None
        assert bad_job["last_error"] is not None
        assert "kaboom" in str(bad_job["last_error"])
        assert counter["good"] == 1, "healthy jobs must run when siblings fail"

    async def test_interval_below_one_second_rejected(self) -> None:
        scheduler = CronScheduler()

        async def _noop() -> None:
            return None

        with pytest.raises(ValueError, match=">= 1 second"):
            scheduler.add_interval("too-fast", _noop, seconds=0.5)
