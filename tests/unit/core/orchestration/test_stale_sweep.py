"""Tests for the stale-run sweep (wedged vs slow runs)."""

from __future__ import annotations

import time

import pytest

from core.orchestration.checkpoint import (
    STATUS_AWAITING_APPROVAL,
    STATUS_FAILED,
    STATUS_RUNNING,
    Checkpoint,
)
from core.orchestration.checkpoint_memory import InMemoryCheckpointStore
from core.orchestration.recovery import sweep_stale_runs

pytestmark = [pytest.mark.unit]


async def _seed(store, run_id: str, *, status: str, age_seconds: float) -> None:
    checkpoint = Checkpoint(run_id=run_id, status=status)
    await store.save(checkpoint)
    stored = store._store[run_id]
    stored["updated_at"] = time.time() - age_seconds


class TestSweepStaleRuns:
    async def test_stale_running_run_marked_failed(self):
        store = InMemoryCheckpointStore()
        await _seed(store, "old", status=STATUS_RUNNING, age_seconds=7200)
        await _seed(store, "fresh", status=STATUS_RUNNING, age_seconds=10)

        report = await sweep_stale_runs(store, max_age_seconds=3600)
        assert report.stale == ["old"]

        old = await store.load("old")
        assert old is not None and old.status == STATUS_FAILED
        assert "stale" in (old.error or "")

        fresh = await store.load("fresh")
        assert fresh is not None and fresh.status == STATUS_RUNNING

    async def test_awaiting_approval_never_swept(self):
        store = InMemoryCheckpointStore()
        await _seed(store, "wait", status=STATUS_AWAITING_APPROVAL, age_seconds=7200)
        report = await sweep_stale_runs(store, max_age_seconds=3600)
        assert report.stale == []
        loaded = await store.load("wait")
        assert loaded is not None and loaded.status == STATUS_AWAITING_APPROVAL

    async def test_loop_heartbeat_keeps_run_alive(self):
        store = InMemoryCheckpointStore()
        await _seed(store, "beating", status=STATUS_RUNNING, age_seconds=7200)
        stored = store._store["beating"]
        stored["plugin_data"] = {"loop_last_progress_at": time.time() - 30}

        report = await sweep_stale_runs(store, max_age_seconds=3600)
        assert report.stale == []
