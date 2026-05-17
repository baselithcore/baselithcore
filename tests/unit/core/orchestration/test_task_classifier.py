"""Unit tests for ``core.orchestration.task_classifier``."""

from __future__ import annotations

import pytest

from core.orchestration.task_classifier import (
    RoutingRecommendation,
    TaskClassifier,
)


def _classify(text: str) -> RoutingRecommendation:
    return TaskClassifier().classify(text).recommendation


class TestAgenticDetection:
    def test_question_is_agentic(self) -> None:
        assert _classify("What is causing the outage?") is RoutingRecommendation.AGENTIC

    def test_conditional_is_agentic(self) -> None:
        assert (
            _classify("Update the record if the user is verified")
            is RoutingRecommendation.AGENTIC
        )

    def test_multiple_agentic_verbs_is_agentic(self) -> None:
        assert (
            _classify("Analyze the logs and recommend a fix")
            is RoutingRecommendation.AGENTIC
        )

    def test_single_agentic_verb_is_agentic(self) -> None:
        assert _classify("Investigate the failure") is RoutingRecommendation.AGENTIC


class TestDeterministicDetection:
    def test_short_get_is_deterministic(self) -> None:
        assert _classify("Get user 42") is RoutingRecommendation.DETERMINISTIC

    def test_short_delete_is_deterministic(self) -> None:
        assert _classify("Delete record 7") is RoutingRecommendation.DETERMINISTIC

    def test_format_is_deterministic(self) -> None:
        assert (
            _classify("Format the JSON output") is RoutingRecommendation.DETERMINISTIC
        )

    def test_long_get_with_reasoning_is_not_deterministic(self) -> None:
        result = _classify(
            "Get the user record but only if their subscription has not expired"
        )
        assert result is RoutingRecommendation.AGENTIC


class TestAmbiguous:
    def test_short_neutral_text_is_ambiguous(self) -> None:
        assert _classify("the cat sat") is RoutingRecommendation.AMBIGUOUS


class TestSignalsAndConfidence:
    def test_signal_carries_features(self) -> None:
        r = TaskClassifier().classify("Why did the system fail?")
        assert r.signal.has_question_mark
        assert r.signal.word_count > 0
        assert r.confidence > 0.5

    def test_rationale_is_non_empty(self) -> None:
        r = TaskClassifier().classify("Recommend a fix")
        assert r.rationale != ""

    def test_empty_description_rejected(self) -> None:
        with pytest.raises(ValueError):
            TaskClassifier().classify("")

    def test_whitespace_only_rejected(self) -> None:
        with pytest.raises(ValueError):
            TaskClassifier().classify("    ")

    def test_confidence_within_unit_interval(self) -> None:
        for text in [
            "What is the answer?",
            "Get item 42",
            "the cat sat",
            "Investigate the issue",
        ]:
            r = TaskClassifier().classify(text)
            assert 0.0 <= r.confidence <= 1.0
