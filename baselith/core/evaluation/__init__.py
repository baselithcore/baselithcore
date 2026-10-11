"""
Core Evaluation Module.

Provides LLM-as-a-Judge evaluation capabilities for assessing
agent response quality across multiple dimensions (relevance,
coherence, faithfulness).

The event-driven ``EvaluationService`` evaluates a deterministic sample of
production flows online: cheap deterministic checks on every sampled flow, an
opt-in LLM-judge rate on top, Prometheus metrics, a per-intent rolling quality
monitor and an opt-in result store.

Also includes the Prompt Regression Testing harness (prompt_eval) for
running structured eval cases against system prompts — see §2.6 of
"Building AI Agents: From Design Patterns to Production".
"""

from baselith.core.evaluation.bake_off import (
    BakeOffResult,
    ModelRunReport,
    run_bake_off,
)
from baselith.core.evaluation.base import BaseLLMEvaluator
from baselith.core.evaluation.consensus import ConsensusEvaluator
from baselith.core.evaluation.fairness import (
    FairnessReport,
    GroupOutcome,
    evaluate_fairness,
)
from baselith.core.evaluation.judges import CompositeEvaluator, RelevanceEvaluator
from baselith.core.evaluation.online_monitor import QualityMonitor
from baselith.core.evaluation.online_store import (
    InMemoryOnlineEvalStore,
    OnlineEvalRecord,
    OnlineEvalStore,
)
from baselith.core.evaluation.promotion import (
    PromotionError,
    PromotionResult,
    checkpoint_to_recorded_run,
    promote_run,
    scrub_text,
)
from baselith.core.evaluation.prompt_eval import (
    CaseResult,
    EvalCase,
    EvalReport,
    PromptEvaluator,
    make_standard_cases,
)
from baselith.core.evaluation.protocols import EvaluationResult, Evaluator, QualityLevel
from baselith.core.evaluation.service import EvaluationService

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
    # Online evaluation of production traffic
    "QualityMonitor",
    "OnlineEvalRecord",
    "OnlineEvalStore",
    "InMemoryOnlineEvalStore",
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
    # Production-run promotion into the eval corpus
    "PromotionError",
    "PromotionResult",
    "checkpoint_to_recorded_run",
    "promote_run",
    "scrub_text",
]
