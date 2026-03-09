"""
Fine-Tuning Models.

Data models for fine-tuning jobs and configurations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class TrainingStatus(str, Enum):
    """Status of a fine-tuning job."""

    PENDING = "pending"
    VALIDATING = "validating"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class FineTuneProvider(str, Enum):
    """Supported fine-tuning providers."""

    OPENAI = "openai"
    TOGETHER = "together"
    ANYSCALE = "anyscale"
    FIREWORKS = "fireworks"


@dataclass
class FineTuneConfig:
    """Configuration for a fine-tuning job."""

    # Base model
    base_model: str = "gpt-4o-mini-2024-07-18"
    provider: FineTuneProvider = FineTuneProvider.OPENAI

    # Training parameters
    n_epochs: int | str = "auto"  # "auto" or integer
    batch_size: int | str = "auto"
    learning_rate_multiplier: float | str = "auto"

    # Suffix for model name
    suffix: str | None = None

    # Validation
    validation_split: float = 0.1  # 10% for validation

    # Advanced options
    seed: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to API-compatible dict."""
        config: dict[str, Any] = {}

        if self.n_epochs != "auto":
            config["n_epochs"] = self.n_epochs
        if self.batch_size != "auto":
            config["batch_size"] = self.batch_size
        if self.learning_rate_multiplier != "auto":
            config["learning_rate_multiplier"] = self.learning_rate_multiplier
        if self.suffix:
            config["suffix"] = self.suffix
        if self.seed is not None:
            config["seed"] = self.seed

        return config


@dataclass
class FineTuneJob:
    """Represents a fine-tuning job."""

    id: str
    provider: str
    base_model: str
    status: TrainingStatus
    created_at: datetime = field(default_factory=datetime.now)
    finished_at: datetime | None = None
    fine_tuned_model: str | None = None
    training_file_id: str | None = None
    validation_file_id: str | None = None
    trained_tokens: int = 0
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_complete(self) -> bool:
        """Check if job is finished (success or failure)."""
        return self.status in (
            TrainingStatus.SUCCEEDED,
            TrainingStatus.FAILED,
            TrainingStatus.CANCELLED,
        )

    @property
    def duration_seconds(self) -> float | None:
        """Get training duration in seconds."""
        if self.finished_at and self.created_at:
            return (self.finished_at - self.created_at).total_seconds()
        return None


@dataclass
class FineTuneResult:
    """Result of a fine-tuning operation."""

    success: bool
    job: FineTuneJob | None = None
    model_id: str | None = None
    error: str | None = None
    metrics: dict[str, float] = field(default_factory=dict)


@dataclass
class TrainingExample:
    """A single training example."""

    messages: list[dict[str, str]]  # [{"role": "user", "content": "..."}, ...]
    weight: float = 1.0

    def to_jsonl_row(self) -> dict[str, Any]:
        """Convert to JSONL format for training."""
        return {"messages": self.messages}

    @classmethod
    def from_conversation(
        cls,
        user_message: str,
        assistant_response: str,
        system_prompt: str | None = None,
    ) -> TrainingExample:
        """Create from a simple conversation pair."""
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_message})
        messages.append({"role": "assistant", "content": assistant_response})
        return cls(messages=messages)


@dataclass
class EvaluationMetrics:
    """Metrics from model evaluation."""

    accuracy: float = 0.0
    perplexity: float = 0.0
    loss: float = 0.0
    f1_score: float = 0.0
    bleu_score: float = 0.0
    custom_metrics: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, float]:
        """Convert all metrics to dict."""
        result = {
            "accuracy": self.accuracy,
            "perplexity": self.perplexity,
            "loss": self.loss,
            "f1_score": self.f1_score,
            "bleu_score": self.bleu_score,
        }
        result.update(self.custom_metrics)
        return result
