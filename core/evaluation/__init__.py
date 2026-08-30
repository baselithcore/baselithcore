"""
Core Evaluation Module.

Provides LLM-as-a-Judge evaluation capabilities for assessing
agent response quality across multiple dimensions (relevance,
coherence, faithfulness).

Also includes the Prompt Regression Testing harness (prompt_eval) for
running structured eval cases against system prompts — see §2.6 of
"Building AI Agents: From Design Patterns to Production".
"""

from core.evaluation.bake_off import (
    BakeOffResult,
    ModelRunReport,
    run_bake_off,
)
from core.evaluation.base import BaseLLMEvaluator
from core.evaluation.consensus import ConsensusEvaluator
from core.evaluation.fairness import (
    FairnessReport,
    GroupOutcome,
    evaluate_fairness,
)
from core.evaluation.judges import CompositeEvaluator, RelevanceEvaluator
from core.evaluation.prompt_eval import (
    CaseResult,
    EvalCase,
    EvalReport,
    PromptEvaluator,
    make_standard_cases,
)
from core.evaluation.protocols import EvaluationResult, Evaluator, QualityLevel
from core.evaluation.service import EvaluationService

__all__ = [
    # LLM-as-a-Judge
    "EvaluationService",
    "QualityLevel",
    "EvaluationResult",
    "Evaluator",
    "BaseLLMEvaluator",
    "RelevanceEvaluator",
    "CompositeEvaluator",
    "ConsensusEvaluator",
    # Prompt regression testing
    "EvalCase",
    "CaseResult",
    "EvalReport",
    "PromptEvaluator",
    "make_standard_cases",
    # Multi-model bake-off
    "BakeOffResult",
    "ModelRunReport",
    "run_bake_off",
    # Bias examination (AI Act Art. 10(2)(f)/(g), Art. 15)
    "FairnessReport",
    "GroupOutcome",
    "evaluate_fairness",
]
