import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from core.task_queue.jobs.indexing import run_indexing_job, _run_indexing_logic
from core.realtime.events import EventType


class TestIndexingJob:
    @pytest.fixture
    def mock_rq_job(self):
        with patch("core.task_queue.jobs.indexing.get_current_job") as mock:
            job = MagicMock()
            job.id = "indexing-job-123"
            mock.return_value = job
            yield job

    @pytest.fixture
    def mock_indexing_service(self):
        with patch("core.task_queue.jobs.indexing.get_indexing_service") as mock:
            service = MagicMock()
            stats = MagicMock()
            stats.new_documents = 42
            service.index_documents = AsyncMock(return_value=stats)
            mock.return_value = service
            yield service

    @pytest.fixture
    def mock_pubsub(self):
        with patch("core.task_queue.jobs.indexing.PubSubManager") as mock_class:
            instance = mock_class.return_value
            instance.publish = AsyncMock()
            yield instance

    @pytest.mark.asyncio
    async def test_run_indexing_logic_success(self, mock_indexing_service, mock_pubsub):
        processed = await _run_indexing_logic(incremental=True, job_id="job1")

        assert processed == 42
        assert mock_pubsub.publish.call_count == 2

        # Verify Start Event
        start_call = mock_pubsub.publish.call_args_list[0]
        event = start_call[0][1]
        assert event.type == EventType.JOB_STARTED
        assert event.payload["incremental"] is True

        # Verify Completion Event
        done_call = mock_pubsub.publish.call_args_list[1]
        event = done_call[0][1]
        assert event.type == EventType.JOB_COMPLETED
        assert event.payload["processed_docs"] == 42

    @pytest.mark.asyncio
    async def test_run_indexing_logic_failure(self, mock_indexing_service, mock_pubsub):
        mock_indexing_service.index_documents.side_effect = Exception("Indexing failed")

        with pytest.raises(Exception, match="Indexing failed"):
            await _run_indexing_logic(incremental=False, job_id="job_fail")

        # Verify Failure Event
        assert mock_pubsub.publish.call_count == 2  # Start + Failure
        fail_call = mock_pubsub.publish.call_args_list[1]
        event = fail_call[0][1]
        assert event.type == EventType.JOB_FAILED
        assert event.payload["error"] == "Indexing failed"

    def test_run_indexing_job_wrapper(
        self, mock_rq_job, mock_indexing_service, mock_pubsub
    ):
        # This tests the non-async wrapper that uses asyncio.run
        with patch("core.task_queue.jobs.indexing.asyncio.run") as mock_run:
            mock_run.return_value = 10
            res = run_indexing_job(incremental=True)
            assert res == 10
            mock_run.assert_called_once()
