"""Rolling-window quality monitor for online evaluation.

Each ``(intent, source)`` series keeps two count-based windows of recent
scores: the **window** (the last ``window`` scores) and the **reference** (the
up to ``reference_window`` scores before it). A series is *degraded* when,
with at least ``min_samples`` scores in the window,

* its windowed mean falls below the configured ``floor``, or
* it falls more than ``threshold`` below the reference mean (used once the
  reference holds ``min_samples`` scores of its own).

Hysteresis keeps the state from flapping: a degraded series recovers only once
the windowed mean is back ``recovery_margin`` *inside* both boundaries, and at
least ``min_samples`` scores must arrive between two state changes. While a
series is degraded its reference mean is frozen at the value it had when it
degraded, so a long incident cannot drag the reference down until the bad
state looks normal and "recovers" on its own.

The monitor is per process: with several workers each watches its own share
of the sampled traffic.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any

DEGRADED = "degraded"
RECOVERED = "recovered"


@dataclass(frozen=True, slots=True)
class MonitorTransition:
    """A series changing state (degraded or recovered)."""

    intent: str
    source: str
    state: str
    window_mean: float
    reference_mean: float | None
    floor: float | None
    samples: int
    reason: str

    def to_event(self) -> dict[str, Any]:
        """The ``EVALUATION_DEGRADED`` / ``EVALUATION_RECOVERED`` payload."""
        return {
            "intent": self.intent,
            "source": self.source,
            "window_mean": round(self.window_mean, 4),
            "reference_mean": (
                round(self.reference_mean, 4)
                if self.reference_mean is not None
                else None
            ),
            "floor": self.floor,
            "samples": self.samples,
            "reason": self.reason,
        }


@dataclass(slots=True)
class _Series:
    history: deque[float]
    degraded: bool = False
    frozen_reference: float | None = None
    since_transition: int = 0
    observed: int = 0


class QualityMonitor:
    """Watch per-intent score windows and report degradation transitions.

    Args:
        window: Scores in the recent window.
        reference_window: Scores in the trailing reference window.
        min_samples: Scores needed before judging a window, and between two
            state changes.
        threshold: Drop below the reference mean that marks degradation.
        floor: Optional absolute floor for the windowed mean.
        recovery_margin: Hysteresis band a recovery must clear.
    """

    def __init__(
        self,
        *,
        window: int = 50,
        reference_window: int = 500,
        min_samples: int = 20,
        threshold: float = 0.15,
        floor: float | None = None,
        recovery_margin: float = 0.05,
    ) -> None:
        self.window = max(1, window)
        self.reference_window = max(0, reference_window)
        self.min_samples = max(1, min(min_samples, self.window))
        self.threshold = threshold
        self.floor = floor
        self.recovery_margin = recovery_margin
        self._series: dict[tuple[str, str], _Series] = {}

    def _get(self, key: tuple[str, str]) -> _Series:
        series = self._series.get(key)
        if series is None:
            series = _Series(history=deque(maxlen=self.window + self.reference_window))
            self._series[key] = series
        return series

    def _means(self, series: _Series) -> tuple[float, int, float | None]:
        """``(window mean, window size, reference mean or None)``."""
        scores = list(series.history)
        recent = scores[-self.window :]
        older = scores[: -self.window] if len(scores) > self.window else []
        window_mean = sum(recent) / len(recent)
        reference = sum(older) / len(older) if len(older) >= self.min_samples else None
        return window_mean, len(recent), reference

    def observe(
        self, intent: str, source: str, score: float
    ) -> MonitorTransition | None:
        """Record one score; return the transition it caused, if any.

        Args:
            intent: The (label-capped) intent.
            source: ``checks`` or ``judge``.
            score: The score in ``[0, 1]``.

        Returns:
            A :class:`MonitorTransition` when the series changed state.
        """
        series = self._get((intent, source))
        series.history.append(float(score))
        series.observed += 1
        series.since_transition += 1
        window_mean, size, reference = self._means(series)
        if size < self.min_samples or series.since_transition < self.min_samples:
            return None
        if series.degraded:
            reference = series.frozen_reference
            return self._maybe_recover(
                intent, source, series, window_mean, size, reference
            )
        reason = self._degradation_reason(window_mean, reference)
        if reason is None:
            return None
        series.degraded = True
        series.frozen_reference = reference
        series.since_transition = 0
        return self._transition(
            intent, source, DEGRADED, window_mean, size, reference, reason
        )

    def _degradation_reason(
        self, window_mean: float, reference: float | None
    ) -> str | None:
        if self.floor is not None and window_mean < self.floor:
            return "below_floor"
        if reference is not None and reference - window_mean > self.threshold:
            return "below_reference"
        return None

    def _maybe_recover(
        self,
        intent: str,
        source: str,
        series: _Series,
        window_mean: float,
        size: int,
        reference: float | None,
    ) -> MonitorTransition | None:
        margin = self.recovery_margin
        if self.floor is not None and window_mean < self.floor + margin:
            return None
        if reference is not None and reference - window_mean > self.threshold - margin:
            return None
        series.degraded = False
        series.frozen_reference = None
        series.since_transition = 0
        return self._transition(
            intent, source, RECOVERED, window_mean, size, reference, "recovered"
        )

    def _transition(
        self,
        intent: str,
        source: str,
        state: str,
        window_mean: float,
        size: int,
        reference: float | None,
        reason: str,
    ) -> MonitorTransition:
        return MonitorTransition(
            intent=intent,
            source=source,
            state=state,
            window_mean=window_mean,
            reference_mean=reference,
            floor=self.floor,
            samples=size,
            reason=reason,
        )

    def summary(self) -> list[dict[str, Any]]:
        """Per-series window statistics, sorted by intent then source."""
        rows: list[dict[str, Any]] = []
        for (intent, source), series in sorted(self._series.items()):
            window_mean, size, reference = self._means(series)
            if series.degraded:
                reference = series.frozen_reference
            rows.append(
                {
                    "intent": intent,
                    "source": source,
                    "window_mean": round(window_mean, 4),
                    "window_samples": size,
                    "reference_mean": round(reference, 4)
                    if reference is not None
                    else None,
                    "observed": series.observed,
                    "degraded": series.degraded,
                }
            )
        return rows


__all__ = ["DEGRADED", "RECOVERED", "MonitorTransition", "QualityMonitor"]
