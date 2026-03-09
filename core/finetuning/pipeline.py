"""
Asynchronous Model Fine-Tuning Orchestrator.

Manages the end-to-end lifecycle of model self-improvement. Facilitates
dataset preparation, multi-provider training execution (OpenAI, Together),
real-time status monitoring, and automated performance evaluation to
continuously refine agent domain expertise.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from datetime import datetime
from pathlib import Path

from core.observability.logging import get_logger
from core.finetuning.models import (
    FineTuneConfig,
    FineTuneJob,
    FineTuneProvider,
    FineTuneResult,
    EvaluationMetrics,
)
from core.finetuning.dataset import DatasetBuilder
from core.finetuning.providers import OpenAIProvider, TogetherProvider
from typing import Union

logger = get_logger(__name__)

# Type alias for providers
ProviderType = Union[OpenAIProvider, TogetherProvider]


class FineTuningPipeline:
    """
    Central coordinator for LLM specialization.

    Implements a provider-agnostic training loop. Handles the complexities
    of file uploads, job polling, and error recovery, providing a clean
    API for triggering and measuring the impact of model refinements within
    the framework.
    """

    def __init__(
        self,
        openai_api_key: str | None = None,
        together_api_key: str | None = None,
        openai_provider: OpenAIProvider | None = None,
        together_provider: TogetherProvider | None = None,
    ) -> None:
        """
        Initialize fine-tuning pipeline.

        Args:
            openai_api_key: (Deprecated) OpenAI API key
            together_api_key: (Deprecated) together.ai API key
            openai_provider: Injected OpenAIProvider instance
            together_provider: Injected TogetherProvider instance
        """
        # DI with fallback to existing logic
        self._openai = openai_provider or OpenAIProvider(openai_api_key)
        self._together = together_provider or TogetherProvider(together_api_key)
        self._jobs: dict[str, FineTuneJob] = {}

        logger.info(
            "finetuning_pipeline_initialized",
            openai_available=self._openai.is_available,
            together_available=self._together.is_available,
        )

    # -------------------------------------------------------------------------
    # Training
    # -------------------------------------------------------------------------

    async def start_training(
        self,
        training_file: str | Path | DatasetBuilder,
        validation_file: str | Path | DatasetBuilder | None = None,
        config: FineTuneConfig | None = None,
    ) -> FineTuneResult:
        """
        Initiate a model fine-tuning process.

        Handles data preparation (converting DatasetBuilder to disk if
        necessary) and dispatches the request to the configured provider
        (OpenAI or Together).

        Args:
            training_file: Dataset for training. Can be a path or a builder.
            validation_file: Optional evaluation dataset.
            config: Tuning hyperparameters and provider selection.

        Returns:
            FineTuneResult: Initial status and identifiers for the new job.
        """
        config = config or FineTuneConfig()

        logger.info(
            "finetuning_start",
            provider=config.provider.value,
            base_model=config.base_model,
        )

        try:
            # Prepare files
            train_path = self._prepare_file(training_file, "train")
            val_path = (
                self._prepare_file(validation_file, "validation")
                if validation_file
                else None
            )

            # Start training based on provider
            provider = self._get_provider(config.provider)
            result = await provider.train(train_path, val_path, config)

            if result.job:
                self._jobs[result.job.id] = result.job

            return result

        except Exception as e:
            logger.error("finetuning_error", error=str(e))
            return FineTuneResult(success=False, error=str(e))

    def _get_provider(self, provider: FineTuneProvider):
        """Get the appropriate provider instance."""
        if provider == FineTuneProvider.OPENAI:
            return self._openai
        elif provider == FineTuneProvider.TOGETHER:
            return self._together
        else:
            raise ValueError(f"Unsupported provider: {provider}")

    def _prepare_file(self, source: str | Path | DatasetBuilder, prefix: str) -> Path:
        """Prepare a training file from various sources."""
        if isinstance(source, DatasetBuilder):
            # Save dataset to temp file
            fd, temp_path_str = tempfile.mkstemp(
                suffix=".jsonl",
                prefix=f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_",
            )
            os.close(fd)
            temp_path = Path(temp_path_str)
            source.save(temp_path)
            return temp_path
        return Path(source)

    # -------------------------------------------------------------------------
    # Job Management
    # -------------------------------------------------------------------------

    async def get_job_status(self, job_id: str) -> FineTuneJob | None:
        """
        Fetch the current execution state of a fine-tuning job.

        Queries the remote provider to update the internal job registry
        with progress, costs, and output models.

        Args:
            job_id: The provider-specific identifier for the job.

        Returns:
            Optional[FineTuneJob]: Updated job details, or None if the
                                 ID is unrecognized.
        """
        if job_id not in self._jobs:
            return None

        job = self._jobs[job_id]

        try:
            provider: ProviderType = (
                self._openai if job.provider == "openai" else self._together
            )
            updated_job = await provider.get_status(job_id)
            self._jobs[job_id] = updated_job
            return updated_job
        except Exception as e:
            logger.error("finetuning_status_error", job_id=job_id, error=str(e))
            return job

    async def wait_for_completion(
        self, job_id: str, poll_interval: int = 60, timeout: int = 7200
    ) -> FineTuneJob:
        """
        Wait for a job to complete.

        Args:
            job_id: Job ID to wait for
            poll_interval: Seconds between status checks
            timeout: Maximum wait time in seconds

        Returns:
            Final job status
        """
        start_time = datetime.now()

        while True:
            job = await self.get_job_status(job_id)
            if job is None:
                raise ValueError(f"Job not found: {job_id}")

            if job.is_complete:
                return job

            elapsed = (datetime.now() - start_time).total_seconds()
            if elapsed > timeout:
                raise TimeoutError(f"Job {job_id} did not complete within {timeout}s")

            logger.info(
                "finetuning_waiting",
                job_id=job_id,
                status=job.status.value,
                elapsed_seconds=elapsed,
            )

            await asyncio.sleep(poll_interval)

    async def cancel_job(self, job_id: str) -> bool:
        """Cancel a running fine-tuning job."""
        job = self._jobs.get(job_id)
        if not job:
            return False

        provider: ProviderType = (
            self._openai if job.provider == "openai" else self._together
        )
        return await provider.cancel(job_id)

    async def list_jobs(
        self, limit: int = 10, provider: FineTuneProvider | None = None
    ) -> list[FineTuneJob]:
        """List recent fine-tuning jobs."""
        jobs: list[FineTuneJob] = []

        if provider is None or provider == FineTuneProvider.OPENAI:
            if self._openai.is_available:
                jobs.extend(await self._openai.list_jobs(limit))

        if provider is None or provider == FineTuneProvider.TOGETHER:
            if self._together.is_available:
                jobs.extend(await self._together.list_jobs(limit))

        return jobs

    # -------------------------------------------------------------------------
    # Model Testing
    # -------------------------------------------------------------------------

    async def test_model(
        self, model_id: str, prompt: str, system_prompt: str = ""
    ) -> str:
        """
        Test a fine-tuned model with a prompt.

        Args:
            model_id: Fine-tuned model ID
            prompt: Test prompt
            system_prompt: Optional system prompt

        Returns:
            Model response
        """
        try:
            from openai import AsyncOpenAI
        except ImportError:
            raise ImportError("openai package required") from None

        client = AsyncOpenAI(api_key=self._openai.api_key)

        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        response = await client.chat.completions.create(
            model=model_id,
            messages=messages,  # type: ignore[arg-type]
        )

        return response.choices[0].message.content or ""

    async def evaluate_model(
        self,
        model_id: str,
        test_dataset: DatasetBuilder | str | Path,
    ) -> EvaluationMetrics:
        """
        Evaluate a fine-tuned model on a test dataset.

        Returns accuracy and other metrics.
        """
        if isinstance(test_dataset, (str, Path)):
            builder = DatasetBuilder()
            builder.add_from_jsonl(test_dataset)
        else:
            builder = test_dataset

        correct = 0
        total = 0

        for example in builder:
            # Get expected response
            expected = None
            prompt = None
            for msg in example.messages:
                if msg["role"] == "assistant":
                    expected = msg["content"]
                elif msg["role"] == "user":
                    prompt = msg["content"]

            if not prompt or not expected:
                continue

            # Get model response
            try:
                response = await self.test_model(model_id, prompt)

                # Simple accuracy: check if response contains expected content
                if expected.lower() in response.lower():
                    correct += 1
                total += 1
            except Exception:
                total += 1

        accuracy = correct / max(total, 1)

        return EvaluationMetrics(
            accuracy=accuracy,
            custom_metrics={
                "total_examples": total,
                "correct": correct,
            },
        )

    # -------------------------------------------------------------------------
    # Properties
    # -------------------------------------------------------------------------

    @property
    def supported_models(self) -> dict[FineTuneProvider, list[str]]:
        """Get supported models per provider."""
        return {
            FineTuneProvider.OPENAI: OpenAIProvider.SUPPORTED_MODELS,
            FineTuneProvider.TOGETHER: TogetherProvider.SUPPORTED_MODELS,
        }
