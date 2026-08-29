"""Unit tests for the skill impact tracker."""

from __future__ import annotations

import pytest

from core.skill_evolution.impact import MAX_OPEN_RUNS, SkillImpactTracker


def test_run_scoped_attribution() -> None:
    tracker = SkillImpactTracker()
    tracker.record_activation("fix-parser", run_id="r1")
    tracker.record_outcome(0.8, run_id="r1")
    impact = tracker.stats()["fix-parser"]
    assert impact.activations == 1
    assert impact.outcomes == 1
    assert impact.mean_score == pytest.approx(0.8)


def test_outcome_for_unknown_run_credits_nothing() -> None:
    tracker = SkillImpactTracker()
    tracker.record_activation("fix-parser", run_id="r1")
    tracker.record_outcome(0.8, run_id="other")
    assert tracker.stats()["fix-parser"].outcomes == 0


def test_windowed_attribution_without_run_ids() -> None:
    tracker = SkillImpactTracker()
    tracker.record_activation("fix-parser")
    tracker.record_outcome(0.6)
    tracker.record_outcome(0.9)  # window cleared: second outcome credits nothing
    impact = tracker.stats()["fix-parser"]
    assert impact.outcomes == 1
    assert impact.mean_score == pytest.approx(0.6)


def test_double_activation_same_run_counts_outcome_once() -> None:
    tracker = SkillImpactTracker()
    tracker.record_activation("fix-parser", run_id="r1")
    tracker.record_activation("fix-parser", run_id="r1")
    tracker.record_outcome(0.5, run_id="r1")
    impact = tracker.stats()["fix-parser"]
    assert impact.activations == 2
    assert impact.outcomes == 1


def test_open_runs_are_lru_capped() -> None:
    tracker = SkillImpactTracker()
    for i in range(MAX_OPEN_RUNS + 10):
        tracker.record_activation("s", run_id=f"r{i}")
    # oldest run evicted: its outcome credits nothing
    tracker.record_outcome(1.0, run_id="r0")
    assert tracker.stats()["s"].outcomes == 0
    # newest run still open
    tracker.record_outcome(1.0, run_id=f"r{MAX_OPEN_RUNS + 9}")
    assert tracker.stats()["s"].outcomes == 1
