"""
Core Evaluation Module.

Provides LLM-as-a-Judge evaluation capabilities for assessing
agent response quality across multiple dimensions (relevance,
coherence, faithfulness).
"""

from core.evaluation.protocols import QualityLevel, EvaluationResult, Evaluator
from core.evaluation.base import BaseLLMEvaluator
from core.evaluation.judges import RelevanceEvaluator, CompositeEvaluator
from core.evaluation.service import EvaluationService

__all__ = [
    "EvaluationService",
    "QualityLevel",
    "EvaluationResult",
    "Evaluator",
    "BaseLLMEvaluator",
    "RelevanceEvaluator",
    "CompositeEvaluator",
]
