"""Tests for Fine-Tuning Pipeline."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from core.finetuning.pipeline import FineTuningPipeline
from core.finetuning.dataset import DatasetBuilder
from core.finetuning.models import (
    FineTuneConfig,
    FineTuneJob,
    FineTuneResult,
    TrainingStatus,
)
from core.finetuning.providers import OpenAIProvider, TogetherProvider


@pytest.fixture
def mock_openai_provider():
    provider = MagicMock(spec=OpenAIProvider)
    provider.is_available = True
    provider.api_key = "fake-key"
    return provider


@pytest.fixture
def mock_together_provider():
    provider = MagicMock(spec=TogetherProvider)
    provider.is_available = True
    provider.api_key = "fake-key"
    return provider


@pytest.fixture
def pipeline(mock_openai_provider, mock_together_provider):
    return FineTuningPipeline(
        openai_provider=mock_openai_provider, together_provider=mock_together_provider
    )


@pytest.mark.asyncio
async def test_dataset_builder():
    builder = DatasetBuilder()
    builder.add_conversation("Hi", "Hello")
    assert builder.size == 1

    is_valid, _ = builder.validate()
    # Should be invalid because size < 10
    assert not is_valid

    for i in range(10):
        builder.add_conversation(f"Q{i}", f"A{i}")

    is_valid, _ = builder.validate()
    assert is_valid


@pytest.mark.asyncio
async def test_start_training_openai(pipeline, mock_openai_provider):
    # Setup mock return
    mock_job = FineTuneJob(
        id="ftjob-123",
        provider="openai",
        base_model="gpt-3.5",
        status=TrainingStatus.QUEUED,
    )
    mock_result = FineTuneResult(success=True, job=mock_job)
    mock_openai_provider.train = AsyncMock(return_value=mock_result)

    # Mock file preparation to avoid real file operations
    with patch(
        "core.finetuning.pipeline.FineTuningPipeline._prepare_file"
    ) as mock_prep:
        mock_prep.return_value = "dummy.jsonl"

        result = await pipeline.start_training(
            training_file="data.jsonl",
            config=FineTuneConfig(base_model="gpt-3.5"),
        )

        assert result.success
        assert result.job.id == "ftjob-123"
        assert result.job.provider == "openai"

        # Verify provider was called
        mock_openai_provider.train.assert_called_once()


@pytest.mark.asyncio
async def test_get_job_status(pipeline, mock_openai_provider):
    job_id = "ftjob-123"

    # Pre-populate job in pipeline
    initial_job = FineTuneJob(
        id=job_id,
        provider="openai",
        base_model="gpt-3.5",
        status=TrainingStatus.QUEUED,
    )
    pipeline._jobs[job_id] = initial_job

    # Mock status update
    updated_job = FineTuneJob(
        id=job_id,
        provider="openai",
        base_model="gpt-3.5",
        status=TrainingStatus.RUNNING,
    )
    mock_openai_provider.get_status = AsyncMock(return_value=updated_job)

    status = await pipeline.get_job_status(job_id)

    assert status.status == TrainingStatus.RUNNING
    mock_openai_provider.get_status.assert_called_with(job_id)
