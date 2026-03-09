"""
Tests for task queue worker instantiation and configuration.
"""

import pytest
from unittest.mock import patch, MagicMock
from core.task_queue.worker import start_worker
from core.config.task_queue import TaskQueueConfig

# Skip module if optional dependency not installed
pytest.importorskip("rq")


class TestWorkerModule:
    """Tests for core.task_queue.worker module."""

    @patch("core.task_queue.worker.Redis")
    @patch("core.task_queue.worker.Queue")
    @patch("core.task_queue.worker.TenantAwareWorker")
    @patch("core.task_queue.worker.get_task_queue_config")
    def test_start_worker(
        self, mock_get_config, mock_worker_cls, mock_queue_cls, mock_redis
    ):
        """Test start_worker initializes Redis, Connection and Worker correctly with config."""
        # Setup mocks
        mock_config = TaskQueueConfig(
            redis_url="redis://test-redis:6379/1",
            queues=["test_queue_1", "test_queue_2"],
        )
        mock_get_config.return_value = mock_config

        mock_conn_instance = MagicMock()
        mock_redis.from_url.return_value = mock_conn_instance

        mock_queue_instance = MagicMock()
        mock_queue_cls.return_value = mock_queue_instance

        mock_worker_instance = MagicMock()
        mock_worker_cls.return_value = mock_worker_instance

        # Execute
        start_worker()

        # Verify Redis connection created with config URL
        mock_redis.from_url.assert_called_once_with("redis://test-redis:6379/1")

        # Verify Queues created with connection
        assert mock_queue_cls.call_count == 2
        mock_queue_cls.assert_any_call("test_queue_1", connection=mock_conn_instance)
        mock_queue_cls.assert_any_call("test_queue_2", connection=mock_conn_instance)

        # Verify Worker initialized with queues list and connection
        mock_worker_cls.assert_called_once()
        args, kwargs = mock_worker_cls.call_args

        # args[0] should be a list of queues
        assert isinstance(args[0], list)
        assert len(args[0]) == 2

        # Verify connection passed to Worker
        assert kwargs["connection"] == mock_conn_instance

        # Verify worker.work() called
        mock_worker_instance.work.assert_called_once()
