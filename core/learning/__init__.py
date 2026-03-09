"""
Learning Module.

Provides continuous learning capabilities:
- Experience collection and replay
- Reward modeling from feedback
- Policy optimization
- Persistent state with Redis
- Automatic fine-tuning from evaluation feedback
"""

from .feedback import FeedbackCollector, FeedbackItem
from .learning_loop import ContinuousLearner, PersistentLearner
from .evolution import EvolutionService
from .auto_finetuning import AutoFineTuningService, AutoFineTuneConfig

__all__ = [
    "FeedbackCollector",
    "FeedbackItem",
    "ContinuousLearner",
    "PersistentLearner",
    "EvolutionService",
    "AutoFineTuningService",
    "AutoFineTuneConfig",
]
