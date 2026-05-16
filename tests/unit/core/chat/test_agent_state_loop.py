"""Tests for the loop-instrumentation fields added to ``AgentState``."""

from __future__ import annotations

from core.chat.agent_state import AgentState
from core.evaluation.trajectory import ToolCall


def _state() -> AgentState:
    return AgentState(request=None)


class TestAgentStateLoopFields:
    def test_default_loop_fields(self) -> None:
        s = _state()
        assert s.iteration_count == 0
        assert s.retry_count == 0
        assert s.cost_usd == 0.0
        assert s.scratchpad_ref is None
        assert s.trajectory == []

    def test_record_tool_call_appends(self) -> None:
        s = _state()
        call: ToolCall = {"name": "search", "args": {"q": "x"}, "ok": True}
        s.record_tool_call(call)
        assert s.trajectory == [call]

    def test_mutating_fields(self) -> None:
        s = _state()
        s.iteration_count += 1
        s.retry_count = 2
        s.cost_usd = 0.05
        s.scratchpad_ref = "thread-42"
        assert s.iteration_count == 1
        assert s.retry_count == 2
        assert s.cost_usd == 0.05
        assert s.scratchpad_ref == "thread-42"

    def test_existing_fields_untouched(self) -> None:
        s = _state()
        s.log("step 1")
        s.log("step 2")
        assert s.logs == ["step 1", "step 2"]
        assert s.next_action == "validate_input"
        assert s.done is False
