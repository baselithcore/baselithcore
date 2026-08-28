"""Tests for the native tool-calling ReAct loop (core.reasoning.react_native)."""

from types import SimpleNamespace

import pytest

from core.reasoning.react import ReActAgent, StepType, ToolDefinition
from core.reasoning.react_native import (
    build_tool_specs,
    infer_tool_parameters,
    resolve_native_mode,
)
from core.services.llm.tool_calling import LLMResult, ToolCall


class ScriptedNativeLLM:
    """LLM double exposing the structured ``generate`` API with a script."""

    def __init__(self, results, *, native_enabled=True, provider_native=True):
        self._results = list(results)
        self.generate_calls: list[dict] = []
        self.generate_response_calls: list[dict] = []
        self.config = SimpleNamespace(enable_native_tools=native_enabled)
        self.provider = SimpleNamespace(supports_native_tools=provider_native)

    async def generate(self, prompt, model=None, *, tools=None, **kwargs):
        self.generate_calls.append({"prompt": prompt, "tools": tools, "kwargs": kwargs})
        if not self._results:
            raise AssertionError("ScriptedNativeLLM exhausted")
        result = self._results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    async def generate_response(self, prompt, system_prompt=None, **kwargs):
        self.generate_response_calls.append({"prompt": prompt})
        return "Final Answer: legacy path"


class LegacyOnlyLLM:
    """LLM double WITHOUT the structured ``generate`` API."""

    def __init__(self):
        self.generate_response_calls: list[dict] = []

    async def generate_response(self, prompt, system_prompt=None, **kwargs):
        self.generate_response_calls.append({"prompt": prompt})
        return "Final Answer: legacy path"


def tool_result(name, arguments, call_id="call-1", text=None):
    return LLMResult(
        text=text,
        tool_calls=[ToolCall(id=call_id, name=name, arguments=arguments)],
        stop_reason="tool_use",
    )


# ---------------------------------------------------------------------------
# Schema inference
# ---------------------------------------------------------------------------


def test_infer_parameters_from_signature():
    async def search(query: str, limit: int = 5) -> str:
        return query

    schema = infer_tool_parameters(ToolDefinition("search", search, "d"))
    assert schema["type"] == "object"
    assert schema["properties"]["query"] == {"type": "string"}
    assert schema["properties"]["limit"] == {"type": "integer"}
    assert schema["required"] == ["query"]


def test_infer_parameters_string_annotations_and_unknown_default_to_string():
    def fn(a: "str", b: "SomeCustomType", c=None):  # noqa: F821
        return a

    schema = infer_tool_parameters(ToolDefinition("t", fn, "d"))
    assert schema["properties"]["a"] == {"type": "string"}
    assert schema["properties"]["b"] == {"type": "string"}
    assert "c" not in schema.get("required", [])


def test_explicit_parameters_win_over_inference():
    explicit = {"type": "object", "properties": {"x": {"type": "number"}}}

    def fn(y: int):
        return y

    tool = ToolDefinition("t", fn, "d", parameters=explicit)
    assert infer_tool_parameters(tool) is explicit
    specs = build_tool_specs([tool])
    assert specs[0].parameters is explicit
    assert specs[0].name == "t"


def test_uninspectable_callable_gets_permissive_schema():
    schema = infer_tool_parameters(ToolDefinition("t", len, "d"))
    assert schema == {"type": "object"} or schema["type"] == "object"


# ---------------------------------------------------------------------------
# Mode resolution
# ---------------------------------------------------------------------------


def test_auto_mode_off_when_flag_disabled():
    llm = ScriptedNativeLLM([], native_enabled=False)
    agent = ReActAgent(llm_service=llm)
    assert resolve_native_mode(agent) is False


def test_auto_mode_on_when_flag_and_provider_support():
    llm = ScriptedNativeLLM([], native_enabled=True, provider_native=True)
    agent = ReActAgent(llm_service=llm)
    assert resolve_native_mode(agent) is True


def test_auto_mode_off_without_provider_support():
    llm = ScriptedNativeLLM([], native_enabled=True, provider_native=False)
    agent = ReActAgent(llm_service=llm)
    assert resolve_native_mode(agent) is False


def test_forced_false_wins_over_capable_service():
    llm = ScriptedNativeLLM([], native_enabled=True)
    agent = ReActAgent(llm_service=llm, native_tools=False)
    assert resolve_native_mode(agent) is False


