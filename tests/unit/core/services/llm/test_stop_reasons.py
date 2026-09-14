"""Unit tests for stop-reason handling (truncation, refusal, pause_turn)."""

from types import SimpleNamespace

import pytest

from core.services.llm.errors import LLMRefusalError
from core.services.llm.stop_reasons import (
    MAX_PAUSE_TURN_CONTINUATIONS,
    apply_stop_reason,
    is_paused,
    stop_details_from,
)
from core.services.llm.tool_calling import LLMResult


class TestApplyStopReason:
    def test_end_turn_is_a_no_op(self):
        result = apply_stop_reason(LLMResult(text="hi", stop_reason="end_turn"))
        assert result.truncated is False

    def test_tool_use_is_a_no_op(self):
        result = apply_stop_reason(LLMResult(stop_reason="tool_use"))
        assert result.truncated is False

    def test_missing_stop_reason_is_a_no_op(self):
        assert apply_stop_reason(LLMResult(text="hi")).truncated is False

    def test_max_tokens_marks_the_result_truncated(self):
        result = apply_stop_reason(
            LLMResult(text="half an ans", stop_reason="max_tokens")
        )
        assert result.truncated is True
        # The text is still returned: a truncated answer beats no answer, the
        # caller just has to know it is incomplete.
        assert result.text == "half an ans"

    def test_openai_length_finish_reason_also_marks_truncation(self):
        assert apply_stop_reason(LLMResult(stop_reason="length")).truncated is True

    def test_refusal_raises_with_category_and_explanation(self):
        result = LLMResult(
            stop_reason="refusal",
            stop_details={"category": "safety", "explanation": "not doing that"},
        )
        with pytest.raises(LLMRefusalError) as exc_info:
            apply_stop_reason(result)
        assert exc_info.value.category == "safety"
        assert exc_info.value.explanation == "not doing that"

    def test_refusal_without_details_still_raises(self):
        with pytest.raises(LLMRefusalError):
            apply_stop_reason(LLMResult(stop_reason="refusal"))

    def test_allow_refusal_returns_the_result_instead(self):
        result = apply_stop_reason(
            LLMResult(text=None, stop_reason="refusal"), allow_refusal=True
        )
        assert result.stop_reason == "refusal"
        assert result.truncated is False

    def test_pause_turn_is_not_an_error(self):
        # The provider resumes a paused turn on the wire; the orchestration
        # layer must not treat it as truncation or refusal.
        result = apply_stop_reason(LLMResult(stop_reason="pause_turn"))
        assert result.truncated is False


class TestPauseTurn:
    def test_is_paused_detects_the_stop_reason(self):
        assert is_paused("pause_turn") is True
        assert is_paused("end_turn") is False
        assert is_paused(None) is False

    def test_continuation_budget_is_three(self):
        assert MAX_PAUSE_TURN_CONTINUATIONS == 3


class TestStopDetails:
    def test_reads_a_refusal_payload(self):
        response = SimpleNamespace(
            stop_reason="refusal",
            stop_details=SimpleNamespace(category="safety", explanation="no"),
        )
        assert stop_details_from(response) == {
            "category": "safety",
            "explanation": "no",
        }

    def test_reads_a_dict_payload(self):
        response = SimpleNamespace(
            stop_reason="refusal", stop_details={"category": "safety"}
        )
        assert stop_details_from(response) == {"category": "safety"}

    def test_absent_details_are_none(self):
        assert stop_details_from(SimpleNamespace(stop_reason="end_turn")) is None
        assert stop_details_from(SimpleNamespace()) is None
