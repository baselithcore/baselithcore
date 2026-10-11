"""With auto resume off, runs orphaned by a crash are failed, not left running."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from baselith.core.api import _recovery_startup
from baselith.core.orchestration.checkpoint import (
    STATUS_FAILED,
    STATUS_RUNNING,
    Checkpoint,
)


class _Store:
    def __init__(self, *checkpoints: Checkpoint) -> None:
        self.rows = {c.run_id: c for c in checkpoints}

    async def list_resumable(
        self, tenant_id: str | None = None, limit: int | None = None
    ) -> list[str]:
        return [r for r, c in self.rows.items() if c.status == STATUS_RUNNING]

    async def load(self, run_id: str) -> Checkpoint | None:
        return self.rows.get(run_id)

    async def save(self, checkpoint: Checkpoint) -> None:
        self.rows[checkpoint.run_id] = checkpoint


def _config(**overrides: Any) -> SimpleNamespace:
    values = {
        "checkpoint_resume_on_startup": False,
        "recovery_stale_sweep_enabled": True,
        "recovery_sweep_interval_seconds": 300.0,
        "recovery_stale_after_seconds": 1800.0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


async def test_stale_loop_fails_orphans_and_spares_live_runs() -> None:
    now = time.time()
    orphan = Checkpoint(run_id="orphan", updated_at=now - 7200, created_at=now - 7200)
    live = Checkpoint(run_id="live", updated_at=now - 5)
    store = _Store(orphan, live)

    await _recovery_startup.stale_sweep_loop(
        store, interval_seconds=1.0, stale_after_seconds=1800.0, max_cycles=1
    )

    assert store.rows["orphan"].status == STATUS_FAILED
    assert "stale" in (store.rows["orphan"].error or "")
    assert store.rows["live"].status == STATUS_RUNNING


async def test_stale_loop_survives_a_failing_store() -> None:
    class _Broken(_Store):
        async def list_resumable(self, *a: Any, **k: Any) -> list[str]:
            raise ConnectionError("db down")

    await _recovery_startup.stale_sweep_loop(
        _Broken(), interval_seconds=0.01, stale_after_seconds=1.0, max_cycles=2
    )


async def test_stale_loop_rejects_a_busy_loop() -> None:
    with pytest.raises(ValueError):
        await _recovery_startup.stale_sweep_loop(
            _Store(), interval_seconds=0, stale_after_seconds=1.0, max_cycles=1
        )


@pytest.mark.parametrize(
    ("overrides", "scheduled"),
    [({}, True), ({"recovery_stale_sweep_enabled": False}, False)],
)
async def test_resume_off_schedules_only_the_stale_sweep(
    overrides: dict[str, Any], scheduled: bool
) -> None:
    tasks: set[asyncio.Task[Any]] = set()
    started: list[Any] = []

    async def _fake_loop(store: Any, **kwargs: Any) -> None:
        started.append((store, kwargs))

    with (
        patch(
            "baselith.core.config.orchestration.get_orchestration_config",
            return_value=_config(**overrides),
        ),
        patch.object(_recovery_startup, "stale_sweep_loop", _fake_loop),
    ):
        store = _Store()
        _recovery_startup._schedule_recovery_sweep(store, tasks)
        if tasks:
            await asyncio.gather(*tasks)

    assert bool(started) is scheduled
    if scheduled:
        assert started[0][0] is store
        assert started[0][1]["stale_after_seconds"] == 1800.0


async def test_a_durable_store_resumes_by_default(tmp_path) -> None:
    """Unset resume flag + sqlite store: the full recovery loop, not fail-only."""
    from baselith.core.orchestration.checkpoint_sqlite import SQLiteCheckpointStore

    tasks: set[asyncio.Task[Any]] = set()
    stale_started: list[Any] = []

    async def _fake_stale(store: Any, **kwargs: Any) -> None:
        stale_started.append(store)

    store = SQLiteCheckpointStore(tmp_path / "c.db")
    try:
        with (
            patch(
                "baselith.core.config.orchestration.get_orchestration_config",
                return_value=_config(
                    checkpoint_resume_on_startup=None,
                    recovery_resume_after_seconds=300.0,
                    checkpoint_lease_seconds=60.0,
                ),
            ),
            patch.object(_recovery_startup, "stale_sweep_loop", _fake_stale),
        ):
            _recovery_startup._schedule_recovery_sweep(store, tasks)
        assert len(tasks) == 1
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        assert stale_started == []
    finally:
        store.close()
