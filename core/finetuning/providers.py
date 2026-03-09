"""Fine-tuning provider implementations.

Contains provider-specific training, status, and job management logic.
"""

from __future__ import annotations

from core.observability.logging import get_logger
from pathlib import Path
from typing import Any

from .models import (
    FineTuneConfig,
    FineTuneJob,
    FineTuneResult,
    TrainingStatus,
)

from core.config import get_finetuning_config

logger = get_logger(__name__)


class OpenAIProvider:
    """OpenAI fine-tuning provider."""

    SUPPORTED_MODELS = [
        "gpt-4o-mini-2024-07-18",
        "gpt-4o-2024-08-06",
        "gpt-4-0613",
        "gpt-3.5-turbo-0125",
    ]

    def __init__(self, api_key: str | None = None) -> None:
        """Initialize OpenAI provider."""
        self.api_key = api_key or get_finetuning_config().openai_api_key

    @property
    def is_available(self) -> bool:
        """Check if provider is configured."""
        return bool(self.api_key)

    async def train(
        self,
        train_path: Path,
        val_path: Path | None,
        config: FineTuneConfig,
    ) -> FineTuneResult:
        """Start a fine-tuning job on OpenAI."""
        if not self.api_key:
            raise ValueError("OpenAI API key not configured")

        try:
            from openai import AsyncOpenAI
        except ImportError:
            raise ImportError("openai package required: pip install openai") from None

        client = AsyncOpenAI(api_key=self.api_key)

        # Upload training file
        with open(train_path, "rb") as f:
            train_file = await client.files.create(file=f, purpose="fine-tune")

        # Upload validation file if provided
        val_file_id = None
        if val_path:
            with open(val_path, "rb") as f:
                val_file = await client.files.create(file=f, purpose="fine-tune")
                val_file_id = val_file.id

        # Create fine-tuning job
        hyperparameters: dict[str, Any] = {}
        if config.n_epochs != "auto":
            hyperparameters["n_epochs"] = config.n_epochs
        if config.batch_size != "auto":
            hyperparameters["batch_size"] = config.batch_size
        if config.learning_rate_multiplier != "auto":
            hyperparameters["learning_rate_multiplier"] = (
                config.learning_rate_multiplier
            )

        job = await client.fine_tuning.jobs.create(
            training_file=train_file.id,
            validation_file=val_file_id,
            model=config.base_model,
            suffix=config.suffix,
            hyperparameters=hyperparameters if hyperparameters else None,  # type: ignore[arg-type]
            seed=config.seed,
        )

        ft_job = FineTuneJob(
            id=job.id,
            provider="openai",
            base_model=config.base_model,
            status=TrainingStatus(job.status),
            training_file_id=train_file.id,
            validation_file_id=val_file_id,
        )

        logger.info(f"OpenAI fine-tuning job created: {job.id}")
        return FineTuneResult(success=True, job=ft_job)

    async def get_status(self, job_id: str) -> FineTuneJob:
        """Get OpenAI job status."""
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=self.api_key)
        job = await client.fine_tuning.jobs.retrieve(job_id)

        return FineTuneJob(
            id=job.id,
            provider="openai",
            base_model=job.model,
            status=TrainingStatus(job.status),
            fine_tuned_model=job.fine_tuned_model,
            trained_tokens=job.trained_tokens or 0,
            error=job.error.message if job.error else None,
        )

    async def cancel(self, job_id: str) -> bool:
        """Cancel an OpenAI fine-tuning job."""
        try:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(api_key=self.api_key)
            await client.fine_tuning.jobs.cancel(job_id)
            return True
        except Exception as e:
            logger.error(f"Failed to cancel OpenAI job {job_id}: {e}")
            return False

    async def list_jobs(self, limit: int = 10) -> list[FineTuneJob]:
        """List OpenAI fine-tuning jobs."""
        try:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(api_key=self.api_key)
            openai_jobs = await client.fine_tuning.jobs.list(limit=limit)

            return [
                FineTuneJob(
                    id=job.id,
                    provider="openai",
                    base_model=job.model,
                    status=TrainingStatus(job.status),
                    fine_tuned_model=job.fine_tuned_model,
                )
                for job in openai_jobs.data
            ]
        except Exception as e:
            logger.warning(f"Failed to list OpenAI jobs: {e}")
            return []


