"""
Unit Tests for Task Queue Module

Tests for status tracking, monitoring, and scheduling.
"""

import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime

from core.task_queue.status import TaskStatus, TaskInfo, TaskTracker
from core.task_queue.monitor import WorkerMonitor, QueueInfo, WorkerInfo

# Check if rq is available
import importlib.util

RQ_AVAILABLE = importlib.util.find_spec("rq") is not None

pytestmark = pytest.mark.skipif(not RQ_AVAILABLE, reason="rq not installed")


class TestTaskStatusEnum:
    """Tests for TaskStatus enum."""

    def test_task_status_values(self):
        """Test TaskStatus values."""
        assert TaskStatus.PENDING.value == "pending"
        assert TaskStatus.COMPLETED.value == "completed"


class TestTaskInfo:
    """Tests for TaskInfo dataclass."""

    def test_task_info_creation(self):
        """Test TaskInfo creation."""
        info = TaskInfo(
            id="test-123",
            name="test_task",
            status=TaskStatus.RUNNING,
            queue="documents",
            progress=50.0,
            message="Processing...",
        )

        assert info.id == "test-123"
        assert info.status == TaskStatus.RUNNING
        assert info.progress == 50.0

    def test_task_info_to_dict(self):
        """Test TaskInfo serialization."""
        info = TaskInfo(
            id="test-456",
            name="serialize_test",
            status=TaskStatus.COMPLETED,
            progress=100.0,
        )

        data = info.to_dict()

        assert data["id"] == "test-456"
        assert data["status"] == "completed"
        assert data["progress"] == 100.0


@pytest.fixture
def mock_conn():
    return MagicMock()


class TestTaskTracker:
    """Tests for TaskTracker."""

    def test_tracker_set_status(self):
        """Test TaskTracker.set_status."""
        mock_conn = MagicMock()
        tracker = TaskTracker(conn=mock_conn)
        tracker.set_status("job-1", TaskStatus.RUNNING, progress=25.0, message="Step 1")

        mock_conn.hset.assert_called_once()
        mock_conn.expire.assert_called_once()

    def test_tracker_get_status(self):
        """Test TaskTracker.get_status."""
        mock_conn = MagicMock()
        mock_conn.hgetall.return_value = {
            b"status": b"running",
            b"progress": b"50.0",
            b"message": b"Processing",
        }

        tracker = TaskTracker(conn=mock_conn)
        result = tracker.get_status("job-1")

        assert result["status"] == "running"
        assert result["progress"] == 50.0

    def test_tracker_mark_completed(self, mock_conn):
        """Test marking task as completed."""
        tracker = TaskTracker(conn=mock_conn)
        tracker.mark_completed("job-1", result={"items": 10})

        call_args = mock_conn.hset.call_args
        mapping = call_args[1]["mapping"]
        assert mapping["status"] == "completed"
        assert mapping["progress"] == 100.0

    def test_tracker_mark_failed(self, mock_conn):
        """Test marking task as failed."""
        tracker = TaskTracker(conn=mock_conn)
        tracker.mark_failed("job-1", error="Bad things")

        call_args = mock_conn.hset.call_args
        mapping = call_args[1]["mapping"]
        assert mapping["status"] == "failed"
        assert mapping["error"] == "Bad things"

    def test_tracker_get_status_json_error(self, mock_conn):
        """Test handling of non-JSON result in get_status."""
        mock_conn.hgetall.return_value = {
            b"status": b"completed",
            b"result": b"not-json-but-string",
        }
        tracker = TaskTracker(conn=mock_conn)
        status = tracker.get_status("job-1")
        assert status["result"] == "not-json-but-string"


class TestStatusFunctions:
    """Tests for standalone status functions."""

    def test_get_task_tracker_singleton(self):
        """Test the lazy singleton get_task_tracker."""
        import core.task_queue.status

        with patch("core.task_queue.status._task_tracker", None):
            with patch("core.task_queue.status.TaskTracker") as mock_tracker_cls:
                with patch("core.task_queue.get_queue_redis_connection"):
                    with patch("core.config.get_task_queue_config"):
                        t1 = core.task_queue.status.get_task_tracker()
                        t2 = core.task_queue.status.get_task_tracker()
                        assert t1 == t2
                        mock_tracker_cls.assert_called_once()

    def test_status_getattr(self):
        """Test __getattr__ for task_tracker."""
        import core.task_queue.status

        assert core.task_queue.status.task_tracker is not None

        with pytest.raises(AttributeError):
            core.task_queue.status.non_existent

    def test_update_job_progress(self):
        """Test update_job_progress helper."""
        import core.task_queue.status

        mock_job = MagicMock()
        mock_job.id = "job-1"

        with patch("core.task_queue.status.get_current_job", return_value=mock_job):
            with patch("core.task_queue.status.get_task_tracker") as mock_get_tracker:
                mock_tracker = MagicMock()
                mock_get_tracker.return_value = mock_tracker

                core.task_queue.status.update_job_progress(50.0, "Halfway")
                mock_tracker.update_progress.assert_called_with(
                    "job-1", 50.0, "Halfway"
                )

    def test_get_job_status(self):
        """Test get_job_status combined logic."""
        import core.task_queue.status

        job_id = "job-123"
        mock_job = MagicMock()
        mock_job.get_status.return_value = "running"
        mock_job.enqueued_at = datetime(2025, 1, 1)
        mock_job.started_at = None
        mock_job.ended_at = None
        mock_job.func_name = "test_func"
        mock_job.origin = "default"

        tracker_data = {"status": "running", "progress": 42.0}

        with patch("core.task_queue.status.Job.fetch", return_value=mock_job):
            with patch("core.task_queue.status.get_task_tracker") as mock_get_tracker:
                mock_get_tracker.return_value.get_status.return_value = tracker_data
                with patch("core.task_queue.get_queue_redis_connection"):
                    res = core.task_queue.status.get_job_status(job_id)

                    assert res["id"] == job_id
                    assert res["rq_status"] == "running"
                    assert res["progress"] == 42.0
                    assert res["func_name"] == "test_func"

    def test_get_job_status_not_found(self):
        """Test get_job_status when job exists neither in RQ nor Tracker."""
        import core.task_queue.status

        with patch(
            "core.task_queue.status.Job.fetch", side_effect=Exception("Not found")
        ):
            with patch("core.task_queue.status.get_task_tracker") as mock_get_tracker:
                mock_get_tracker.return_value.get_status.return_value = None
                with patch("core.task_queue.get_queue_redis_connection"):
                    assert core.task_queue.status.get_job_status("ghost") is None


