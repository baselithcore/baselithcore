from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from rq import Retry
from rq.job import Job, JobStatus

from core.task_queue.scheduler import (
    TaskScheduler,
    enqueue_task,
    get_task_scheduler,
    schedule_task,
)
from core.task_queue.status import TaskStatus
from core.config.task_queue import TaskQueueConfig


@pytest.fixture
def mock_queue():
    queue = MagicMock()
    job = MagicMock(spec=Job)
    job.id = "test-job-id"
    job.get_status.return_value = JobStatus.QUEUED
    queue.enqueue.return_value = job
    queue.enqueue_at.return_value = job
    return queue


@pytest.fixture
def mock_get_queue(mock_queue):
    with patch("core.task_queue.scheduler.get_queue", return_value=mock_queue) as mock:
        yield mock


@pytest.fixture
def mock_task_tracker():
    with patch("core.task_queue.scheduler.get_task_tracker") as mock:
        yield mock.return_value


@pytest.fixture
def mock_config():
    config = TaskQueueConfig(
        redis_url="redis://localhost:6379/0",
        default_retry_count=3,
        job_timeout=3600,
        result_ttl=500,
        failure_ttl=1000,
    )
    # Patch the function in core.config because it is imported inside the method
    with patch("core.config.get_task_queue_config", return_value=config):
        yield config


@pytest.fixture
def scheduler():
    return TaskScheduler()


def dummy_task(x, y):
    return x + y


class TestTaskScheduler:
    def test_singleton(self):
        s1 = get_task_scheduler()
        s2 = get_task_scheduler()
        assert s1 is s2

    def test_enqueue_defaults(
        self, scheduler, mock_get_queue, mock_task_tracker, mock_config
    ):
        job_id = scheduler.enqueue(dummy_task, 1, 2)

        assert job_id == "test-job-id"

        # Verify get_queue called with default
        mock_get_queue.assert_called_with("default")

        # Verify queue.enqueue called with correct config defaults
        mock_get_queue.return_value.enqueue.assert_called_once()
        call_args = mock_get_queue.return_value.enqueue.call_args
        assert call_args[0][0] == dummy_task
        assert call_args[0][1:] == (1, 2)
        assert call_args[1]["job_timeout"] == mock_config.job_timeout
        retry_arg = call_args[1]["retry"]
        assert isinstance(retry_arg, Retry)
        assert retry_arg.max == mock_config.default_retry_count

        # Verify tracker updated
        mock_task_tracker.set_status.assert_called_with(
            "test-job-id", TaskStatus.QUEUED, message="Queued in default"
        )

    def test_enqueue_custom_params(
        self, scheduler, mock_get_queue, mock_task_tracker, mock_config
    ):
        custom_timeout = 100
        custom_retry = 5

        scheduler.enqueue(
            dummy_task,
            queue_name="high_priority",
            job_timeout=custom_timeout,
            retry_count=custom_retry,
        )

        mock_get_queue.assert_called_with("high_priority")

        call_args = mock_get_queue.return_value.enqueue.call_args
        assert call_args[1]["job_timeout"] == custom_timeout
        retry_arg = call_args[1]["retry"]
        assert isinstance(retry_arg, Retry)
        assert retry_arg.max == custom_retry

    def test_enqueue_at(self, scheduler, mock_get_queue, mock_task_tracker):
        scheduled_time = datetime.now(timezone.utc) + timedelta(hours=1)

        job_id = scheduler.enqueue_at(
            dummy_task, scheduled_time, 1, 2, queue_name="scheduled"
        )

        assert job_id == "test-job-id"
        mock_get_queue.assert_called_with("scheduled")

        # Verify enqueue_at called
        mock_get_queue.return_value.enqueue_at.assert_called_with(
            scheduled_time, dummy_task, 1, 2
        )

        # Verify tracker
        mock_task_tracker.set_status.assert_called()
        assert mock_task_tracker.set_status.call_args[0][1] == TaskStatus.PENDING

    def test_enqueue_in(self, scheduler, mock_get_queue):
        with patch.object(scheduler, "enqueue_at") as mock_enqueue_at:
            mock_enqueue_at.return_value = "delegated-id"

            job_id = scheduler.enqueue_in(dummy_task, 60, 1, 2)

            assert job_id == "delegated-id"
            mock_enqueue_at.assert_called_once()
            call_args = mock_enqueue_at.call_args
            assert call_args[0][0] == dummy_task
            # Check time is roughly now + 60s
            assert isinstance(call_args[0][1], datetime)

    def test_cancel_job_success(self, scheduler, mock_task_tracker):
        # Patch core.task_queue.get_queue_redis_connection because it works via import inside method
        with patch("core.task_queue.get_queue_redis_connection"):
            with patch("rq.job.Job.fetch") as mock_fetch:
                mock_job = MagicMock()
                mock_fetch.return_value = mock_job

                result = scheduler.cancel_job("job-123")

                assert result is True
                mock_job.cancel.assert_called_once()
                mock_task_tracker.set_status.assert_called_with(
                    "job-123", TaskStatus.CANCELLED, message="Cancelled by user"
                )

    def test_cancel_job_failure(self, scheduler):
        with patch("core.task_queue.get_queue_redis_connection"):
            with patch("rq.job.Job.fetch", side_effect=Exception("Job not found")):
                result = scheduler.cancel_job("job-123")
                assert result is False

    def test_get_job_success(self, scheduler):
        with patch("core.task_queue.get_queue_redis_connection"):
            with patch("rq.job.Job.fetch") as mock_fetch:
                mock_job = MagicMock()
                mock_job.id = "job-123"
                mock_job.get_status.return_value = "started"
                mock_job.func_name = "func"
                mock_job.result = "test-result"

                # Mock datetimes
                now = datetime.now()
                mock_job.created_at = now
                mock_job.enqueued_at = now
                mock_job.started_at = now
                mock_job.ended_at = now

                mock_fetch.return_value = mock_job

                info = scheduler.get_job("job-123")

                assert info is not None
                assert info["id"] == "job-123"
                assert info["status"] == "started"
                assert info["result"] == "test-result"

    def test_get_job_failure(self, scheduler):
        with patch("core.task_queue.get_queue_redis_connection"):
            with patch("rq.job.Job.fetch", side_effect=Exception("doh")):
                info = scheduler.get_job("job-123")
                assert info is None

    def test_global_enqueue_task(self, mock_get_queue, mock_task_tracker, mock_config):
        with patch(
            "core.task_queue.scheduler.get_current_tenant_id", return_value="tenant-123"
        ):
            enqueue_task(dummy_task, 1, 2, queue="documents", meta={"foo": "bar"})

            mock_get_queue.assert_called_with("documents")
            call_args = mock_get_queue.return_value.enqueue.call_args

            # Verify tenant ID injected into meta
            meta = call_args[1]["meta"]
            assert meta["tenant_id"] == "tenant-123"
            assert meta["foo"] == "bar"

    def test_global_schedule_task(self, mock_get_queue, mock_task_tracker):
        with patch.object(TaskScheduler, "enqueue_in") as mock_enqueue_in:
            schedule_task(dummy_task, 60, 1, 2)
            mock_enqueue_in.assert_called_once()
