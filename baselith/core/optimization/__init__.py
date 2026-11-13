"""
Optimization Module.

The public names are resolved on first access (:pep:`562`, via
:func:`baselith.core._lazy.lazy_exports`) rather than imported here. The vector
store imports ``baselith.core.optimization.caching`` for its Redis cache, and
with an eager ``__init__`` that one import also loaded the prompt compiler, the
evolution engine, the evaluation package and the guardrail pattern tables —
none of which a serving process needs to answer a search.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from baselith.core._lazy import lazy_exports

if TYPE_CHECKING:  # pragma: no cover - the eager view, for type checkers
    from .caching import RedisCache, SemanticCache, get_semantic_cache
    from .compile import (
        CompiledPrompt,
        EvaluatorFactory,
        SupportsEvalRun,
        compile_prompt,
    )
    from .evolution import (
        Candidate,
        CandidateArchive,
        EvolutionBudget,
        EvolutionEngine,
        EvolutionReport,
        ReflectiveMutator,
    )
    from .loop import OptimizationLoop
    from .optimizer import OptimizationSuggestion, PromptOptimizer, TuneResult
    from .tune_gate import (
        TuneEvaluator,
        TuneGateDecision,
        eval_gate_enabled,
        review_candidate,
    )

#: ``{exported name: submodule}``. Keep in step with the literal ``__all__``
#: below — the public API gate reads only the literal.
_EXPORTS: Final[dict[str, str]] = {
    "Candidate": "evolution",
    "CandidateArchive": "evolution",
    "CompiledPrompt": "compile",
    "EvaluatorFactory": "compile",
    "EvolutionBudget": "evolution",
    "EvolutionEngine": "evolution",
    "EvolutionReport": "evolution",
    "OptimizationLoop": "loop",
    "OptimizationSuggestion": "optimizer",
    "PromptOptimizer": "optimizer",
    "RedisCache": "caching",
    "ReflectiveMutator": "evolution",
    "SemanticCache": "caching",
    "SupportsEvalRun": "compile",
    "TuneEvaluator": "tune_gate",
    "TuneGateDecision": "tune_gate",
    "TuneResult": "optimizer",
    "compile_prompt": "compile",
    "eval_gate_enabled": "tune_gate",
    "get_semantic_cache": "caching",
    "review_candidate": "tune_gate",
}

__getattr__ = lazy_exports(__name__, _EXPORTS)


def __dir__() -> list[str]:
    """The public names, so ``dir()`` sees the ones not yet resolved."""
    return sorted(__all__)


__all__ = [
    "Candidate",
    "CandidateArchive",
    "CompiledPrompt",
    "EvaluatorFactory",
    "EvolutionBudget",
    "EvolutionEngine",
    "EvolutionReport",
    "OptimizationLoop",
    "OptimizationSuggestion",
    "PromptOptimizer",
    "RedisCache",
    "ReflectiveMutator",
    "SemanticCache",
    "SupportsEvalRun",
    "TuneEvaluator",
    "TuneGateDecision",
    "TuneResult",
    "compile_prompt",
    "eval_gate_enabled",
    "get_semantic_cache",
    "review_candidate",
]
