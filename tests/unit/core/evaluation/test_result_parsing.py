"""What `BaseLLMEvaluator._parse_result` does with an answer it cannot read.

The parser sits between a language model and a score. Everything it fails to
read becomes a zero, and `evaluate` catches broadly enough that a zero from a
parse failure is indistinguishable from a zero the model actually gave. These
tests pin the boundary: every unreadable shape must arrive at the documented
fallback, carrying the feedback that says so, rather than raising somewhere in
the caller and being silently converted into the same zero.
"""

from __future__ import annotations

import pytest

from core.evaluation.base import BaseLLMEvaluator


class _Evaluator(BaseLLMEvaluator):
    """Concrete subclass: the parser under test is inherited, not overridden."""

    def get_prompt(
        self, query: str, response: str, context: dict | None = None
    ) -> str:  # pragma: no cover - the parser under test never calls it
        return ""


@pytest.fixture
def parse():
    return _Evaluator()._parse_result


class TestReadableAnswers:
    def test_a_plain_json_object(self, parse):
        assert parse('{"score": 0.8, "feedback": "good"}') == {
            "score": 0.8,
            "feedback": "good",
        }

    def test_an_object_inside_a_json_fence(self, parse):
        text = 'Here is my evaluation:\n```json\n{"score": 0.5}\n```\nHope that helps.'
        assert parse(text) == {"score": 0.5}

    def test_an_unterminated_fence_still_parses(self, parse):
        assert parse('```json\n{"score": 0.25}') == {"score": 0.25}

    def test_prose_around_a_bare_object_is_not_required_to_parse(self, parse):
        """No fence and no clean JSON: the fallback, not an exception."""
        assert parse('I think the answer scores {"score": 1.0}')["score"] == 0.0


class TestUnreadableAnswers:
    """Each of these used to reach `evaluate` and raise there instead."""

    @pytest.mark.parametrize(
        "text",
        ['["a", "list"]', "42", "true", "null", '"just a string"'],
        ids=["list", "number", "bool", "null", "string"],
    )
    def test_valid_json_that_is_not_an_object(self, parse, text):
        """`json.loads` accepts these; `.get()` in the caller does not.

        Before the guard this returned the list/number/None as-is, and
        `evaluate`'s `result.get("score", 0.0)` raised AttributeError into a
        broad handler — a zero score with no explanation anywhere.
        """
        result = parse(text)

        assert result["score"] == 0.0
        assert result["feedback"] == _Evaluator._UNPARSABLE

    def test_a_fence_containing_malformed_json(self, parse):
        """The second `json.loads` used to run unguarded inside the except."""
        result = parse('```json\n{"score": 0.9,,,}\n```')

        assert result["score"] == 0.0
        assert result["feedback"] == _Evaluator._UNPARSABLE

    def test_a_fence_containing_a_non_object(self, parse):
        result = parse("```json\n[1, 2, 3]\n```")

        assert result["score"] == 0.0
        assert result["feedback"] == _Evaluator._UNPARSABLE

    def test_text_that_is_not_json_at_all(self, parse):
        result = parse("The response was quite good, I would say around 8/10.")

        assert result["score"] == 0.0
        assert result["feedback"] == _Evaluator._UNPARSABLE

    def test_an_empty_answer(self, parse):
        assert parse("")["score"] == 0.0


class TestTheFallbackIsAlwaysUsable:
    """Whatever comes back, the caller's three lookups must not raise."""

    @pytest.mark.parametrize(
        "text", ["[]", "null", "not json", "```json\n{bad}\n```", ""]
    )
    def test_the_caller_can_always_read_a_score(self, parse, text):
        result = parse(text)

        assert isinstance(result, dict)
        assert isinstance(result.get("score", 0.0), int | float)
        result.get("feedback", "")
        result.get("aspects", {})
