"""Prometheus recording for online evaluation, with bounded label values.

Intents are open-ended (every plugin registers its own), and an unbounded
label set is a time series per value forever. :class:`IntentLabeler` admits
the first ``max_intents`` distinct intents it sees and reports every later
one as ``other``; the monitor and the metrics share the capped label, so a
flood of novel intents can grow neither.

The metric objects themselves live with the rest of the framework's metrics
in :mod:`baselith.core.observability.metrics` (``mas_eval_online_*``).
"""

from __future__ import annotations

from baselith.core.observability.metrics import (
    EVAL_ONLINE_DEGRADED,
    EVAL_ONLINE_DROPPED_TOTAL,
    EVAL_ONLINE_FAILURES_TOTAL,
    EVAL_ONLINE_FLAGS_TOTAL,
    EVAL_ONLINE_QUALITY_TOTAL,
    EVAL_ONLINE_SAMPLES_TOTAL,
    EVAL_ONLINE_SCORE,
)

#: Label used for every intent beyond the cap.
OTHER_INTENT = "other"
_MAX_LABEL_CHARS = 64


class IntentLabeler:
    """Cap the distinct ``intent`` label values to *max_intents*.

    Args:
        max_intents: Distinct intents admitted before folding into ``other``.
    """

    def __init__(self, max_intents: int) -> None:
        self.max_intents = max(1, max_intents)
        self._admitted: set[str] = set()

    def label(self, intent: str) -> str:
        """The label value for *intent* (itself, truncated, or ``other``)."""
        value = intent[:_MAX_LABEL_CHARS] or OTHER_INTENT
        if value in self._admitted:
            return value
        if len(self._admitted) < self.max_intents:
            self._admitted.add(value)
            return value
        return OTHER_INTENT


def record_sample(intent: str) -> None:
    """Count one sampled flow."""
    EVAL_ONLINE_SAMPLES_TOTAL.labels(intent=intent).inc()


def record_score(intent: str, source: str, score: float, quality: str) -> None:
    """Record one score and its quality verdict."""
    EVAL_ONLINE_SCORE.labels(intent=intent, source=source).observe(score)
    EVAL_ONLINE_QUALITY_TOTAL.labels(
        intent=intent, source=source, quality=quality
    ).inc()


def record_flags(intent: str, flags: tuple[str, ...]) -> None:
    """Count each failed deterministic check."""
    for flag in flags:
        EVAL_ONLINE_FLAGS_TOTAL.labels(intent=intent, flag=flag).inc()


def record_failure(stage: str) -> None:
    """Count a step of the pipeline that raised."""
    EVAL_ONLINE_FAILURES_TOTAL.labels(stage=stage).inc()


def record_drop(reason: str) -> None:
    """Count a sample dropped before evaluation."""
    EVAL_ONLINE_DROPPED_TOTAL.labels(reason=reason).inc()


def set_degraded(intent: str, source: str, degraded: bool) -> None:
    """Set the degradation gauge of one series."""
    EVAL_ONLINE_DEGRADED.labels(intent=intent, source=source).set(1 if degraded else 0)


__all__ = [
    "OTHER_INTENT",
    "IntentLabeler",
    "record_drop",
    "record_failure",
    "record_flags",
    "record_sample",
    "record_score",
    "set_degraded",
]
