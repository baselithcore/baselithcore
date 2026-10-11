"""Deterministic sampling of production flows (baselith.core.evaluation.online_sampling)."""

from __future__ import annotations

import pytest

from baselith.core.evaluation.online_sampling import OnlineSampler, sample_position


def test_position_is_stable_and_in_unit_interval() -> None:
    first = sample_position("run-123")
    assert first == sample_position("run-123")
    assert 0.0 <= first < 1.0
    assert first != sample_position("run-124")


def test_decision_is_deterministic_per_run() -> None:
    sampler = OnlineSampler(0.3)
    decisions = {
        sampler.decide("qa", f"run-{i}").sampled for _ in range(3) for i in [7]
    }
    assert len(decisions) == 1


@pytest.mark.parametrize("rate", [0.05, 0.2, 0.5])
def test_rate_is_respected_over_many_runs(rate: float) -> None:
    sampler = OnlineSampler(rate)
    n = 20_000
    sampled = sum(sampler.decide("qa", f"run-{i}").sampled for i in range(n))
    assert abs(sampled / n - rate) < 0.015


def test_raising_the_rate_only_adds_runs() -> None:
    low, high = OnlineSampler(0.05), OnlineSampler(0.10)
    runs = [f"run-{i}" for i in range(5_000)]
    low_set = {r for r in runs if low.decide("qa", r).sampled}
    high_set = {r for r in runs if high.decide("qa", r).sampled}
    assert low_set <= high_set


def test_zero_and_full_rates() -> None:
    assert not OnlineSampler(0.0).decide("qa", "run-1").sampled
    assert OnlineSampler(1.0).decide("qa", "run-1").sampled


def test_intent_override_wins() -> None:
    sampler = OnlineSampler(0.0, intent_rates={"vip": 1.0})
    assert sampler.decide("vip", "run-1").sampled
    assert not sampler.decide("qa", "run-1").sampled
    assert sampler.rate_for("vip") == 1.0


def test_judged_runs_are_a_subset_of_sampled_ones() -> None:
    sampler = OnlineSampler(0.2, judge_rate=0.5)  # judge capped at the sample rate
    decisions = [sampler.decide("qa", f"run-{i}") for i in range(5_000)]
    assert all(d.sampled for d in decisions if d.judged)
    judged = sum(d.judged for d in decisions)
    sampled = sum(d.sampled for d in decisions)
    assert judged == sampled  # judge_rate >= rate: every sample is judged


def test_judge_off_by_default() -> None:
    sampler = OnlineSampler(1.0)
    assert not any(sampler.decide("qa", f"run-{i}").judged for i in range(100))


def test_rates_are_clamped() -> None:
    sampler = OnlineSampler(7.0, judge_rate=-1.0, intent_rates={"x": 2.0})
    assert sampler.rate == 1.0
    assert sampler.judge_rate == 0.0
    assert sampler.rate_for("x") == 1.0


def test_missing_run_id_still_samples_at_the_rate() -> None:
    assert OnlineSampler(1.0).decide("qa", None).sampled
    assert not OnlineSampler(0.0).decide("qa", None).sampled
