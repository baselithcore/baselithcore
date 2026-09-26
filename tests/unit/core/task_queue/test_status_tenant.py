"""Tenant ownership of TaskTracker records and the configured default queue."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from rq.job import Job

from core.config.task_queue import TaskQueueConfig
from core.task_queue.scheduler import ScheduledTask, TaskScheduler, enqueue_task
from core.task_queue.status import TaskStatus, TaskTracker

pytestmark = [pytest.mark.unit]


class _FakeRedis:
    """Just enough of a sync Redis client for TaskTracker (hash + pipeline)."""

    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, Any]] = {}

    def pipeline(self, transaction: bool = False) -> _FakeRedis:
        return self

    def __enter__(self) -> _FakeRedis:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def hset(self, key: str, mapping: dict[str, Any]) -> None:
        self.hashes.setdefault(key, {}).update(
            {k: str(v).encode() for k, v in mapping.items()}
        )

    def expire(self, key: str, ttl: int) -> None:
        return None

    def execute(self) -> None:
        return None

    def hgetall(self, key: str) -> dict[bytes, bytes]:
        return {k.encode(): v for k, v in self.hashes.get(key, {}).items()}


@pytest.fixture
def tracker() -> TaskTracker:
    return TaskTracker(conn=_FakeRedis())  # type: ignore[arg-type]


class TestTenantOwnership:
    def test_owner_reads_its_record(self, tracker: TaskTracker) -> None:
        tracker.set_status("j1", TaskStatus.QUEUED, tenant_id="acme")
        status = tracker.get_status_for_tenant("j1", "acme")
        assert status is not None
        assert status["tenant_id"] == "acme"

    def test_other_tenant_gets_none(self, tracker: TaskTracker) -> None:
        tracker.set_status("j1", TaskStatus.QUEUED, tenant_id="acme")
        assert tracker.get_status_for_tenant("j1", "globex") is None

    def test_later_updates_keep_the_owner(self, tracker: TaskTracker) -> None:
        tracker.set_status("j1", TaskStatus.QUEUED, tenant_id="acme")
        tracker.mark_started("j1")
        tracker.mark_completed("j1", result={"answer": "ok"})
        status = tracker.get_status_for_tenant("j1", "acme")
        assert status is not None
        assert status["status"] == TaskStatus.COMPLETED.value
        assert tracker.get_status_for_tenant("j1", "globex") is None

    def test_ownerless_record_fails_closed(self, tracker: TaskTracker) -> None:
        tracker.set_status("j1", TaskStatus.QUEUED)
        assert tracker.get_status("j1") is not None
        assert tracker.get_status_for_tenant("j1", "default") is None

    def test_unknown_id(self, tracker: TaskTracker) -> None:
        assert tracker.get_status_for_tenant("missing", "acme") is None


@pytest.fixture
def queue_mocks():
    queue = MagicMock()
    job = MagicMock(spec=Job)
    job.id = "job-1"
    queue.enqueue.return_value = job
    queue.enqueue_at.return_value = job
    config = TaskQueueConfig(
        redis_url="redis://localhost:6379/0", default_queue="priority"
    )
    with (
        patch("core.task_queue.scheduler.get_queue", return_value=queue) as get_q,
        patch("core.task_queue.scheduler.get_task_tracker") as get_tracker,
        patch("core.config.get_task_queue_config", return_value=config),
    ):
        yield get_q, get_tracker.return_value


def _task() -> None:
    return None


class TestSchedulerRecordsTenant:
    def test_enqueue_records_the_enqueuing_tenant(self, queue_mocks) -> None:
        _, tracker = queue_mocks
        with patch(
            "core.task_queue.scheduler.get_current_tenant_id", return_value="acme"
        ):
            enqueue_task(_task)
        assert tracker.set_status.call_args.kwargs["tenant_id"] == "acme"

    def test_enqueue_in_records_tenant_from_meta(self, queue_mocks) -> None:
        _, tracker = queue_mocks
        TaskScheduler().enqueue_in(_task, 30, meta={"tenant_id": "globex"})
        assert tracker.set_status.call_args.kwargs["tenant_id"] == "globex"


class TestConfiguredDefaultQueue:
    def test_enqueue_uses_configured_default_queue(self, queue_mocks) -> None:
        get_q, _ = queue_mocks
        TaskScheduler().enqueue(_task)
        get_q.assert_called_with("priority")

    def test_enqueue_task_uses_configured_default_queue(self, queue_mocks) -> None:
        get_q, _ = queue_mocks
        enqueue_task(_task)
        get_q.assert_called_with("priority")

    def test_enqueue_in_uses_configured_default_queue(self, queue_mocks) -> None:
        get_q, _ = queue_mocks
        TaskScheduler().enqueue_in(_task, 5)
        get_q.assert_called_with("priority")

    def test_explicit_queue_wins(self, queue_mocks) -> None:
        get_q, _ = queue_mocks
        TaskScheduler().enqueue(_task, queue_name="documents")
        get_q.assert_called_with("documents")

    def test_scheduled_task_defaults_to_configured_queue(self, queue_mocks) -> None:
        task = ScheduledTask(name="t", func=_task, interval_seconds=60)
        assert task.queue_name == "priority"
