"""DSPy-lite prompt compilation — the machine finds the prompt.

Given a trainset of :class:`~core.evaluation.prompt_eval.EvalCase` and the
metric already encoded in those cases, :func:`compile_prompt` bootstraps
few-shot demonstrations from the base prompt's own passing answers, splices
them into a candidate prompt, and lands the candidate in the prompt registry
only when it measurably beats the baseline — the same eval-gated,
candidate-labelled landing as :mod:`core.optimization.tune_gate`.

Pipeline::

    trainset + metric -> bootstrap demos -> candidate -> eval gate -> registry

A held-out ``valset`` is strongly recommended: with the default
(trainset-as-valset) the candidate is scored on the very cases its demos were
harvested from, which optimistically biases the result.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from core.evaluation.prompt_eval import EvalCase, EvalReport, PromptEvaluator
from core.observability.logging import get_logger
from core.optimization.tune_gate import _register_candidate

logger = get_logger(__name__)

#: Header of the rendered demonstrations block appended to the base prompt.
DEMOS_HEADER = "## Examples"


class SupportsEvalRun(Protocol):
    """Minimal evaluator surface :func:`compile_prompt` depends on."""

    async def run(self, cases: list[EvalCase]) -> EvalReport:
        """Run *cases* and return an aggregated report."""
        ...


#: ``(system_prompt, llm_service) -> evaluator`` — injectable in tests;
#: defaults to :class:`~core.evaluation.prompt_eval.PromptEvaluator`.
EvaluatorFactory = Callable[[str, Any], SupportsEvalRun]


@dataclass(frozen=True)
class CompiledPrompt:
    """Outcome of one prompt-compilation run.

    Attributes:
        prompt_name: Registry name the candidate targets.
        template: The compiled candidate (base prompt + demos block); the
            unmodified base prompt when the bootstrap yielded no demos.
        demos: Selected ``(input, output)`` demonstration pairs.
        baseline_pass_rate: Base-prompt pass rate on the evaluation set.
        compiled_pass_rate: Candidate pass rate on the evaluation set.
        registered_version: Registry version the candidate landed as, or
            ``None`` when nothing was registered.
        improved: Whether the candidate strictly beat the baseline.
    """

    prompt_name: str
    template: str
    demos: list[tuple[str, str]]
    baseline_pass_rate: float
    compiled_pass_rate: float
    registered_version: str | None
    improved: bool


def _default_evaluator_factory(system_prompt: str, llm_service: Any) -> SupportsEvalRun:
    """Build the stock :class:`PromptEvaluator` for *system_prompt*."""
    return PromptEvaluator(system_prompt, llm_service=llm_service)


def _harvest_demos(
    trainset: list[EvalCase], report: EvalReport, k_demos: int
) -> list[tuple[str, str]]:
    """Select up to *k_demos* ``(input, output)`` pairs from passing cases.

    Prefers the shortest responses (prompt economy), breaking length ties by
    case name so the selection is deterministic across runs.
    """
    by_name: dict[str, EvalCase] = {}
    for case in trainset:
        by_name.setdefault(case.name, case)
    passing: list[tuple[int, str, str, str]] = []
    for result in report.results:
        if not result.passed:
            continue
        source = by_name.get(result.case_name)
        if source is None:  # pragma: no cover - report/trainset always aligned
            continue
        passing.append(
            (len(result.response), result.case_name, source.user_input, result.response)
        )
    passing.sort(key=lambda item: (item[0], item[1]))
    return [(user_input, response) for _, _, user_input, response in passing[:k_demos]]


def _render_demos(demos: list[tuple[str, str]]) -> str:
    """Render *demos* as a deterministic ``User:``/``Assistant:`` block."""
    blocks = [f"User: {inp}\nAssistant: {out}" for inp, out in demos]
    return DEMOS_HEADER + "\n\n" + "\n\n".join(blocks)


def _assemble(base_prompt: str, demos: list[tuple[str, str]]) -> str:
    """Append the rendered demos block to *base_prompt*."""
    return f"{base_prompt.rstrip()}\n\n{_render_demos(demos)}"


async def _audit(
    prompt_name: str,
    improved: bool,
    baseline_pass_rate: float,
    compiled_pass_rate: float,
    registered_version: str | None,
    demo_count: int,
) -> None:
    """Record the compilation outcome on the audit trail (never raises)."""
    try:
        from core.observability.audit import AuditEventType, get_audit_logger

        event = (
            AuditEventType.SELF_MODIFY_APPLY
            if improved
            else AuditEventType.SELF_MODIFY_REJECT
        )
        await get_audit_logger().log(
            event,
            resource=prompt_name,
            action="prompt_compile.land",
            details={
                "baseline_pass_rate": baseline_pass_rate,
                "compiled_pass_rate": compiled_pass_rate,
                "registered_version": registered_version,
                "demo_count": demo_count,
            },
            success=improved,
        )
    except Exception:  # pragma: no cover - observability only
        logger.debug("prompt_compile_audit_failed", exc_info=True)


async def compile_prompt(
    prompt_name: str,
    base_prompt: str,
    trainset: list[EvalCase],
    *,
    llm_service: Any,
    k_demos: int = 4,
    valset: list[EvalCase] | None = None,
    register: bool = True,
    evaluator_factory: EvaluatorFactory | None = None,
) -> CompiledPrompt:
    """Bootstrap few-shot demos for *base_prompt* and land the winner.

    Runs *base_prompt* over *trainset*, harvests up to *k_demos* passing
    ``(input, output)`` pairs as demonstrations (shortest responses first,
    ties broken by case name), appends them as an ``## Examples`` block, then
    scores baseline vs candidate on *valset*. The candidate lands in the
    prompt registry as the next version labelled ``candidate`` (tune-gate
    semantics) only when it strictly beats the baseline; either outcome is
    audited as a ``self_modify`` event. A bootstrap with zero passing cases
    returns early — an un-demoed copy of the base prompt is never registered.

    Args:
        prompt_name: Registry name to land the candidate under.
        base_prompt: The system prompt to compile.
        trainset: Cases used to bootstrap demonstrations.
        llm_service: LLM service driving the evaluations.
        k_demos: Maximum demonstrations to splice in (must be >= 1).
        valset: Held-out cases for the baseline-vs-candidate comparison —
            strongly recommended. Defaults to *trainset*, which scores the
            candidate on the cases its demos came from and therefore
            optimistically biases the result.
        register: Whether an improved candidate is landed in the registry.
        evaluator_factory: ``(system_prompt, llm_service) -> evaluator``
            override for tests; defaults to :class:`PromptEvaluator`.

    Returns:
        The :class:`CompiledPrompt` describing template, demos, pass rates
        and registration outcome.

    Raises:
        ValueError: If ``k_demos`` is not a positive integer.
    """
    if k_demos < 1:
        raise ValueError("k_demos must be >= 1")
    factory = evaluator_factory or _default_evaluator_factory

    bootstrap_report = await factory(base_prompt, llm_service).run(trainset)
    demos = _harvest_demos(trainset, bootstrap_report, k_demos)
    if not demos:
        logger.info(
            "prompt_compile_no_passing_bootstrap prompt=%s trainset=%d",
            prompt_name,
            len(trainset),
        )
        return CompiledPrompt(
            prompt_name=prompt_name,
            template=base_prompt,
            demos=[],
            baseline_pass_rate=bootstrap_report.pass_rate,
            compiled_pass_rate=bootstrap_report.pass_rate,
            registered_version=None,
            improved=False,
        )

    candidate = _assemble(base_prompt, demos)

    eval_cases = trainset if valset is None else valset
    if valset is None:
        # The bootstrap already scored the base prompt on these exact cases.
        baseline_report = bootstrap_report
    else:
        baseline_report = await factory(base_prompt, llm_service).run(eval_cases)
    candidate_report = await factory(candidate, llm_service).run(eval_cases)

    improved = candidate_report.pass_rate > baseline_report.pass_rate

    registered: str | None = None
    if improved and register:
        registered = _register_candidate(prompt_name, candidate)
    await _audit(
        prompt_name,
        improved,
        baseline_report.pass_rate,
        candidate_report.pass_rate,
        registered,
        len(demos),
    )
    logger.info(
        "prompt_compiled prompt=%s improved=%s baseline=%.3f compiled=%.3f "
        "demos=%d registered=%s",
        prompt_name,
        improved,
        baseline_report.pass_rate,
        candidate_report.pass_rate,
        len(demos),
        registered,
    )
    return CompiledPrompt(
        prompt_name=prompt_name,
        template=candidate,
        demos=demos,
        baseline_pass_rate=baseline_report.pass_rate,
        compiled_pass_rate=candidate_report.pass_rate,
        registered_version=registered,
        improved=improved,
    )


__all__ = [
    "DEMOS_HEADER",
    "CompiledPrompt",
    "EvaluatorFactory",
    "SupportsEvalRun",
    "compile_prompt",
]
