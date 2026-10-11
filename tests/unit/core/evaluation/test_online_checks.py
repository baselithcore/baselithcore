"""Deterministic online checks (baselith.core.evaluation.online_checks)."""

from __future__ import annotations

from typing import Any

import pytest

from baselith.core.evaluation.online_checks import (
    CHECK_FLAGS,
    OnlineSample,
    run_online_checks,
)
from baselith.core.evaluation.protocols import QualityLevel


def _sample(response: str, **extra: Any) -> OnlineSample:
    return OnlineSample(intent="qa", query="what is x?", response=response, **extra)


GOOD = "X is a widely used configuration value that controls the cache TTL."


def test_good_answer_scores_one() -> None:
    outcome = run_online_checks(_sample(GOOD), min_chars=20)
    assert outcome.score == 1.0
    assert outcome.quality is QualityLevel.EXCELLENT
    assert outcome.flags == ()
    assert set(outcome.aspects) == {f"not_{flag}" for flag in CHECK_FLAGS}


def test_empty_answer_scores_zero() -> None:
    outcome = run_online_checks(_sample("   "), min_chars=20)
    assert outcome.score == 0.0
    assert outcome.flags == ("empty",)
    assert outcome.quality is QualityLevel.POOR


def test_too_short_answer() -> None:
    outcome = run_online_checks(_sample("Yes."), min_chars=20)
    assert outcome.flags == ("too_short",)
    assert outcome.score == 0.5


@pytest.mark.parametrize(
    "text",
    [
        "I'm sorry, but I can't help with that request today, unfortunately.",
        "I cannot provide that information because it is outside my remit.",
        "Unfortunately, I am unable to answer questions about this topic now.",
        "As an AI language model I do not have opinions about this subject.",
    ],
)
def test_refusals_are_flagged(text: str) -> None:
    outcome = run_online_checks(_sample(text), min_chars=20)
    assert "refusal" in outcome.flags
    assert outcome.score == 0.25


def test_refusal_phrase_deep_in_answer_is_not_a_refusal() -> None:
    text = GOOD + " Note: I cannot guarantee the value on older releases."
    assert "refusal" not in run_online_checks(_sample(text), min_chars=20).flags


def test_guard_verdicts_are_read() -> None:
    blocked = run_online_checks(
        _sample(GOOD, guardrails={"moderation": {"blocked": True}}), min_chars=20
    )
    assert blocked.flags == ("guard_blocked",)
    assert blocked.score == 0.0

    ungrounded = run_online_checks(
        _sample(GOOD, guardrails={"groundedness": {"score": 0.2}}), min_chars=20
    )
    assert ungrounded.flags == ("ungrounded",)
    assert ungrounded.aspects["groundedness"] == 0.2

    redacted = run_online_checks(
        _sample(GOOD, guardrails={"redactions": {"email": 2, "phone": 1}}), min_chars=20
    )
    assert redacted.flags == ()  # a redaction is reported, not scored
    assert redacted.signals["redactions"] == 3.0


def test_latency_and_cost_are_signals() -> None:
    outcome = run_online_checks(
        _sample(GOOD, duration_ms=1234, cost_usd=0.01, tokens=99), min_chars=20
    )
    assert outcome.score == 1.0
    assert outcome.signals["duration_ms"] == 1234.0
    assert outcome.signals["cost_usd"] == 0.01
    assert outcome.signals["tokens"] == 99.0


def test_failed_checks_multiply() -> None:
    outcome = run_online_checks(_sample("I can't."), min_chars=20)
    assert set(outcome.flags) == {"refusal", "too_short"}
    assert outcome.score == pytest.approx(0.125)
