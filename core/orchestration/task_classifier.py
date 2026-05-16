"""
Capability detection: when NOT to use an agent.

Book ch1: "if it's a DAG with no LLM-conditional branches, it's not an
agent task." Many production requests are deterministic pipelines that
do not benefit from an LLM loop, and running one wastes tokens, increases
latency, and adds non-determinism. This classifier inspects a task
description and returns a recommendation: agentic, deterministic, or
ambiguous (the caller decides).

The heuristic is intentionally conservative — when in doubt, route to the
agent. False negatives are fine; false positives (suppressing an agent
that was actually needed) are bad.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Final


class RoutingRecommendation(str, Enum):
    """Result of capability detection."""

    AGENTIC = "agentic"
    DETERMINISTIC = "deterministic"
    AMBIGUOUS = "ambiguous"


# Indicators that LLM reasoning / decision-making is required.
_AGENTIC_SIGNALS: Final[frozenset[str]] = frozenset(
    {
        "analyze",
        "compare",
        "decide",
        "evaluate",
        "explain",
        "investigate",
        "plan",
        "reason",
        "recommend",
        "research",
        "summarize",
        "synthesize",
        "should",
        "why",
        "how",
    }
)

# Indicators that the task is a single fixed operation with no branching.
_DETERMINISTIC_SIGNALS: Final[frozenset[str]] = frozenset(
    {
        "delete",
        "fetch",
        "format",
        "get",
        "insert",
        "list",
        "lookup",
        "rename",
        "set",
        "translate",
        "update",
    }
)


@dataclass(frozen=True)
class TaskSignal:
    """Concrete features extracted from a task description."""

    word_count: int
    has_conditional: bool
    has_question_mark: bool
    agentic_hits: int
    deterministic_hits: int


@dataclass(frozen=True)
class ClassificationResult:
    """The classifier's recommendation plus its evidence."""

    recommendation: RoutingRecommendation
    confidence: float
    signal: TaskSignal
    rationale: str


_CONDITIONAL_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(if|unless|when|depending|whichever|otherwise|but only if)\b",
    re.IGNORECASE,
)
_WORD_RE: Final[re.Pattern[str]] = re.compile(r"[A-Za-z']+")


def _extract_signal(description: str) -> TaskSignal:
    words = [w.lower() for w in _WORD_RE.findall(description)]
    agentic_hits = sum(1 for w in words if w in _AGENTIC_SIGNALS)
    deterministic_hits = sum(1 for w in words if w in _DETERMINISTIC_SIGNALS)
    return TaskSignal(
        word_count=len(words),
        has_conditional=bool(_CONDITIONAL_RE.search(description)),
        has_question_mark="?" in description,
        agentic_hits=agentic_hits,
        deterministic_hits=deterministic_hits,
    )


class TaskClassifier:
    """Heuristic classifier for agentic-vs-deterministic routing."""

    def classify(self, description: str) -> ClassificationResult:
        """Return a recommendation for ``description``."""
        if not description or not description.strip():
            raise ValueError("description must be non-empty")
        signal = _extract_signal(description)

        # Strong agentic indicators short-circuit.
        if signal.has_conditional or signal.has_question_mark:
            return ClassificationResult(
                recommendation=RoutingRecommendation.AGENTIC,
                confidence=0.85,
                signal=signal,
                rationale="conditional or question detected",
            )
        if signal.agentic_hits >= 2:
            return ClassificationResult(
                recommendation=RoutingRecommendation.AGENTIC,
                confidence=0.80,
                signal=signal,
                rationale=f"multiple agentic verbs ({signal.agentic_hits})",
            )

        # Pure deterministic: short single-verb command.
        if (
            signal.deterministic_hits >= 1
            and signal.agentic_hits == 0
            and signal.word_count <= 10
        ):
            return ClassificationResult(
                recommendation=RoutingRecommendation.DETERMINISTIC,
                confidence=0.75,
                signal=signal,
                rationale="short verb-only command without reasoning markers",
            )

        # Single agentic verb without other markers: lean agentic.
        if signal.agentic_hits == 1 and signal.deterministic_hits == 0:
            return ClassificationResult(
                recommendation=RoutingRecommendation.AGENTIC,
                confidence=0.60,
                signal=signal,
                rationale="single agentic verb present",
            )

        return ClassificationResult(
            recommendation=RoutingRecommendation.AMBIGUOUS,
            confidence=0.40,
            signal=signal,
            rationale="no decisive signal; defer to caller",
        )
