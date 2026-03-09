"""
Fine-Tuning Pipeline Module.

Provides tools for fine-tuning LLMs with custom datasets:
- Dataset preparation and validation
- Training orchestration (OpenAI, together.ai)
- Model evaluation and comparison
"""

from core.finetuning.pipeline import FineTuningPipeline
from core.finetuning.dataset import DatasetBuilder, DatasetFormat
from core.finetuning.models import (
    FineTuneJob,
    FineTuneConfig,
    FineTuneResult,
    TrainingStatus,
)

__all__ = [
    "FineTuningPipeline",
    "DatasetBuilder",
    "DatasetFormat",
    "FineTuneJob",
    "FineTuneConfig",
    "FineTuneResult",
    "TrainingStatus",
]