class TestQueueInit:
    """Tests for core/task_queue/__init__.py functions."""

    def test_get_queue_redis_connection(self):
        """Test getting Redis connection as a singleton."""
        from core.task_queue import get_queue_redis_connection

        with patch("core.task_queue.Redis.from_url") as mock_from_url:
            mock_from_url.return_value = MagicMock()

            # Use a fresh module state for isolation if possible, or just mock the cache
            with patch("core.task_queue._redis_conn", None):
                conn1 = get_queue_redis_connection()
                conn2 = get_queue_redis_connection()

                assert conn1 == conn2
                mock_from_url.assert_called_once()

    def test_get_queue(self):
        """Test getting an RQ queue."""
        from core.task_queue import get_queue

        with patch("core.task_queue.get_queue_redis_connection") as mock_get_conn:
            mock_conn = MagicMock()
            mock_get_conn.return_value = mock_conn

            with patch("core.task_queue.Queue") as mock_queue_cls:
                get_queue("test-queue")
                mock_queue_cls.assert_called_once_with(
                    "test-queue", connection=mock_conn
                )

    def test_module_getattr(self):
        """Test __getattr__ for redis_conn."""
        import core.task_queue

        with patch("core.task_queue.get_queue_redis_connection") as mock_get_conn:
            mock_conn = MagicMock()
            mock_get_conn.return_value = mock_conn

            assert core.task_queue.redis_conn == mock_conn

        with pytest.raises(AttributeError):
            core.task_queue.non_existent_attr


class TestWorkerMonitor:
    """Tests for WorkerMonitor."""

    def test_get_workers_empty(self):
        """Test getting workers when none exist."""
        mock_conn = MagicMock()

        with patch("core.task_queue.monitor.Worker") as mock_worker_class:
            mock_worker_class.all.return_value = []

            monitor = WorkerMonitor(conn=mock_conn)
            workers = monitor.get_workers()

            assert workers == []

    def test_get_worker_count(self):
        """Test getting worker count."""
        mock_conn = MagicMock()

        with patch("core.task_queue.monitor.Worker") as mock_worker_class:
            mock_worker_class.count.return_value = 3

            monitor = WorkerMonitor(conn=mock_conn)
            count = monitor.get_worker_count()

            assert count == 3

    def test_get_all_queues(self):
        """Test getting all queues based on config."""
        mock_conn = MagicMock()

        # Mock Queue class to avoid actual Redis connection
        with patch("core.task_queue.monitor.Queue") as mock_queue_cls:
            mock_queue_instance = MagicMock()
            mock_queue_instance.job_count = 0
            # Setup registry counts
            mock_queue_instance.started_job_registry.count = 0
            mock_queue_instance.deferred_job_registry.count = 0
            mock_queue_instance.finished_job_registry.count = 0
            mock_queue_instance.failed_job_registry.count = 0

            mock_queue_cls.return_value = mock_queue_instance

            # Use real config or mock? Let's rely on default config which has 3 queues
            monitor = WorkerMonitor(conn=mock_conn)
            queues = monitor.get_all_queues()

            # Default config has 3 queues: default, documents, analysis
            assert len(queues) == 3


class TestQueueInfo:
    """Tests for QueueInfo dataclass."""

    def test_queue_info_to_dict(self):
        """Test QueueInfo serialization."""
        info = QueueInfo(
            name="documents",
            job_count=10,
            started_job_count=2,
            deferred_job_count=0,
            finished_job_count=100,
            failed_job_count=5,
        )

        data = info.to_dict()

        assert data["name"] == "documents"
        assert data["job_count"] == 10
        assert data["failed_job_count"] == 5


class TestWorkerInfo:
    """Tests for WorkerInfo dataclass."""

    def test_worker_info_to_dict(self):
        """Test WorkerInfo serialization."""
        info = WorkerInfo(
            name="worker-1",
            state="busy",
            queues=["default", "documents"],
            current_job="job-789",
            successful_jobs=50,
            failed_jobs=2,
            birth_date=datetime(2025, 1, 1, 10, 0, 0),
            last_heartbeat=datetime(2025, 1, 1, 12, 0, 0),
        )

        data = info.to_dict()

        assert data["name"] == "worker-1"
        assert data["state"] == "busy"
