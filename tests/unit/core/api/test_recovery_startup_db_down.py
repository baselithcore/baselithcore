"""Checkpoint store at boot with PostgreSQL down: degrade fast, recover later."""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from core.api import _recovery_startup
from core.orchestration.checkpoint_factory import CheckpointStoreUnavailableError


@pytest.fixture(autouse=True)
def _fast_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_recovery_startup, "_RETRY_INITIAL_DELAY_S", 0.01)
    monkeypatch.setattr(_recovery_startup, "_RETRY_MAX_DELAY_S", 0.02)


async def test_boot_does_not_wait_and_the_store_initializes_once_the_db_returns() -> (
    None
):
    store = object()
    init = AsyncMock(side_effect=[CheckpointStoreUnavailableError("down"), store])
    probe = AsyncMock(side_effect=[False, True])
    tasks: set[asyncio.Task[Any]] = set()

    with (
        patch(
            "core.orchestration.checkpoint_factory.initialize_default_checkpoint_store",
            init,
        ),
        patch("core.db.reachability.probe_postgres", probe),
        patch.object(_recovery_startup, "_schedule_recovery_sweep") as sweep,
    ):
        started = time.monotonic()
        await _recovery_startup.start_checkpoint_recovery(tasks)
        assert time.monotonic() - started < 0.5
        assert len(tasks) == 1  # the background retry, not a blocked boot

        await asyncio.wait_for(asyncio.gather(*tasks), timeout=2.0)

    assert init.await_count == 2
    assert probe.await_count == 2
    sweep.assert_called_once()
    assert sweep.call_args.args[0] is store


async def test_retry_task_is_cancellable_at_shutdown() -> None:
    init = AsyncMock(side_effect=CheckpointStoreUnavailableError("down"))
    probe = AsyncMock(return_value=False)
    tasks: set[asyncio.Task[Any]] = set()
    with (
        patch(
            "core.orchestration.checkpoint_factory.initialize_default_checkpoint_store",
            init,
        ),
        patch("core.db.reachability.probe_postgres", probe),
    ):
        await _recovery_startup.start_checkpoint_recovery(tasks)
        await asyncio.sleep(0.05)
        (task,) = tasks
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
