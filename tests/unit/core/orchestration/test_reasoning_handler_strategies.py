"""ReasoningHandler now gives ReActAgent and ParallelToolExecutor live
call-sites via selectable strategies (resolves the dead-code decision)."""

from unittest.mock import AsyncMock

import pytest

from core.orchestration.handlers.reasoning import ReasoningHandler
from core.orchestration.parallel import ToolCall
from core.reasoning.react import ToolDefinition

pytestmark = [pytest.mark.contract]


@pytest.mark.asyncio
class TestReActStrategy:
    async def test_react_strategy_runs_react_agent(self):
        handler = ReasoningHandler()
        # ReActAgent calls llm.generate_response; return a final answer directly.
        handler._llm_service = AsyncMock()
        handler._llm_service.generate_response = AsyncMock(
            return_value="Thought: I know this.\nFinal Answer: 42"
        )

        async def search(q: str) -> str:
            return f"results for {q}"

        result = await handler.handle(
            "what is 6*7?",
            {
                "strategy": "react",
                "react_tools": [
                    ToolDefinition(name="search", fn=search, description="search")
                ],
            },
        )
        assert result["metadata"]["strategy"] == "react"
        assert result["response"] == "42"
        assert "hit_limit" in result["metadata"]

    async def test_react_without_tools_falls_back_to_tot(self):
        handler = ReasoningHandler()
        handler._tot_engine = AsyncMock()
        handler._tot_engine.solve = AsyncMock(
            return_value={"solution": "tot-answer", "steps": []}
        )
        # strategy=react but no react_tools → default ToT path.
        result = await handler.handle("q", {"strategy": "react"})
        assert result["response"] == "tot-answer"
        assert result["metadata"]["strategy"] == "react"  # echoes requested


@pytest.mark.asyncio
class TestParallelToolsStrategy:
    async def test_parallel_tools_executes_concurrently(self):
        handler = ReasoningHandler()

        async def add(a, b):
            return a + b

        async def mul(a, b):
            return a * b

        calls = [
            ToolCall(id="c1", tool_name="add", parameters={"a": 1, "b": 2}),
            ToolCall(id="c2", tool_name="mul", parameters={"a": 3, "b": 4}),
        ]
        result = await handler.handle(
            "compute",
            {
                "strategy": "parallel_tools",
                "tool_calls": calls,
                "tool_registry": {"add": add, "mul": mul},
            },
        )
        assert result["metadata"]["strategy"] == "parallel_tools"
        assert result["metadata"]["tool_count"] == 2
        assert result["metadata"]["success"] is True
        assert result["response"]["c1"] == 3
        assert result["response"]["c2"] == 12

    async def test_parallel_tools_reports_failure(self):
        handler = ReasoningHandler()

        async def boom(**_):
            raise RuntimeError("nope")

        result = await handler.handle(
            "x",
            {
                "strategy": "parallel_tools",
                "tool_calls": [ToolCall(id="c1", tool_name="boom", parameters={})],
                "tool_registry": {"boom": boom},
            },
        )
        assert result["metadata"]["success"] is False
        assert "ERROR" in result["response"]["c1"]

    async def test_missing_tool_calls_falls_back_to_tot(self):
        handler = ReasoningHandler()
        handler._tot_engine = AsyncMock()
        handler._tot_engine.solve = AsyncMock(
            return_value={"solution": "tot", "steps": []}
        )
        result = await handler.handle("q", {"strategy": "parallel_tools"})
        assert result["response"] == "tot"


def _skill_service(tmp_path):
    """A SkillService over one plugin shipping a single 'review' skill."""
    from core.plugins.skills_service import SkillService

    root = tmp_path / "alpha" / "skills" / "review"
    root.mkdir(parents=True)
    (root / "SKILL.md").write_text(
        "---\nname: review\ndescription: Review code changes.\n---\n"
        "Review checklist body.\n",
        encoding="utf-8",
    )

    class _Registry:
        def get_all_skill_roots(self):
            return {"alpha": tmp_path / "alpha" / "skills"}

    return SkillService(_Registry())


@pytest.mark.asyncio
class TestSkillWiring:
    async def test_react_gets_activate_skill_tool_and_catalog(self, tmp_path):
        handler = ReasoningHandler()
        handler._llm_service = AsyncMock()
        # Turn 1: the model activates the skill; turn 2: final answer.
        handler._llm_service.generate_response = AsyncMock(
            side_effect=[
                "Thought: need the skill.\nAction: activate_skill(review)",
                "Thought: done.\nFinal Answer: reviewed",
            ]
        )

        async def search(q: str) -> str:
            return "hit"

        result = await handler.handle(
            "review this",
            {
                "strategy": "react",
                "react_tools": [
                    ToolDefinition(name="search", fn=search, description="search")
                ],
                "skill_service": _skill_service(tmp_path),
            },
        )
        assert result["response"] == "reviewed"
        # The observation of the activation carries the skill body.
        assert any("Review checklist body" in step for step in result["steps"])
        # The catalog reached the system prompt on every LLM turn.
        system_prompt = handler._llm_service.generate_response.call_args_list[0][1][
            "system_prompt"
        ]
        assert "Available skills" in system_prompt
        assert "review" in system_prompt

    async def test_parallel_tools_registers_activate_skill(self, tmp_path):
        handler = ReasoningHandler()
        result = await handler.handle(
            "activate",
            {
                "strategy": "parallel_tools",
                "tool_calls": [
                    ToolCall(
                        id="c1",
                        tool_name="activate_skill",
                        parameters={"name": "review"},
                    )
                ],
                "tool_registry": {},
                "skill_service": _skill_service(tmp_path),
            },
        )
        assert result["metadata"]["success"] is True
        assert "Review checklist body" in result["response"]["c1"]

    async def test_react_without_skill_service_unchanged(self):
        handler = ReasoningHandler()
        handler._llm_service = AsyncMock()
        handler._llm_service.generate_response = AsyncMock(
            return_value="Final Answer: plain"
        )

        async def noop() -> str:
            return ""

        result = await handler.handle(
            "q",
            {
                "strategy": "react",
                "react_tools": [
                    ToolDefinition(name="noop", fn=noop, description="noop")
                ],
            },
        )
        assert result["response"] == "plain"
        system_prompt = handler._llm_service.generate_response.call_args[1][
            "system_prompt"
        ]
        assert "Available skills" not in system_prompt
