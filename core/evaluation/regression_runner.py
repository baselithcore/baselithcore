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
ALLOWED_CASE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "case_id",
        "input",
        "expected_keywords",
        "forbidden_keywords",
        "expected_tools",
        "forbidden_tools",
        "max_tool_calls",
        "max_latency_ms",
        "max_cost_usd",
    }
)


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


async def run_regression_async(
    cases: Iterable[TrajectoryCase],
    recorded: dict[str, RecordedRun],
    *,
    threshold: float = DEFAULT_PASS_THRESHOLD,
    judge: Any | None = None,
    judge_min_score: float = DEFAULT_JUDGE_MIN_SCORE,
) -> RegressionReport:
    """Deterministic regression pass, optionally gated by an LLM judge.

    With ``judge`` (an ``core.evaluation.judges`` evaluator — anything
    exposing ``await evaluate(response, query) -> EvaluationResult``) each
    case that passes the deterministic checks is additionally scored; a
    score below ``judge_min_score`` fails the case. Judge scores land in
    ``RegressionReport.judge_scores``.

    Failure semantics are deliberately asymmetric:

    * a **low judge score** fails the case (that is the gate);
    * a **judge error** (provider down, malformed reply) does NOT flip the
      deterministic verdict — the case keeps its deterministic result and
      the case id is recorded in ``judge_errors``, so a flaky judge can
      never turn CI red on its own. Judging is inherently nondeterministic,
      which is why this gate is a separate opt-in entry point.
    """
    case_list = list(cases)  # may be a generator; consumed twice below
    base = run_regression(case_list, recorded, threshold=threshold)
    if judge is None:
        return base

    from dataclasses import replace

    from core.observability.logging import get_logger

    logger = get_logger(__name__)
    judge_scores: dict[str, float] = {}
    judge_errors: list[str] = []
    adjusted: list[TrajectoryResult] = []
    cases_by_id = {case.get("case_id", ""): case for case in case_list}

    for result in base.results:
        run = recorded.get(result.case_id)
        case = cases_by_id.get(result.case_id, {})
        if not result.passed or run is None:
            adjusted.append(result)
            continue
        try:
            outcome = await judge.evaluate(run.output_text, case.get("input", ""))
            score = float(outcome.score)
        except Exception as exc:  # judge flake must not turn CI red
            judge_errors.append(result.case_id)
            logger.warning(
                "LLM judge failed for case %s: %s (keeping deterministic verdict)",
                result.case_id,
                exc,
            )
            adjusted.append(result)
            continue
        judge_scores[result.case_id] = score
        adjusted.append(
            replace(result, passed=False) if score < judge_min_score else result
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
