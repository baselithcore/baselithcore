"""Tests for the background retention scheduler."""

from __future__ import annotations

import asyncio

import pytest

from core.privacy.scheduler import RetentionScheduler


class _FakeReport:
    total = 3


class TestRetentionScheduler:
    @pytest.mark.asyncio
    async def test_runs_sweep_with_horizon_then_stops(self, monkeypatch):
        calls: list[int] = []
        done = asyncio.Event()

        class _FakeService:
            async def purge_expired(self, seconds: int) -> _FakeReport:
                calls.append(seconds)
                done.set()
                return _FakeReport()

        monkeypatch.setattr(
            "core.privacy.scheduler.get_data_subject_service",
            lambda: _FakeService(),
        )

        sched = RetentionScheduler(retention_seconds=100, interval_seconds=3600)
        sched.start()
        await asyncio.wait_for(done.wait(), timeout=1.0)
        await sched.stop()

        assert calls and calls[0] == 100

    @pytest.mark.asyncio
    async def test_start_is_idempotent(self, monkeypatch):
        monkeypatch.setattr(
            "core.privacy.scheduler.get_data_subject_service",
            lambda: pytest.fail("should not be called before a tick"),
        )
        sched = RetentionScheduler(retention_seconds=100, interval_seconds=3600)
        sched.start()
        first = sched._task
        sched.start()  # no-op
        assert sched._task is first
        await sched.stop()

    @pytest.mark.asyncio
    async def test_stop_without_start_is_safe(self):
        await RetentionScheduler(retention_seconds=100).stop()

    @pytest.mark.asyncio
    async def test_sweep_failure_does_not_kill_loop(self, monkeypatch):
        attempts: list[int] = []
        ran_twice = asyncio.Event()

        class _FlakyService:
            async def purge_expired(self, seconds: int):
                attempts.append(seconds)
                if len(attempts) >= 2:
                    ran_twice.set()
                raise RuntimeError("db down")

        monkeypatch.setattr(
            "core.privacy.scheduler.get_data_subject_service",
            lambda: _FlakyService(),
        )
        # Tiny interval so the loop retries quickly despite the raised error.
        sched = RetentionScheduler(retention_seconds=100, interval_seconds=0)
        sched.start()
        await asyncio.wait_for(ran_twice.wait(), timeout=1.0)
        await sched.stop()
        assert len(attempts) >= 2
