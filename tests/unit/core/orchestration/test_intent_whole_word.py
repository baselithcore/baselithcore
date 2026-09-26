"""Built-in intent keywords match whole words only.

Substring matching routed ordinary questions to specialised handlers ahead of
the LLM classifier and the default intent: ``"photo"`` fired on
"photosynthesis", ``"reason"`` on "reasonable", and the ``"baselith-core"``
pattern (a botched rename of ``"multi-agent"``) sent any question naming the
project to the swarm.
"""

from __future__ import annotations

import pytest

from core.orchestration.orchestrator import Orchestrator


@pytest.fixture(scope="module")
def classifier():
    return Orchestrator(default_intent="qa_docs").intent_classifier


def _keyword_intent(classifier, text: str) -> str | None:
    result = classifier._classify_with_keywords(text)
    return None if result is None else result.intent


@pytest.mark.parametrize(
    ("text", "not_intent"),
    [
        ("What is photosynthesis?", "vision_analysis"),
        ("Is this price reasonable?", "complex_reasoning"),
        ("How does baselith-core handle retries?", "collaborative_task"),
        ("Show me the swarming behaviour of bees", "collaborative_task"),
        ("Any simulations published this year?", "scenario_simulation"),
    ],
)
def test_substrings_no_longer_route(classifier, text, not_intent):
    assert _keyword_intent(classifier, text) != not_intent


@pytest.mark.parametrize(
    ("text", "intent"),
    [
        ("Take a photo of the dashboard", "vision_analysis"),
        ("Run OCR on this receipt", "vision_analysis"),
        ("Let's reason step by step", "complex_reasoning"),
        ("Use a multi-agent team for this", "collaborative_task"),
        ("Spin up a swarm to research it", "collaborative_task"),
        ("Run a simulation of the market", "scenario_simulation"),
        ("Reason about the image, please", "multimodal_reasoning"),
    ],
)
def test_whole_word_patterns_still_match(classifier, text, intent):
    assert _keyword_intent(classifier, text) == intent
