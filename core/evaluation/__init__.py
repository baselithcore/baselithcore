"""
Core Evaluation Module.

Provides LLM-as-a-Judge evaluation capabilities for assessing
agent response quality across multiple dimensions (relevance,
coherence, faithfulness).

Also includes the Prompt Regression Testing harness (prompt_eval) for
running structured eval cases against system prompts — see §2.6 of
"Building AI Agents: From Design Patterns to Production".
"""

from core.evaluation.protocols import QualityLevel, EvaluationResult, Evaluator
from core.evaluation.base import BaseLLMEvaluator
from core.evaluation.judges import RelevanceEvaluator, CompositeEvaluator
from core.evaluation.service import EvaluationService
from core.evaluation.prompt_eval import (
    EvalCase,
    CaseResult,
    EvalReport,
    PromptEvaluator,
    make_standard_cases,
)

__all__ = [
    # LLM-as-a-Judge
    "EvaluationService",
    "QualityLevel",
    "EvaluationResult",
    "Evaluator",
    "BaseLLMEvaluator",
    "RelevanceEvaluator",
    "CompositeEvaluator",
    # Prompt regression testing
    "EvalCase",
    "CaseResult",
    "EvalReport",
    "PromptEvaluator",
    "make_standard_cases",
]
