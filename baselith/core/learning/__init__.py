"""
Learning Module.

Provides continuous learning capabilities:
- Experience collection and replay
- Reward modeling from feedback
- Policy optimization
- Persistent state with Redis
- Automatic fine-tuning from evaluation feedback
"""

from .auto_finetuning import AutoFineTuneConfig, AutoFineTuningService
from .evolution import EvolutionService
from .feedback import FeedbackCollector, FeedbackItem
from .learning_loop import ContinuousLearner, PersistentLearner

__all__ = [
    "AutoFineTuneConfig",
    "AutoFineTuningService",
    "ContinuousLearner",
    "EvolutionService",
    "FeedbackCollector",
    "FeedbackItem",
    "PersistentLearner",
]
