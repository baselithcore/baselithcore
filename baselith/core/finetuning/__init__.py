"""
Fine-Tuning Pipeline Module.

Provides tools for fine-tuning LLMs with custom datasets:
- Dataset preparation and validation
- Training orchestration (OpenAI, together.ai)
- Model evaluation and comparison
"""

from baselith.core.finetuning.dataset import DatasetBuilder, DatasetFormat
from baselith.core.finetuning.models import (
    FineTuneConfig,
    FineTuneJob,
    FineTuneResult,
    TrainingStatus,
)
from baselith.core.finetuning.pipeline import FineTuningPipeline

__all__ = [
    "DatasetBuilder",
    "DatasetFormat",
    "FineTuneConfig",
    "FineTuneJob",
    "FineTuneResult",
    "FineTuningPipeline",
    "TrainingStatus",
]