class TogetherProvider:
    """together.ai fine-tuning provider."""

    SUPPORTED_MODELS = [
        "meta-llama/Llama-3-8b-hf",
        "mistralai/Mistral-7B-v0.1",
        "meta-llama/Llama-2-7b-hf",
    ]

    def __init__(self, api_key: str | None = None) -> None:
        """Initialize together.ai provider."""
        self.api_key = api_key or get_finetuning_config().together_api_key

    @property
    def is_available(self) -> bool:
        """Check if provider is configured."""
        return bool(self.api_key)

    async def train(
        self,
        train_path: Path,
        val_path: Path | None,
        config: FineTuneConfig,
    ) -> FineTuneResult:
        """Start a fine-tuning job on together.ai."""
        if not self.api_key:
            raise ValueError("together.ai API key not configured")

        try:
            import httpx
        except ImportError:
            raise ImportError("httpx package required") from None

        async with httpx.AsyncClient() as client:
            # Upload file
            with open(train_path, "rb") as f:
                file_response = await client.post(
                    "https://api.together.xyz/v1/files",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    files={"file": f},
                    data={"purpose": "fine-tune"},
                    timeout=300.0,
                )
                file_response.raise_for_status()
                file_data = file_response.json()

            # Create fine-tuning job
            job_response = await client.post(
                "https://api.together.xyz/v1/fine-tunes",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "training_file": file_data["id"],
                    "model": config.base_model,
                    "n_epochs": config.n_epochs if config.n_epochs != "auto" else 3,
                    "suffix": config.suffix,
                },
                timeout=60.0,
            )
            job_response.raise_for_status()
            job_data = job_response.json()

        ft_job = FineTuneJob(
            id=job_data["id"],
            provider="together",
            base_model=config.base_model,
            status=TrainingStatus.PENDING,
            training_file_id=file_data["id"],
        )

        return FineTuneResult(success=True, job=ft_job)

    async def get_status(self, job_id: str) -> FineTuneJob:
        """Get together.ai job status."""
        import httpx

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"https://api.together.xyz/v1/fine-tunes/{job_id}",
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
            response.raise_for_status()
            data = response.json()

        return FineTuneJob(
            id=data["id"],
            provider="together",
            base_model=data["model"],
            status=TrainingStatus(data["status"]),
            fine_tuned_model=data.get("output_name"),
        )

    async def cancel(self, job_id: str) -> bool:
        """Cancel a together.ai fine-tuning job."""
        if not self.api_key:
            logger.warning("together.ai API key not configured")
            return False

        try:
            import httpx

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"https://api.together.xyz/v1/fine-tunes/{job_id}/cancel",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    timeout=30.0,
                )
                response.raise_for_status()
                logger.info(f"together.ai fine-tuning job cancelled: {job_id}")
                return True
        except Exception as e:
            logger.error(f"Failed to cancel together.ai job {job_id}: {e}")
            return False

    async def list_jobs(self, limit: int = 10) -> list[FineTuneJob]:
        """List together.ai fine-tuning jobs."""
        if not self.api_key:
            return []

        try:
            import httpx

            async with httpx.AsyncClient() as client:
                response = await client.get(
                    "https://api.together.xyz/v1/fine-tunes",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    timeout=30.0,
                )
                response.raise_for_status()
                data = response.json()

            jobs_data = data.get("data", data) if isinstance(data, dict) else data
            if not isinstance(jobs_data, list):
                jobs_data = []

            return [
                FineTuneJob(
                    id=job["id"],
                    provider="together",
                    base_model=job.get("model", "unknown"),
                    status=TrainingStatus(job.get("status", "unknown")),
                    fine_tuned_model=job.get("output_name"),
                )
                for job in jobs_data[:limit]
            ]
        except Exception as e:
            logger.warning(f"Failed to list together.ai jobs: {e}")
            return []


__all__ = ["OpenAIProvider", "TogetherProvider"]
