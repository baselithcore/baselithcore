"""Retry backoff, explicit job ids, and trace propagation on enqueue.

``Retry(max=n)`` with no interval retries *immediately*, so a job failing on a
dependency that is down burns its whole retry budget inside a second and is
dead-lettered before the dependency has a chance to come back.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
from rq.job import Job, JobStatus

from core.config.task_queue import TaskQueueConfig
from core.task_queue.scheduler import (
    TaskScheduler,
    ambient_job_meta,
    retry_intervals,
)

pytestmark = [pytest.mark.unit]


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


@pytest.fixture(autouse=True)
def mock_task_tracker():
    with patch("core.task_queue.scheduler.get_task_tracker") as mock:
        yield mock.return_value


@pytest.fixture
def mock_config():
    config = TaskQueueConfig(
        redis_url="redis://localhost:6379/0",
        default_retry_count=3,
        default_retry_delay=60,
        job_timeout=3600,
    )
    with patch("core.config.get_task_queue_config", return_value=config):
        yield config


def _noop(*_args, **_kwargs):
    return None


class TestRetryIntervals:
    def test_exponential_schedule(self):
        assert retry_intervals(3, 60) == [60, 120, 240]

    def test_one_entry_per_retry(self):
        assert len(retry_intervals(5, 10)) == 5

    def test_capped(self):
        assert retry_intervals(6, 600, cap=3600) == [600, 1200, 2400, 3600, 3600, 3600]

    def test_zero_retries_is_empty(self):
        assert retry_intervals(0, 60) == []

    def test_zero_delay_stays_zero(self):
        """A deliberate no-delay policy is honoured, not silently changed."""
        assert retry_intervals(3, 0) == [0, 0, 0]


class TestEnqueueRetry:
    def test_retry_carries_exponential_intervals(
        self, mock_get_queue, mock_queue, mock_config
    ):
        TaskScheduler().enqueue(_noop)
        retry = mock_queue.enqueue.call_args.kwargs["retry"]
        assert retry.max == 3
        assert retry.intervals == [60, 120, 240]

    def test_explicit_retry_delay_is_the_base(
        self, mock_get_queue, mock_queue, mock_config
    ):
        TaskScheduler().enqueue(_noop, retry_count=2, retry_delay=5)
        retry = mock_queue.enqueue.call_args.kwargs["retry"]
        assert retry.max == 2
        assert retry.intervals == [5, 10]

    def test_no_retry_object_when_retries_disabled(
        self, mock_get_queue, mock_queue, mock_config
    ):
        TaskScheduler().enqueue(_noop, retry_count=0)
        assert mock_queue.enqueue.call_args.kwargs["retry"] is None


class TestJobId:
    def test_job_id_is_forwarded(self, mock_get_queue, mock_queue, mock_config):
        TaskScheduler().enqueue(_noop, job_id="idempotent-key")
        assert mock_queue.enqueue.call_args.kwargs["job_id"] == "idempotent-key"

    def test_job_id_absent_by_default(self, mock_get_queue, mock_queue, mock_config):
        TaskScheduler().enqueue(_noop)
        assert mock_queue.enqueue.call_args.kwargs.get("job_id") is None

    def test_enqueue_at_forwards_job_id(self, mock_get_queue, mock_queue, mock_config):
        TaskScheduler().enqueue_at(_noop, datetime.now(UTC), job_id="scheduled-key")
        assert mock_queue.enqueue_at.call_args.kwargs["job_id"] == "scheduled-key"

    def test_enqueue_in_forwards_job_id(self, mock_get_queue, mock_queue, mock_config):
        TaskScheduler().enqueue_in(_noop, 30, job_id="delayed-key")
        assert mock_queue.enqueue_at.call_args.kwargs["job_id"] == "delayed-key"


class TestAmbientTraceContext:
    def test_meta_carries_the_traceparent(self):
        from opentelemetry.sdk.trace import TracerProvider

        provider = TracerProvider()
        with provider.get_tracer("t").start_as_current_span("produce"):
            meta = ambient_job_meta()
        assert "traceparent" in meta

    def test_meta_has_no_traceparent_without_a_span(self):
        assert "traceparent" not in ambient_job_meta()

    def test_enqueue_attaches_the_traceparent(
        self, mock_get_queue, mock_queue, mock_config
    ):
        from opentelemetry.sdk.trace import TracerProvider

        provider = TracerProvider()
        with provider.get_tracer("t").start_as_current_span("produce"):
            TaskScheduler().enqueue(_noop)
        assert "traceparent" in mock_queue.enqueue.call_args.kwargs["meta"]

    def test_caller_meta_still_wins(self, mock_get_queue, mock_queue, mock_config):
        TaskScheduler().enqueue(_noop, meta={"tenant_id": "acme"})
        assert mock_queue.enqueue.call_args.kwargs["meta"]["tenant_id"] == "acme"
