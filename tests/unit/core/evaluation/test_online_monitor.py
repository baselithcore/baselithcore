"""Rolling-window quality monitor (baselith.core.evaluation.online_monitor)."""

from __future__ import annotations

from baselith.core.evaluation.online_monitor import (
    DEGRADED,
    RECOVERED,
    QualityMonitor,
)


def _feed(monitor: QualityMonitor, score: float, n: int, intent: str = "qa") -> list:
    return [
        t for t in (monitor.observe(intent, "checks", score) for _ in range(n)) if t
    ]


def _monitor(**kw) -> QualityMonitor:
    params = {"window": 10, "reference_window": 40, "min_samples": 5, "threshold": 0.2}
    params.update(kw)
    return QualityMonitor(**params)


def test_no_verdict_before_min_samples() -> None:
    monitor = _monitor(floor=0.9)
    assert _feed(monitor, 0.0, 4) == []
    assert monitor.summary()[0]["degraded"] is False


def test_floor_degradation_then_recovery() -> None:
    monitor = _monitor(floor=0.6, recovery_margin=0.1)
    transitions = _feed(monitor, 0.2, 5)
    assert [t.state for t in transitions] == [DEGRADED]
    assert transitions[0].reason == "below_floor"
    assert transitions[0].to_event()["intent"] == "qa"

    # Back at the floor is not enough: recovery must clear floor + margin.
    assert _feed(monitor, 0.65, 10) == []
    assert monitor.summary()[0]["degraded"] is True

    transitions = _feed(monitor, 1.0, 10)
    assert [t.state for t in transitions] == [RECOVERED]
    assert monitor.summary()[0]["degraded"] is False


def test_reference_degradation() -> None:
    monitor = _monitor()
    assert _feed(monitor, 0.9, 40) == []
    transitions = _feed(monitor, 0.5, 10)
    assert len(transitions) == 1
    t = transitions[0]
    assert t.state == DEGRADED and t.reason == "below_reference"
    assert t.reference_mean is not None and t.reference_mean > t.window_mean + 0.2


def test_reference_is_frozen_while_degraded() -> None:
    monitor = _monitor()
    _feed(monitor, 0.9, 40)
    assert _feed(monitor, 0.5, 10)
    # A long incident fills the reference window with bad scores; the frozen
    # reference keeps it degraded instead of drifting into "normal".
    assert _feed(monitor, 0.5, 200) == []
    row = monitor.summary()[0]
    assert row["degraded"] is True
    assert row["reference_mean"] >= 0.85


def _oscillate(monitor: QualityMonitor) -> list[str]:
    """Phases of 10 scores just below, then just above, the 0.6 floor."""
    states = []
    for phase in range(20):
        score = 0.55 if phase % 2 == 0 else 0.66
        states += [t.state for t in _feed(monitor, score, 10)]
    return states


def test_hysteresis_prevents_flapping() -> None:
    # Without a margin the series flaps on every phase change...
    flapping = _oscillate(_monitor(floor=0.6, recovery_margin=0.0))
    assert flapping.count(RECOVERED) > 3
    # ...with one, it degrades once and stays degraded.
    damped = _oscillate(_monitor(floor=0.6, recovery_margin=0.1))
    assert damped == [DEGRADED]


def test_series_are_independent() -> None:
    monitor = _monitor(floor=0.6)
    _feed(monitor, 0.1, 5, intent="bad")
    _feed(monitor, 1.0, 5, intent="good")
    rows = {r["intent"]: r for r in monitor.summary()}
    assert rows["bad"]["degraded"] is True
    assert rows["good"]["degraded"] is False
    assert rows["good"]["window_mean"] == 1.0