def test_forced_true_without_generate_falls_back_to_text_loop():
    llm = LegacyOnlyLLM()
    agent = ReActAgent(llm_service=llm, native_tools=True)
    assert resolve_native_mode(agent) is False


def test_no_llm_service_resolves_false():
    agent = ReActAgent(llm_service=None, native_tools=True)
    agent._get_llm_service = lambda: None
    assert resolve_native_mode(agent) is False


# ---------------------------------------------------------------------------
# Native loop behavior
# ---------------------------------------------------------------------------


async def test_native_loop_tool_then_answer():
    captured = {}

    async def search(query: str) -> str:
        captured["query"] = query
        return "Tokyo population: 37M"

    llm = ScriptedNativeLLM(
        [
            tool_result("search", {"query": "tokyo"}, text="Need data."),
            LLMResult(text="The population of Tokyo is 37M."),
        ]
    )
    agent = ReActAgent(
        tools=[ToolDefinition("search", search, "Search", category="read_only")],
        llm_service=llm,
        native_tools=True,
    )
    result = await agent.run("Population of Tokyo?")

    assert result.final_answer == "The population of Tokyo is 37M."
    assert captured["query"] == "tokyo"  # kwargs dispatch, not comma parsing
    assert result.iterations_used == 2
    assert result.hit_limit is False
    kinds = [s.step_type for s in result.trace]
    assert StepType.THOUGHT in kinds
    assert StepType.ACTION in kinds
    assert StepType.OBSERVATION in kinds
    assert kinds[-1] is StepType.FINAL_ANSWER
    # Tool schema was sent to the model.
    assert llm.generate_calls[0]["tools"][0].name == "search"
    # Second turn saw the tool result in the transcript.
    assert "Tokyo population: 37M" in llm.generate_calls[1]["prompt"]
    assert llm.generate_response_calls == []


async def test_native_loop_multiple_calls_one_turn_execute_in_order():
    order = []

    async def a(x: str = "") -> str:
        order.append("a")
        return "ra"

    async def b(x: str = "") -> str:
        order.append("b")
        return "rb"

    turn = LLMResult(
        tool_calls=[
            ToolCall(id="1", name="a", arguments={}),
            ToolCall(id="2", name="b", arguments={}),
        ],
        stop_reason="tool_use",
    )
    llm = ScriptedNativeLLM([turn, LLMResult(text="done")])
    agent = ReActAgent(
        tools=[
            ToolDefinition("a", a, "A", category="read_only"),
            ToolDefinition("b", b, "B", category="read_only"),
        ],
        llm_service=llm,
        native_tools=True,
    )
    result = await agent.run("q")
    assert order == ["a", "b"]
    assert result.final_answer == "done"
    observations = [
        s.content for s in result.trace if s.step_type is StepType.OBSERVATION
    ]
    assert observations == ["ra", "rb"]


async def test_native_loop_unknown_tool_becomes_observation():
    llm = ScriptedNativeLLM([tool_result("missing", {}), LLMResult(text="ok")])
    agent = ReActAgent(tools=[], llm_service=llm, native_tools=True)
    result = await agent.run("q")
    obs = next(s for s in result.trace if s.step_type is StepType.OBSERVATION)
    assert "unknown tool 'missing'" in obs.content
    assert result.final_answer == "ok"


async def test_native_loop_hit_limit_returns_last_observation():
    async def t(x: str = "") -> str:
        return "obs"

    llm = ScriptedNativeLLM([tool_result("t", {}, call_id=str(i)) for i in range(3)])
    agent = ReActAgent(
        tools=[ToolDefinition("t", t, "T", category="read_only")],
        max_iterations=3,
        llm_service=llm,
        native_tools=True,
    )
    result = await agent.run("q")
    assert result.hit_limit is True
    assert result.iterations_used == 3
    assert result.final_answer == "obs"


async def test_native_loop_llm_error_degrades_gracefully():
    llm = ScriptedNativeLLM([RuntimeError("boom")])
    agent = ReActAgent(llm_service=llm, native_tools=True)
    result = await agent.run("q")
    assert "error occurred" in result.final_answer.lower()
    assert result.hit_limit is False


