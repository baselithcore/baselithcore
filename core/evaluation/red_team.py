"""Deterministic red-team regression suite for the guardrail surfaces.

Adversarial cases belong in the eval suite permanently, not in a one-off
audit: a jailbreak that was blocked last quarter and passes today is a
regression, and the only way to notice is to re-run it on every build.

The suite is deterministic by construction — every case is replayed through
the regex/scanner layers of :mod:`core.guardrails`, never through an LLM.
No API key, no cost, no flakiness, so it can gate a merge.

Three surfaces are covered, matching the layered defense:

``input``
    :class:`~core.guardrails.input_guard.InputGuard` — what the user typed.
``external``
    :class:`~core.guardrails.indirect.IndirectInjectionScanner` — the emails,
    documents and pages the agent fetches. The 2026 primary threat.
``output``
    :class:`~core.guardrails.output_guard.OutputGuard` — PII and harmful
    content on the way back out.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

__all__ = [
    "RedTeamCase",
    "RedTeamLoadError",
    "RedTeamReport",
    "RedTeamResult",
    "load_red_team_cases",
    "run_red_team_suite",
]

Surface = Literal["input", "external", "output"]
Expectation = Literal["blocked", "allowed", "flagged", "clean", "redacted"]

_SURFACE_EXPECTATIONS: dict[str, set[str]] = {
    "input": {"blocked", "allowed"},
    "external": {"flagged", "clean"},
    "output": {"redacted", "clean"},
}


class RedTeamLoadError(RuntimeError):
    """Raised when a red-team corpus file cannot be parsed."""


@dataclass(frozen=True)
class RedTeamCase:
    """One adversarial case.

    Attributes:
        case_id: Unique identifier, stable across runs.
        surface: Which guardrail surface the payload is fed to.
        expect: Required verdict for the case to pass.
        payload: The adversarial text.
        repeat: Repetition factor for stress payloads (token bombs, unicode
            floods). The effective payload is ``payload * repeat``; keeping
            the factor declarative keeps the corpus file small and readable.
        category: Free-form grouping used in the report (``jailbreak``,
            ``injection``, ``stress``, ``pii``, …).
        note: Why this case exists — read by whoever sees it fail.
    """

    case_id: str
    surface: Surface
    expect: Expectation
    payload: str
    repeat: int = 1
    category: str = "uncategorized"
    note: str = ""

    @property
    def text(self) -> str:
        """The effective payload after applying ``repeat``."""
        return self.payload * self.repeat


@dataclass(frozen=True)
class RedTeamResult:
    """Outcome of replaying one case through its guardrail surface."""

    case_id: str
    surface: str
    category: str
    expected: str
    actual: str
    passed: bool
    detail: str = ""


@dataclass
class RedTeamReport:
    """Aggregate outcome of a red-team suite run."""

    results: list[RedTeamResult] = field(default_factory=list)
    threshold: float = 1.0

    @property
    def total(self) -> int:
        """Number of cases replayed."""
        return len(self.results)

    @property
    def passed(self) -> int:
        """Number of cases whose verdict matched the expectation."""
        return sum(1 for r in self.results if r.passed)

    @property
    def pass_rate(self) -> float:
        """Fraction of cases that passed (1.0 for an empty suite is refused
        by the gate script, not here)."""
        return self.passed / self.total if self.total else 0.0

    @property
    def meets_threshold(self) -> bool:
        """Whether the run clears the configured pass-rate threshold."""
        return self.pass_rate >= self.threshold

    def failures(self) -> list[RedTeamResult]:
        """Every case whose verdict did not match its expectation."""
        return [r for r in self.results if not r.passed]

    def to_json(self) -> str:
        """Serialize the report as indented JSON."""
        return json.dumps(
            {
                "total": self.total,
                "passed": self.passed,
                "pass_rate": round(self.pass_rate, 4),
                "threshold": self.threshold,
                "meets_threshold": self.meets_threshold,
                "results": [asdict(r) for r in self.results],
            },
            indent=2,
            sort_keys=True,
        )


def _parse_case(raw: Any, source: Path) -> RedTeamCase:
    """Validate one raw mapping into a :class:`RedTeamCase`."""
    if not isinstance(raw, dict):
        raise RedTeamLoadError(
            f"{source}: each case must be a mapping, got {type(raw)}"
        )
    missing = {"case_id", "surface", "expect", "payload"} - raw.keys()
    if missing:
        raise RedTeamLoadError(f"{source}: case missing keys {sorted(missing)}")
    surface = str(raw["surface"])
    expect = str(raw["expect"])
    allowed = _SURFACE_EXPECTATIONS.get(surface)
    if allowed is None:
        raise RedTeamLoadError(
            f"{source}: unknown surface '{surface}' "
            f"(expected one of {sorted(_SURFACE_EXPECTATIONS)})"
        )
    if expect not in allowed:
        raise RedTeamLoadError(
            f"{source}: surface '{surface}' cannot expect '{expect}' "
            f"(allowed: {sorted(allowed)})"
        )
    repeat = int(raw.get("repeat", 1))
    if repeat < 1:
        raise RedTeamLoadError(f"{source}: repeat must be >= 1")
    return RedTeamCase(
        case_id=str(raw["case_id"]),
        surface=surface,  # type: ignore[arg-type]
        expect=expect,  # type: ignore[arg-type]
        payload=str(raw["payload"]),
        repeat=repeat,
        category=str(raw.get("category", "uncategorized")),
        note=str(raw.get("note", "")),
    )


def load_red_team_cases(directory: str | Path) -> list[RedTeamCase]:
    """Load every red-team case from the YAML files in *directory*.

    Args:
        directory: Corpus directory (``*.yaml`` / ``*.yml``, non-recursive).

    Returns:
        Cases sorted by ``case_id``.

    Raises:
        RedTeamLoadError: On a missing directory, unparsable file, invalid
            case, or duplicate ``case_id``.
    """
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise RedTeamLoadError("PyYAML is required to load red-team cases") from exc

    root = Path(directory)
    if not root.is_dir():
        raise RedTeamLoadError(f"red-team corpus directory not found: {root}")

    cases: list[RedTeamCase] = []
    seen: set[str] = set()
    for path in sorted([*root.glob("*.yaml"), *root.glob("*.yml")]):
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise RedTeamLoadError(f"{path}: invalid YAML: {exc}") from exc
        if payload is None:
            continue
        if not isinstance(payload, list):
            raise RedTeamLoadError(f"{path}: expected a list of cases")
        for raw in payload:
            case = _parse_case(raw, path)
            if case.case_id in seen:
                raise RedTeamLoadError(f"{path}: duplicate case_id '{case.case_id}'")
            seen.add(case.case_id)
            cases.append(case)
    return sorted(cases, key=lambda c: c.case_id)


def _run_input_case(case: RedTeamCase, guard: Any) -> tuple[str, str]:
    """Replay an ``input`` case; return ``(actual, detail)``."""
    result = guard.validate(case.text)
    actual = "allowed" if result.is_valid else "blocked"
    detail = result.blocked_reason or ""
    if result.detected_patterns:
        detail = f"{detail} patterns={result.detected_patterns[:3]}".strip()
    return actual, detail


def _run_external_case(case: RedTeamCase, scanner: Any) -> tuple[str, str]:
    """Replay an ``external`` case; return ``(actual, detail)``."""
    result = scanner.scan(case.text)
    actual = "flagged" if result.is_suspicious else "clean"
    detail = ", ".join(sorted(kind.value for kind in result.kinds()))
    return actual, detail


def _run_output_case(case: RedTeamCase, guard: Any) -> tuple[str, str]:
    """Replay an ``output`` case; return ``(actual, detail)``."""
    result = guard.filter(case.text)
    redactions = result.redactions or {}
    warnings = result.warnings or []
    actual = "redacted" if (redactions or warnings) else "clean"
    detail = json.dumps(
        {"redactions": redactions, "warnings": warnings}, sort_keys=True
    )
    return actual, detail


def run_red_team_suite(
    cases: list[RedTeamCase],
    *,
    threshold: float = 1.0,
    config: Any | None = None,
) -> RedTeamReport:
    """Replay every case through its guardrail surface.

    Args:
        cases: Cases to replay.
        threshold: Minimum pass rate for :attr:`RedTeamReport.meets_threshold`.
        config: Optional ``GuardrailsConfig`` override. Defaults to the
            shipped defaults so the gate measures the out-of-the-box
            posture, not a locally hardened one.

    Returns:
        The :class:`RedTeamReport` for the run.
    """
    from core.guardrails.config import GuardrailsConfig
    from core.guardrails.indirect import IndirectInjectionScanner
    from core.guardrails.input_guard import InputGuard
    from core.guardrails.output_guard import OutputGuard

    cfg = config or GuardrailsConfig()
    input_guard = InputGuard(cfg)
    output_guard = OutputGuard(cfg)
    scanner = IndirectInjectionScanner()

    results: list[RedTeamResult] = []
    for case in cases:
        if case.surface == "input":
            actual, detail = _run_input_case(case, input_guard)
        elif case.surface == "external":
            actual, detail = _run_external_case(case, scanner)
        else:
            actual, detail = _run_output_case(case, output_guard)
        results.append(
            RedTeamResult(
                case_id=case.case_id,
                surface=case.surface,
                category=case.category,
                expected=case.expect,
                actual=actual,
                passed=actual == case.expect,
                detail=detail,
            )
        )
    return RedTeamReport(results=results, threshold=threshold)
