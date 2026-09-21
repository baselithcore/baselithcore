"""
Prompt regression runner.

Loads ``TrajectoryCase`` definitions from a directory of YAML files and
evaluates pre-recorded agent runs against them. Designed for CI: the
caller pipes a JSON file containing the agent's outputs and trajectories,
the runner produces a summary report, and exits non-zero when the pass
rate falls below the configured quality-gate threshold.

The runner is provider-agnostic — it does not invoke an LLM itself. Tests
or upstream jobs capture model output + trajectory beforehand, so the
regression suite is deterministic and replayable.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Final, cast

import yaml

from core.evaluation.trajectory import (
    ToolCall,
    TrajectoryCase,
    TrajectoryEvaluator,
    TrajectoryResult,
    aggregate_pass_rate,
)

DEFAULT_PASS_THRESHOLD: Final[float] = 0.90
# Derived from the evaluator's own case schema so the YAML loader can never
# lag a field the evaluator already honours (it did: ``expected_tool_order``,
# ``expected_tool_args`` and ``reference_fact`` were evaluated but rejected at
# load time, so no corpus could use them).
ALLOWED_CASE_KEYS: Final[frozenset[str]] = frozenset(TrajectoryCase.__annotations__)


class RegressionLoadError(RuntimeError):
    """Raised when a case file or recorded-run file fails validation."""


@dataclass(frozen=True)
class RecordedRun:
    """A single agent execution captured for replay."""

    case_id: str
    output_text: str
    trajectory: list[ToolCall]
    latency_ms: int
    cost_usd: float = 0.0


@dataclass(frozen=True)
class RegressionReport:
    """Aggregate of all cases evaluated."""

    total: int
    passed: int
    failed: int
    pass_rate: float
    results: list[TrajectoryResult] = field(default_factory=list)
    threshold: float = DEFAULT_PASS_THRESHOLD
    # LLM-as-judge extension (populated only by run_regression_async with a
    # judge): per-case judge scores and cases whose judge call errored.
    judge_scores: dict[str, float] = field(default_factory=dict)
    judge_errors: list[str] = field(default_factory=list)

    @property
    def meets_threshold(self) -> bool:
        return self.pass_rate >= self.threshold

    def to_json(self) -> str:
        payload: dict[str, Any] = {
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "pass_rate": self.pass_rate,
            "threshold": self.threshold,
            "meets_threshold": self.meets_threshold,
            "results": [
                {
                    "case_id": r.case_id,
                    "passed": r.passed,
                    "score": r.score,
                    "tool_calls": r.tool_calls,
                    "latency_ms": r.latency_ms,
                    "cost_usd": r.cost_usd,
                    "violations": [asdict(v) for v in r.violations],
                }
                for r in self.results
            ],
        }
        if self.judge_scores or self.judge_errors:
            payload["judge_scores"] = self.judge_scores
            payload["judge_errors"] = self.judge_errors
        return json.dumps(payload, indent=2, sort_keys=True)


def _validate_case(raw: object, source: Path) -> TrajectoryCase:
    if not isinstance(raw, dict):
        raise RegressionLoadError(
            f"{source}: each case must be a mapping, got {type(raw).__name__}"
        )
    keys = set(raw.keys())
    unknown = keys - ALLOWED_CASE_KEYS
    if unknown:
        raise RegressionLoadError(f"{source}: unknown case fields: {sorted(unknown)}")
    case_id = raw.get("case_id")
    if not isinstance(case_id, str) or not case_id:
        raise RegressionLoadError(f"{source}: 'case_id' must be a non-empty string")
    return _coerce_case_dict(raw)


def _coerce_case_dict(raw: dict[str, Any]) -> TrajectoryCase:
    """Trim ``None`` values and pass through to ``TrajectoryCase`` type."""
    return {k: v for k, v in raw.items() if v is not None}  # type: ignore[return-value]


def load_cases(directory: Path | str) -> list[TrajectoryCase]:
    """Load every ``.yaml`` / ``.yml`` file under ``directory`` as a case set."""
    d = Path(directory)
    if not d.exists():
        raise RegressionLoadError(f"case directory does not exist: {d}")
    if not d.is_dir():
        raise RegressionLoadError(f"case path is not a directory: {d}")
    cases: list[TrajectoryCase] = []
    for path in sorted(d.glob("*.y*ml")):
        text = path.read_text(encoding="utf-8")
        data = yaml.safe_load(text)
        if isinstance(data, list):
            for raw in data:
                cases.append(_validate_case(raw, path))
        elif isinstance(data, dict):
            cases.append(_validate_case(data, path))
        else:
            raise RegressionLoadError(
                f"{path}: top-level must be a mapping or list of mappings"
            )
    if not cases:
        raise RegressionLoadError(f"{d}: no cases found")
    return cases


def load_recorded_runs(path: Path | str) -> dict[str, RecordedRun]:
    """Load recorded agent runs from a JSON file, keyed by ``case_id``."""
    p = Path(path)
    if not p.exists():
        raise RegressionLoadError(f"recorded runs file does not exist: {p}")
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise RegressionLoadError(f"{p}: top-level must be a JSON list")
    runs: dict[str, RecordedRun] = {}
    for raw in data:
        if not isinstance(raw, dict):
            raise RegressionLoadError(
                f"{p}: each run must be a mapping, got {type(raw).__name__}"
            )
        case_id = raw.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            raise RegressionLoadError(
                f"{p}: each run must include a non-empty 'case_id'"
            )
        if case_id in runs:
            raise RegressionLoadError(
                f"{p}: duplicate recorded run for case_id={case_id}"
            )
        trajectory_raw = raw.get("trajectory", [])
        if not isinstance(trajectory_raw, list):
            raise RegressionLoadError(f"{p}: 'trajectory' for {case_id} must be a list")
        trajectory: list[ToolCall] = [
            cast(ToolCall, t) for t in trajectory_raw if isinstance(t, dict)
        ]
        runs[case_id] = RecordedRun(
            case_id=case_id,
            output_text=str(raw.get("output_text", "")),
            trajectory=trajectory,
            latency_ms=int(raw.get("latency_ms", 0)),
            cost_usd=float(raw.get("cost_usd", 0.0)),
        )
    return runs


def run_regression(
    cases: Iterable[TrajectoryCase],
    recorded: dict[str, RecordedRun],
    *,
    threshold: float = DEFAULT_PASS_THRESHOLD,
) -> RegressionReport:
    """Evaluate every case against its recorded run. Missing recordings fail."""
    evaluator = TrajectoryEvaluator()
    results: list[TrajectoryResult] = []
    for case in cases:
        case_id = case.get("case_id", "")
        run = recorded.get(case_id)
        if run is None:
            results.append(TrajectoryResult(case_id=case_id, passed=False))
            continue
        results.append(
            evaluator.evaluate(
                case=case,
                output_text=run.output_text,
                trajectory=run.trajectory,
                latency_ms=run.latency_ms,
                cost_usd=run.cost_usd,
            )
        )
    passed = sum(1 for r in results if r.passed)
    rate = aggregate_pass_rate(results)
    return RegressionReport(
        total=len(results),
        passed=passed,
        failed=len(results) - passed,
        pass_rate=rate,
        results=results,
        threshold=threshold,
    )


DEFAULT_JUDGE_MIN_SCORE: Final[float] = 0.7
#: Judge evaluations per case. LLM scoring is nondeterministic, so a single
#: sample makes each verdict a coin flip — one unlucky draw fails a good case
#: and one lucky draw passes a bad one. Scoring k times and gating on the
#: median makes the outcome stable without pretending the judge is exact.
DEFAULT_JUDGE_SAMPLES: Final[int] = 3
#: Judge calls in flight at once across the whole suite.
DEFAULT_JUDGE_MAX_PARALLEL: Final[int] = 4


def _judge_defaults() -> tuple[int, int]:
    """``(samples, max_parallel)`` from settings, falling back to the constants.

    Read at call time so ``EVAL_JUDGE_SAMPLES`` / ``EVAL_JUDGE_MAX_PARALLEL``
    are honoured without an import-time snapshot.
    """
    try:
        from core.config.evaluation import get_evaluation_config

        config = get_evaluation_config()
        return int(config.judge_samples), int(config.judge_max_parallel)
    except Exception:  # silent-ok: the runner is used from CI scripts without an app config; the documented defaults are the safe answer
        return DEFAULT_JUDGE_SAMPLES, DEFAULT_JUDGE_MAX_PARALLEL


def _median(values: list[float]) -> float:
    """Median of a non-empty score list (mean of the middle pair when even)."""
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


async def run_regression_async(
    cases: Iterable[TrajectoryCase],
    recorded: dict[str, RecordedRun],
    *,
    threshold: float = DEFAULT_PASS_THRESHOLD,
    judge: Any | None = None,
    judge_min_score: float = DEFAULT_JUDGE_MIN_SCORE,
    judge_concurrency: int | None = None,
    judge_samples: int | None = None,
) -> RegressionReport:
    """Deterministic regression pass, optionally gated by an LLM judge.

    With ``judge`` (an ``core.evaluation.judges`` evaluator — anything
    exposing ``await evaluate(response, query) -> EvaluationResult``) each
    case that passes the deterministic checks is additionally scored
    ``judge_samples`` times and gated on the **median** of those scores; a
    median below ``judge_min_score`` fails the case. Judge scores land in
    ``RegressionReport.judge_scores``.

    Failure semantics are deliberately asymmetric:

    * a **low median judge score** fails the case (that is the gate);
    * a **judge error** (provider down, malformed reply) does NOT flip the
      deterministic verdict — a case whose samples all errored keeps its
      deterministic result and its id is recorded in ``judge_errors``, so a
      flaky judge can never turn CI red on its own. A case with *some*
      surviving samples is scored on those. Judging is inherently
      nondeterministic, which is why this gate is a separate opt-in entry
      point.

    Args:
        cases: Trajectory cases to evaluate.
        recorded: Recorded runs keyed by ``case_id``.
        threshold: Pass-rate floor for ``RegressionReport.meets_threshold``.
        judge: Optional LLM judge; ``None`` runs the deterministic pass only.
        judge_min_score: Median score below which a case fails.
        judge_concurrency: Maximum judge calls in flight; defaults to
            ``EvaluationConfig.judge_max_parallel`` (4).
        judge_samples: Judge evaluations per case; defaults to
            ``EvaluationConfig.judge_samples`` (3). Values below 1 are
            clamped to 1.
    """
    case_list = list(cases)  # may be a generator; consumed twice below
    base = run_regression(case_list, recorded, threshold=threshold)
    if judge is None:
        return base

    from dataclasses import replace

    from core.evaluation.base import judge_unavailable
    from core.observability.logging import get_logger
    from core.utils.concurrency import bounded_gather

    logger = get_logger(__name__)
    default_samples, default_parallel = _judge_defaults()
    samples = max(1, default_samples if judge_samples is None else judge_samples)
    limit = max(1, default_parallel if judge_concurrency is None else judge_concurrency)
    cases_by_id = {case.get("case_id", ""): case for case in case_list}

    async def _sample(case_id: str, output_text: str, question: str) -> float | None:
        """One judge draw; ``None`` when the call errored."""
        try:
            outcome = await judge.evaluate(output_text, question)
            if judge_unavailable(outcome):
                # The evaluators catch their own provider errors and answer
                # with a scored-zero fallback, so the ``except`` below never
                # fired for the failure mode it was written for: an outage
                # scored every sample 0.0, the median landed under
                # ``judge_min_score``, and the nightly gate reported the whole
                # corpus as a regression. The flag is what tells an absent
                # judgement from a harsh one.
                logger.warning(
                    "LLM judge unavailable for case %s (keeping deterministic verdict)",
                    case_id,
                )
                return None
            return float(outcome.score)
        except Exception as exc:  # judge flake must not turn CI red
            logger.warning(
                "LLM judge failed for case %s: %s (keeping deterministic verdict)",
                case_id,
                exc,
            )
            return None

    # Flatten every (case, sample) pair into ONE bounded fan-out. Bounding the
    # cases and their samples separately would multiply into
    # ``limit * samples`` simultaneous provider calls; flattening keeps the
    # ceiling literal no matter how the suite grows.
    # Keyed by the result's POSITION, never by case_id: a corpus may repeat an
    # id, and grouping by id merged two distinct cases' draws into one median
    # that then decided both.
    pending: list[int] = []
    coroutines: list[Any] = []
    for index, result in enumerate(base.results):
        run = recorded.get(result.case_id)
        if not result.passed or run is None:
            continue  # no wasted LLM call on an already-failed case
        question = str(cases_by_id.get(result.case_id, {}).get("input", ""))
        for _ in range(samples):
            pending.append(index)
            coroutines.append(_sample(result.case_id, run.output_text, question))

    drawn = await bounded_gather(coroutines, limit=limit)
    by_index: dict[int, list[float]] = {index: [] for index in pending}
    for index, value in zip(pending, drawn, strict=True):
        if isinstance(value, float):
            by_index[index].append(value)

    judge_scores: dict[str, float] = {}
    judge_errors: list[str] = []
    adjusted: list[TrajectoryResult] = []
    for index, result in enumerate(base.results):
        scores = by_index.get(index)
        if scores is None:  # case was never judged
            adjusted.append(result)
            continue
        if not scores:  # every sample errored — keep the deterministic verdict
            judge_errors.append(result.case_id)
            adjusted.append(result)
            continue
        median = _median(scores)
        # ``judge_scores`` is id-keyed for the report's JSON shape; with a
        # duplicated id the last occurrence wins there, but each case keeps
        # its own verdict above.
        judge_scores[result.case_id] = median
        adjusted.append(
            replace(result, passed=False) if median < judge_min_score else result
        )

    passed = sum(1 for r in adjusted if r.passed)
    return RegressionReport(
        total=len(adjusted),
        passed=passed,
        failed=len(adjusted) - passed,
        pass_rate=aggregate_pass_rate(adjusted),
        results=adjusted,
        threshold=threshold,
        judge_scores=judge_scores,
        judge_errors=judge_errors,
    )