async def test_native_loop_sync_tool_and_transient_retry():
    attempts = {"n": 0}

    def flaky(x: str = "") -> str:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise ConnectionError("transient")
        return "recovered"

    llm = ScriptedNativeLLM([tool_result("flaky", {"x": "1"}), LLMResult(text="fin")])
    agent = ReActAgent(
        tools=[ToolDefinition("flaky", flaky, "F", category="read_only")],
        llm_service=llm,
        native_tools=True,
        tool_retries=1,
        retry_backoff=0.0,
    )
    result = await agent.run("q")
    assert attempts["n"] == 2
    obs = next(s for s in result.trace if s.step_type is StepType.OBSERVATION)
    assert obs.content == "recovered"
    assert result.final_answer == "fin"


async def test_auto_detection_disabled_uses_legacy_text_loop():
    llm = ScriptedNativeLLM([], native_enabled=False)
    agent = ReActAgent(llm_service=llm)  # native_tools=None -> auto
    result = await agent.run("q")
    assert result.final_answer == "legacy path"
    assert llm.generate_calls == []
    assert len(llm.generate_response_calls) == 1


async def test_skills_extra_lands_in_native_system_prompt():
    llm = ScriptedNativeLLM([LLMResult(text="ok")])
    agent = ReActAgent(
        llm_service=llm,
        native_tools=True,
        system_prompt_extra="## Skills catalog",
    )
    await agent.run("q")
    assert "## Skills catalog" in llm.generate_calls[0]["kwargs"]["system_prompt"]


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])


class TestParallelToolCalls:
    """A native multi-tool turn emits every call before seeing any result, so
    the calls are independent and used to pay the sum of their latencies."""

    @staticmethod
    def _agent(tools):
        return ReActAgent(tools=tools, llm_service=ScriptedNativeLLM([]))

    async def test_calls_in_one_turn_overlap(self):
        import asyncio
        import time

        async def slow(**kwargs):
            await asyncio.sleep(0.1)
            return "done"

        agent = self._agent(
            [
                ToolDefinition(
                    name=f"t{i}", description="d", fn=slow, category="read_only"
                )
                for i in range(3)
            ]
        )

        started = time.monotonic()
        observations = await agent._execute_tool_calls(
            [("t0", {}), ("t1", {}), ("t2", {})]
        )
        elapsed = time.monotonic() - started

        assert observations == ["done", "done", "done"]
        # Sequential would be >= 0.3s; concurrent is one sleep plus overhead.
        assert elapsed < 0.25

    async def test_observations_keep_emission_order(self):
        import asyncio

        async def slow(**kwargs):
            await asyncio.sleep(0.05)
            return "slow"

        async def fast(**kwargs):
            return "fast"

        agent = self._agent(
            [
                ToolDefinition(
                    name="slow", description="d", fn=slow, category="read_only"
                ),
                ToolDefinition(
                    name="fast", description="d", fn=fast, category="read_only"
                ),
            ]
        )

        observations = await agent._execute_tool_calls([("slow", {}), ("fast", {})])

        assert observations == ["slow", "fast"]

    async def test_unknown_tool_does_not_abort_the_turn(self):
        async def ok(**kwargs):
            return "ok"

        agent = self._agent(
            [ToolDefinition(name="ok", description="d", fn=ok, category="read_only")]
        )

        observations = await agent._execute_tool_calls([("nope", {}), ("ok", {})])

        assert "unknown tool" in observations[0]
        assert observations[1] == "ok"

    async def test_gates_run_before_any_tool_executes(self):
        """A fail-closed refusal (approval pending, budget exhausted) must abort
        the turn before a later tool in it has run."""
        from core.orchestration.autonomy import ApprovalPendingError

        executed: list[str] = []

        async def record(name):
            executed.append(name)
            return name

        agent = self._agent(
            [
                ToolDefinition(
                    name="first",
                    description="d",
                    fn=lambda **k: record("first"),
                    category="read_only",
                ),
                ToolDefinition(
                    name="second",
                    description="d",
                    fn=lambda **k: record("second"),
                    category="read_only",
                ),
            ]
        )

        async def _deny(tool):
            raise ApprovalPendingError("awaiting operator", "destructive", "run-1")

        agent._enforce_tool_gates = _deny  # type: ignore[method-assign]

        with pytest.raises(ApprovalPendingError):
            await agent._execute_tool_calls([("first", {}), ("second", {})])

        assert executed == []
