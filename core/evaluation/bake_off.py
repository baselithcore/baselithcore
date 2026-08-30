"""Multi-model bake-off: one eval suite, N models, one comparison matrix.

Model choice decided by vibes is the portability anti-pattern: the routing
policy deserves the same evidence discipline as any other change. This
harness runs a single :class:`~core.evaluation.prompt_eval.EvalCase` suite
against every candidate model — reusing
:class:`~core.evaluation.prompt_eval.PromptEvaluator` per model — and
returns a ranked matrix of pass rate, latency and (optionally) cost, ready
to feed a :class:`~core.models.routing.RoutingPolicy` decision.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from core.evaluation.prompt_eval import EvalCase, EvalReport, PromptEvaluator
from core.observability.logging import get_logger

logger = get_logger(__name__)

#: Optional ``(model, report) -> estimated USD`` for the cost column —
#: typically an adapter over ``core.models.pricing``.
CostEstimator = Callable[[str, EvalReport], float]


@dataclass
class ModelRunReport:
    """One model's row in the bake-off matrix."""

    model: str
    report: EvalReport
    cost_usd: float | None = None


@dataclass
class BakeOffResult:
    """Suite × model comparison matrix."""

    rows: list[ModelRunReport] = field(default_factory=list)

    def best(self) -> ModelRunReport:
        """Highest pass rate; average latency breaks ties (lower wins)."""
        if not self.rows:
            raise ValueError("bake-off produced no rows")
        return max(
            self.rows,
            key=lambda row: (row.report.pass_rate, -row.report.avg_latency),
        )

    def summary(self) -> str:
        """Human-readable ranking table."""
        lines = [
            "=" * 68,
            f"{'model':<28} {'pass rate':>10} {'avg latency':>12} {'cost':>10}",
            "-" * 68,
        ]
        ordered = sorted(
            self.rows,
            key=lambda row: (row.report.pass_rate, -row.report.avg_latency),
            reverse=True,
        )
        for row in ordered:
            cost = f"${row.cost_usd:.4f}" if row.cost_usd is not None else "—"
            lines.append(
                f"{row.model:<28} {row.report.pass_rate:>9.0%} "
                f"{row.report.avg_latency:>11.2f}s {cost:>10}"
            )
        lines.append("=" * 68)
        return "\n".join(lines)


async def run_bake_off(
    *,
    system_prompt: str,
    cases: list[EvalCase],
    models: list[str],
    llm_factory: Callable[[str], Any],
    cost_estimator: CostEstimator | None = None,
    max_concurrent: int = 3,
) -> BakeOffResult:
    """Run ``cases`` against every model and return the comparison matrix.

    Args:
        system_prompt: The agent system prompt under test (held constant —
            the bake-off varies the model, not the prompt).
        cases: The eval suite, shared across all models.
        models: Candidate model identifiers.
        llm_factory: ``model -> LLM service`` configured for that model.
        cost_estimator: Optional ``(model, report) -> USD`` filling the cost
            column; omitted, the column stays empty.
        max_concurrent: Per-model case concurrency (models run sequentially
            so their latency numbers are not cross-contaminated).

    Returns:
        The :class:`BakeOffResult` matrix (one row per model).
    """
    rows: list[ModelRunReport] = []
    for model in models:
        evaluator = PromptEvaluator(
            system_prompt,
            llm_service=llm_factory(model),
            max_concurrent=max_concurrent,
        )
        report = await evaluator.run(cases)
        cost = cost_estimator(model, report) if cost_estimator else None
        logger.info(
            "bake_off_model_done model=%s pass_rate=%.2f", model, report.pass_rate
        )
        rows.append(ModelRunReport(model=model, report=report, cost_usd=cost))
    return BakeOffResult(rows=rows)


__all__ = ["BakeOffResult", "CostEstimator", "ModelRunReport", "run_bake_off"]
