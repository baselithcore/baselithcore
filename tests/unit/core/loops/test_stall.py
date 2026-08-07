"""Stall guard: futility detection across attempts."""

import pytest

from core.loops.stall import StallGuard


class TestStallGuard:
    def test_threshold_below_two_is_rejected(self):
        # A single failure is never a stall.
        with pytest.raises(ValueError):
            StallGuard(threshold=1)

    def test_identical_failures_trip_the_guard(self):
        guard = StallGuard(threshold=3)
        evidence = "FAILED test_a.py::x - AssertionError"
        assert guard.record(evidence).stalled is False
        assert guard.record(evidence).stalled is False
        verdict = guard.record(evidence)
        assert verdict.stalled is True
        assert verdict.repeats == 3
        assert verdict.reason and "no progress" in verdict.reason

    def test_a_different_failure_resets_the_counter(self):
        guard = StallGuard(threshold=2)
        guard.record("FAILED test_a.py::x - AssertionError")
        verdict = guard.record("FAILED test_b.py::y - TypeError")
        assert verdict.stalled is False
        assert verdict.repeats == 1

    def test_progress_then_regression_needs_a_fresh_streak(self):
        guard = StallGuard(threshold=2)
        guard.record("FAILED a")
        guard.record("FAILED b")
        assert guard.record("FAILED a").stalled is False
        assert guard.record("FAILED a").stalled is True

    def test_reset_forgets_history(self):
        guard = StallGuard(threshold=2)
        guard.record("FAILED a")
        guard.reset()
        assert guard.last_fingerprint is None
        assert guard.record("FAILED a").stalled is False
