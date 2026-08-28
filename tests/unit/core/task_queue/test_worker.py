"""
Tests for task queue worker instantiation and configuration.
"""

from unittest.mock import MagicMock, patch

import pytest

from core.config.task_queue import TaskQueueConfig
from core.task_queue.worker import start_worker

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


class TestSchedulerAndConcurrency:
    """The two properties that make delayed jobs actually run."""

    @patch("core.task_queue.worker.Redis")
    @patch("core.task_queue.worker.Queue")
    @patch("core.task_queue.worker.TenantAwareWorker")
    @patch("core.task_queue.worker.get_task_queue_config")
    def test_worker_runs_with_scheduler(
        self, mock_get_config, mock_worker_cls, mock_queue_cls, mock_redis
    ):
        """A worker without the scheduler silently never runs delayed jobs."""
        mock_get_config.return_value = TaskQueueConfig(
            redis_url="redis://test:6379/2", queues=["default"]
        )
        worker = MagicMock()
        mock_worker_cls.return_value = worker

        start_worker()

        worker.work.assert_called_once_with(with_scheduler=True)

    @patch("core.task_queue.worker.Redis")
    @patch("core.task_queue.worker.Queue")
    @patch("core.task_queue.worker.TenantAwareWorker")
    def test_build_worker_registers_dead_letter_handler(
        self, mock_worker_cls, mock_queue_cls, mock_redis
    ):
        from core.task_queue.dead_letter import dead_letter_handler
        from core.task_queue.worker import build_worker

        build_worker(["default"], MagicMock())

        _args, kwargs = mock_worker_cls.call_args
        assert kwargs["exception_handlers"] == [dead_letter_handler]

    @patch("core.task_queue.worker.Process")
    @patch("core.task_queue.worker.Redis")
    @patch("core.task_queue.worker.Queue")
    @patch("core.task_queue.worker.TenantAwareWorker")
    @patch("core.task_queue.worker.get_task_queue_config")
    def test_concurrency_spawns_extra_processes(
        self, mock_get_config, mock_worker_cls, mock_queue_cls, mock_redis, mock_process
    ):
        """`--concurrency N` must actually run N workers, not just print N."""
        mock_get_config.return_value = TaskQueueConfig(
            redis_url="redis://test:6379/2", queues=["default"]
        )
        mock_worker_cls.return_value = MagicMock()

        start_worker(concurrency=3)

        # N - 1 children; the Nth worker runs in the calling process.
        assert mock_process.call_count == 2
        child = mock_process.return_value
        assert child.start.call_count == 2
        assert child.join.call_count == 2

    @patch("core.task_queue.worker.Process")
    @patch("core.task_queue.worker.Redis")
    @patch("core.task_queue.worker.Queue")
    @patch("core.task_queue.worker.TenantAwareWorker")
    @patch("core.task_queue.worker.get_task_queue_config")
    def test_single_worker_spawns_no_children(
        self, mock_get_config, mock_worker_cls, mock_queue_cls, mock_redis, mock_process
    ):
        mock_get_config.return_value = TaskQueueConfig(
            redis_url="redis://test:6379/2", queues=["default"]
        )
        mock_worker_cls.return_value = MagicMock()

        start_worker(concurrency=1)

        mock_process.assert_not_called()


class TestQueueUrlResolution:
    """Producers and consumers must resolve the same Redis database."""

    def test_producer_and_consumer_agree(self, monkeypatch):
        """Enqueueing where no worker listens is a silent, total failure."""
        import core.task_queue as tq

        cfg = TaskQueueConfig(queue_redis_url="redis://queue-host:6379/7")
        monkeypatch.setattr(tq, "get_task_queue_config", lambda: cfg)
        monkeypatch.setattr(tq, "_redis_conn", None)

        captured: dict[str, str] = {}

        def fake_from_url(url, **_kwargs):
            captured["url"] = url
            return MagicMock()

        monkeypatch.setattr(tq.Redis, "from_url", fake_from_url)
        tq.get_queue_redis_connection()

        # The consumer side (core.task_queue.worker) resolves via get_redis_url().
        assert captured["url"] == cfg.get_redis_url() == "redis://queue-host:6379/7"


class TestJobContextRestoration:
    """A worker must run a job under the identity it was enqueued with."""

    def _job(self, meta):
        job = MagicMock()
        job.meta = meta
        return job

    def _run_and_capture(self, monkeypatch, meta):
        """Run perform_job with a stubbed super() and capture ambient context."""
        from core.context import get_current_plugin, get_current_tenant_id
        from core.services.llm.policy import get_bound_llm_policy
        from core.task_queue.worker import TenantAwareWorker

        seen: dict[str, object] = {}

        def _inner(self, job, queue):
            seen["tenant"] = get_current_tenant_id()
            seen["plugin"] = get_current_plugin()
            seen["policy"] = get_bound_llm_policy()
            return "done"

        monkeypatch.setattr("rq.worker.Worker.perform_job", _inner)
        worker = TenantAwareWorker.__new__(TenantAwareWorker)
        result = TenantAwareWorker.perform_job(worker, self._job(meta), MagicMock())
        return result, seen

    def test_plugin_and_policy_are_restored(self, monkeypatch):
        result, seen = self._run_and_capture(
            monkeypatch,
            {
                "tenant_id": "acme",
                "plugin": "baselith_world",
                "llm_policy": {"provider": "ollama", "model": "llama3.2"},
            },
        )

        assert result == "done"
        assert seen["tenant"] == "acme"
        assert seen["plugin"] == "baselith_world"
        assert seen["policy"].provider == "ollama"
        assert seen["policy"].model == "llama3.2"

    def test_context_is_cleared_after_the_job(self, monkeypatch):
        from core.context import get_current_plugin
        from core.services.llm.policy import get_bound_llm_policy

        self._run_and_capture(
            monkeypatch,
            {"plugin": "baselith_world", "llm_policy": {"provider": "openai"}},
        )

        assert get_current_plugin() is None
        assert get_bound_llm_policy() is None

    def test_unattributed_job_binds_nothing(self, monkeypatch):
        _result, seen = self._run_and_capture(monkeypatch, {})

        assert seen["plugin"] is None
        assert seen["policy"] is None
