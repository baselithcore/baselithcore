"""Tests for Worker Monitor."""

import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime
from core.task_queue.monitor import (
    WorkerMonitor,
    WorkerInfo,
    QueueInfo,
    get_worker_monitor,
)


@pytest.fixture
def mock_redis():
    return MagicMock()


@pytest.fixture
def monitor(mock_redis):
    # Mock get_queue_redis_connection if needed, but we pass conn directly
    return WorkerMonitor(conn=mock_redis)


def test_worker_info_to_dict():
    info = WorkerInfo(
        name="worker1",
        state="busy",
        queues=["default"],
        current_job="job1",
        successful_jobs=10,
        failed_jobs=1,
        birth_date=datetime(2023, 1, 1),
        last_heartbeat=datetime(2023, 1, 2),
    )
    d = info.to_dict()
    assert d["name"] == "worker1"
    assert d["birth_date"] == "2023-01-01T00:00:00"


def test_queue_info_to_dict():
    info = QueueInfo(
        name="default",
        job_count=5,
        started_job_count=2,
        deferred_job_count=1,
        finished_job_count=100,
        failed_job_count=3,
    )
    d = info.to_dict()
    assert d["name"] == "default"
    assert d["job_count"] == 5


def test_get_workers(monitor):
    with patch("core.task_queue.monitor.Worker") as MockWorker:
        mm_worker = MagicMock()
        mm_worker.name = "w1"
        mm_worker.get_state.return_value = "idle"
        mm_worker.queues = [MagicMock()]
        mm_worker.queues[0].name = "q1"
        mm_worker.get_current_job_id.return_value = None
        mm_worker.successful_job_count = 5
        mm_worker.failed_job_count = 0
        mm_worker.birth_date = None
        mm_worker.last_heartbeat = None

        MockWorker.all.return_value = [mm_worker]

        workers = monitor.get_workers()
        assert len(workers) == 1
        assert workers[0].name == "w1"
        assert workers[0].state == "idle"


def test_get_worker_count(monitor):
    with patch("core.task_queue.monitor.Worker") as MockWorker:
        MockWorker.count.return_value = 5
        assert monitor.get_worker_count() == 5


def test_get_queue_info(monitor):
    with patch("core.task_queue.monitor.Queue") as MockQueue:
        mq = MagicMock()
        mq.name = "default"
        mq.__len__.return_value = 10
        mq.started_job_registry.count = 2
        mq.deferred_job_registry.count = 1
        mq.finished_job_registry.count = 50
        mq.failed_job_registry.count = 5
        MockQueue.return_value = mq

        info = monitor.get_queue_info("default")
        assert info is not None
        assert info.name == "default"
        assert info.job_count == 10


def test_get_queue_info_error(monitor):
    with patch("core.task_queue.monitor.Queue") as MockQueue:
        MockQueue.side_effect = Exception("error")
        info = monitor.get_queue_info("default")
        assert info is None


def test_get_all_queues(monitor):
    with patch("core.config.get_task_queue_config") as mock_conf:
        mock_conf.return_value.queues = ["q1", "q2"]
        with patch.object(monitor, "get_queue_info") as mock_get_info:
            mock_get_info.side_effect = [QueueInfo("q1", 1, 0, 0, 0, 0), None]
            queues = monitor.get_all_queues()
            assert len(queues) == 1
            assert queues[0].name == "q1"


def test_get_health_status(monitor):
    # Mock everything
    monitor.get_workers = MagicMock(
        return_value=[
            WorkerInfo("w1", "busy", [], "job1", 0, 0, None, None),
            WorkerInfo("w2", "idle", [], None, 0, 0, None, None),
        ]
    )
    monitor.get_all_queues = MagicMock(return_value=[QueueInfo("q1", 10, 0, 0, 0, 5)])

    # Healthy case
    status = monitor.get_health_status()
    assert status["status"] == "healthy"
    assert status["redis_connected"] is True
    assert status["workers"]["active"] == 1
    assert status["workers"]["idle"] == 1

    # Redis failure
    monitor._conn.ping.side_effect = Exception("fail")
    status = monitor.get_health_status()
    assert status["status"] == "unhealthy"

    # No workers
    monitor._conn.ping.side_effect = None
    monitor.get_workers.return_value = []
    status = monitor.get_health_status()
    assert status["status"] == "degraded"

    # High failures
    monitor.get_workers.return_value = [
        WorkerInfo("w1", "busy", [], "job1", 0, 0, None, None)
    ]
    monitor.get_all_queues.return_value = [QueueInfo("q1", 0, 0, 0, 0, 101)]
    status = monitor.get_health_status()
    assert status["status"] == "degraded"


def test_clean_failed_jobs(monitor):
    with patch("core.task_queue.monitor.Queue") as MockQueue:
        mq = MagicMock()
        registry = MagicMock()
        registry.get_job_ids.return_value = ["j1", "j2"]
        mq.failed_job_registry = registry
        MockQueue.return_value = mq

        # Simulate removal success and fail
        registry.remove.side_effect = [None, Exception("fail")]

        count = monitor.clean_failed_jobs("default")
        assert count == 1
        assert registry.remove.call_count == 2


def test_retry_failed_job(monitor):
    with patch("core.task_queue.monitor.Job") as MockJob:
        job = MagicMock()
        MockJob.fetch.return_value = job

        assert monitor.retry_failed_job("j1") is True
        job.requeue.assert_called_once()

        # Failure
        MockJob.fetch.side_effect = Exception("fail")
        assert monitor.retry_failed_job("j1") is False


def test_get_worker_monitor_singleton():
    # Patch where it is imported from in the module, or the source.
    # Since it is imported inside __init__, we should patch existing calls or the source.
    with patch("core.task_queue.get_queue_redis_connection"):
        # Reset the singleton first if it's already set from previous tests?
        # The module level variable _worker_monitor might be set.
        # We need to access it and reset it.
        import core.task_queue.monitor as monitor_module

        original_monitor = monitor_module._worker_monitor
        monitor_module._worker_monitor = None

        try:
            m1 = get_worker_monitor()
            m2 = get_worker_monitor()
            assert m1 is m2
        finally:
            monitor_module._worker_monitor = original_monitor
