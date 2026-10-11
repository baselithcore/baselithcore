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

:class:`QualityMonitor` keeps the series in process memory, so with several
workers each watches its own share of the traffic. The decision itself is
:meth:`QualityMonitor.assess`, a pure function of the scores and the series
state, which :mod:`.online_fleet` applies to windows shared through Redis to
monitor the whole fleet.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence
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
            "state": self.state,
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

    def window_stats(self, scores: Sequence[float]) -> tuple[float, int, float | None]:
        """``(window mean, window size, reference mean or None)`` of *scores*.

        Args:
            scores: The series, oldest first (non-empty).
        """
        scores = list(scores)[-(self.window + self.reference_window) :]
        recent = scores[-self.window :]
        older = scores[: -self.window] if len(scores) > self.window else []
        window_mean = sum(recent) / len(recent)
        reference = sum(older) / len(older) if len(older) >= self.min_samples else None
        return window_mean, len(recent), reference

    def assess(
        self,
        intent: str,
        source: str,
        scores: Sequence[float],
        *,
        since_transition: int,
        degraded: bool,
        frozen_reference: float | None,
    ) -> MonitorTransition | None:
        """Decide whether a series changes state; mutates nothing.

        Args:
            intent: The (label-capped) intent.
            source: ``checks`` or ``judge``.
            scores: The series' scores, oldest first.
            since_transition: Scores observed since the last state change.
            degraded: Whether the series is currently degraded.
            frozen_reference: The reference mean frozen when it degraded.

        Returns:
            The transition, or ``None`` when the state holds.
        """
        if not scores:
            return None
        window_mean, size, reference = self.window_stats(scores)
        if size < self.min_samples or since_transition < self.min_samples:
            return None
        if degraded:
            if not self._recovered(window_mean, frozen_reference):
                return None
            return self._transition(
                intent,
                source,
                RECOVERED,
                window_mean,
                size,
                frozen_reference,
                "recovered",
            )
        reason = self._degradation_reason(window_mean, reference)
        if reason is None:
            return None
        return self._transition(
            intent, source, DEGRADED, window_mean, size, reference, reason
        )

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
        transition = self.assess(
            intent,
            source,
            series.history,
            since_transition=series.since_transition,
            degraded=series.degraded,
            frozen_reference=series.frozen_reference,
        )
        if transition is not None:
            series.degraded = transition.state == DEGRADED
            series.frozen_reference = (
                transition.reference_mean if series.degraded else None
            )
            series.since_transition = 0
        return transition

    def _degradation_reason(
        self, window_mean: float, reference: float | None
    ) -> str | None:
        if self.floor is not None and window_mean < self.floor:
            return "below_floor"
        if reference is not None and reference - window_mean > self.threshold:
            return "below_reference"
        return None

    def _recovered(self, window_mean: float, reference: float | None) -> bool:
        margin = self.recovery_margin
        if self.floor is not None and window_mean < self.floor + margin:
            return False
        return not (
            reference is not None and reference - window_mean > self.threshold - margin
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

    def series_row(
        self,
        intent: str,
        source: str,
        scores: Sequence[float],
        *,
        observed: int,
        degraded: bool,
        frozen_reference: float | None,
    ) -> dict[str, Any]:
        """One row of :meth:`summary` for a series held anywhere.

        Args:
            intent: The intent label.
            source: ``checks`` or ``judge``.
            scores: The series' scores, oldest first (non-empty).
            observed: Scores observed over the series' life.
            degraded: Whether the series is degraded.
            frozen_reference: The reference mean frozen when it degraded.
        """
        window_mean, size, reference = self.window_stats(scores)
        if degraded:
            reference = frozen_reference
        return {
            "intent": intent,
            "source": source,
            "window_mean": round(window_mean, 4),
            "window_samples": size,
            "reference_mean": round(reference, 4) if reference is not None else None,
            "observed": observed,
            "degraded": degraded,
        }

    def summary(self) -> list[dict[str, Any]]:
        """Per-series window statistics, sorted by intent then source."""
        return [
            self.series_row(
                intent,
                source,
                series.history,
                observed=series.observed,
                degraded=series.degraded,
                frozen_reference=series.frozen_reference,
            )
            for (intent, source), series in sorted(self._series.items())
        ]


__all__ = ["DEGRADED", "RECOVERED", "MonitorTransition", "QualityMonitor"]
