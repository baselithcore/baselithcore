import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from core.task_queue.jobs.documents import (
    ingest_document_task,
    batch_ingest_task,
    reindex_collection_task,
)


class TestDocumentJobs:
    @pytest.fixture
    def mock_job(self):
        with patch("core.task_queue.jobs.documents.get_current_job") as mock:
            job = MagicMock()
            job.id = "test-job-id"
            mock.return_value = job
            yield job

    @pytest.fixture
    def mock_indexing_service(self):
        with patch("core.services.indexing.get_indexing_service") as mock:
            service = MagicMock()
            service.ingest_file = AsyncMock()
            service.reindex_collection = AsyncMock()
            mock.return_value = service
            yield service

    @pytest.fixture
    def mock_status(self):
        with (
            patch("core.task_queue.jobs.documents.get_task_tracker") as mock_tracker,
            patch(
                "core.task_queue.jobs.documents.update_job_progress"
            ) as mock_progress,
        ):
            tracker = MagicMock()
            mock_tracker.return_value = tracker
            yield tracker, mock_progress

    def test_ingest_document_task(self, mock_job, mock_indexing_service, mock_status):
        tracker, progress = mock_status
        result_mock = MagicMock()
        result_mock.chunks_created = 10
        mock_indexing_service.ingest_file.return_value = result_mock

        res = ingest_document_task("test.pdf", collection="docs")

        assert res["chunks_created"] == 10
        assert res["status"] == "completed"
        mock_indexing_service.ingest_file.assert_called_once()
        tracker.mark_started.assert_called_once()
        tracker.mark_completed.assert_called_once()

    def test_batch_ingest_task(self, mock_job, mock_indexing_service, mock_status):
        tracker, progress = mock_status
        result_mock = MagicMock()
        result_mock.chunks_created = 5
        mock_indexing_service.ingest_file.return_value = result_mock

        res = batch_ingest_task(["f1.txt", "f2.txt"], collection="docs")

        assert res["total"] == 2
        assert res["successful"] == 2
        assert len(res["results"]) == 2
        assert mock_indexing_service.ingest_file.call_count == 2

    def test_batch_ingest_partial_failure(
        self, mock_job, mock_indexing_service, mock_status
    ):
        tracker, progress = mock_status
        mock_indexing_service.ingest_file.side_effect = [
            MagicMock(chunks_created=5),
            Exception("Fail"),
        ]

        res = batch_ingest_task(["success.txt", "fail.txt"])

        assert res["successful"] == 1
        assert res["failed"] == 1
        assert len(res["failures"]) == 1
        assert res["failures"][0]["file_path"] == "fail.txt"

    def test_reindex_collection_task(
        self, mock_job, mock_indexing_service, mock_status
    ):
        tracker, progress = mock_status
        stats_mock = MagicMock()
        stats_mock.documents_processed = 100
        stats_mock.chunks_created = 500
        mock_indexing_service.reindex_collection.return_value = stats_mock

        res = reindex_collection_task("my_collection", force=True)

        assert res["documents_processed"] == 100
        assert res["chunks_created"] == 500
        mock_indexing_service.reindex_collection.assert_called_once_with(
            collection_name="my_collection", force=True
        )
