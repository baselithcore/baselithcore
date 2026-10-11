"""MCP tasks a driver abandoned, or that never finish, end in ``failed``."""

from __future__ import annotations

import asyncio
import time

import pytest

from baselith.core.mcp.errors import InvalidParams
from baselith.core.mcp.tasks import (
    CANCELLED,
    COMPLETED,
    DEFAULT_MAX_WORKING_SECONDS,
    FAILED,
    INPUT_REQUIRED,
    WORKING,
    TaskHandlerMixin,
    TaskStore,
)


class _Host(TaskHandlerMixin):
    def __init__(self, store: TaskStore) -> None:
        self._tasks_store = store


def test_default_working_bound_is_one_hour() -> None:
    assert TaskStore().max_working_seconds == DEFAULT_MAX_WORKING_SECONDS == 3600.0


def test_working_task_without_a_driver_is_failed_on_read() -> None:
    store = TaskStore()
    task = store.create(ttl_ms=60_000, poll_interval_ms=10)

    seen = store.get(task.task_id)

    assert seen.status == FAILED
    assert "orphaned" in seen.to_wire()["error"]["message"]
    assert "orphaned" in seen.to_wire()["statusMessage"]


@pytest.mark.asyncio
async def test_driver_that_ended_without_status_is_failed() -> None:
    store = TaskStore()
    task = store.create(ttl_ms=60_000, poll_interval_ms=10)

    async def quiet() -> None:
        return None

    task._task = asyncio.create_task(quiet())
    await task._task

    assert store.get(task.task_id).status == FAILED


@pytest.mark.asyncio
async def test_task_past_the_working_bound_is_failed_and_cancelled() -> None:
    store = TaskStore(max_working_seconds=30)
    host = _Host(store)
    release = asyncio.Event()

    async def forever(_answers: dict) -> dict:
        await release.wait()
        return {"content": []}

    created = await host._run_as_task(forever, 60_000, 10)
    task = store.get(created["taskId"])
    assert task.status == WORKING
    task.working_since = time.monotonic() - 31

    wire = await host._handle_task_get({"taskId": task.task_id})
    await asyncio.sleep(0)  # let the cancellation unwind the driver

    assert wire["status"] == FAILED
    assert "working limit" in wire["error"]["message"]
    assert task._task is not None and task._task.cancelled()
    # The driver's cancellation must not overwrite the reason with "cancelled".
    assert store.get(task.task_id).status == FAILED


@pytest.mark.asyncio
async def test_bound_disabled_lets_a_long_task_run() -> None:
    store = TaskStore(max_working_seconds=None)
    host = _Host(store)
    release = asyncio.Event()

    async def slow(_answers: dict) -> dict:
        await release.wait()
        return {"content": []}

    created = await host._run_as_task(slow, 60_000, 10)
    task = store.get(created["taskId"])
    task.working_since = time.monotonic() - 10_000

    assert store.get(task.task_id).status == WORKING
    release.set()
    await task._task  # type: ignore[misc]
    assert store.get(task.task_id).status == COMPLETED


def test_input_required_task_is_never_swept() -> None:
    store = TaskStore(max_working_seconds=1)
    task = store.create(ttl_ms=60_000, poll_interval_ms=10)
    task.status = INPUT_REQUIRED
    task.working_since = time.monotonic() - 100

    assert store.get(task.task_id).status == INPUT_REQUIRED


@pytest.mark.asyncio
async def test_client_cancel_still_reports_cancelled() -> None:
    store = TaskStore()
    host = _Host(store)

    async def slow(_answers: dict) -> dict:
        await asyncio.Event().wait()
        return {}

    created = await host._run_as_task(slow, 60_000, 10)
    await host._handle_task_cancel({"taskId": created["taskId"]})
    await asyncio.sleep(0)

    assert store.get(created["taskId"]).status == CANCELLED


def test_unknown_handle_after_restart_is_a_terminal_error() -> None:
    # A fresh store is what a restarted process has: the handle is gone.
    with pytest.raises(InvalidParams, match="Unknown task"):
        TaskStore().get("lost-in-restart")
