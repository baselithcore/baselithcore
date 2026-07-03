"""
Fine-Tuning Pipeline Module.

Provides tools for fine-tuning LLMs with custom datasets:
- Dataset preparation and validation
- Training orchestration (OpenAI, together.ai)
- Model evaluation and comparison
"""

from core.finetuning.dataset import DatasetBuilder, DatasetFormat
from core.finetuning.models import (
    FineTuneConfig,
    FineTuneJob,
    FineTuneResult,
    TrainingStatus,
)
from core.finetuning.pipeline import FineTuningPipeline

__all__ = [
    "DatasetBuilder",
    "DatasetFormat",
    "FineTuneConfig",
    "FineTuneJob",
    "FineTuneResult",
    "FineTuningPipeline",
    "TrainingStatus",
]
