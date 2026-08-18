"""Unit tests for the declarative multi-agent Crew facade."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from core.agent import Agent, Crew, CrewResult, Task
from core.services.llm.tool_calling import LLMResult


def _mock_service(results):
    svc = AsyncMock()
    svc.generate = AsyncMock(side_effect=list(results))
    return svc


def _agent(*texts):
    return Agent(llm_service=_mock_service([LLMResult(text=t) for t in texts]))


class TestConstruction:
    def test_multi_agent_crew_requires_task_assignment(self):
        a, b = _agent("x"), _agent("y")
        with pytest.raises(ValueError, match="agent"):
            Crew(agents=[a, b], tasks=[Task("do something")])

    def test_unknown_process_rejected(self):
        a = _agent("x")
        with pytest.raises(ValueError, match="process"):
            Crew(agents=[a], tasks=[Task("t")], process="tournament")

    def test_empty_tasks_rejected(self):
        with pytest.raises(ValueError, match="task"):
            Crew(agents=[_agent("x")], tasks=[])


@pytest.mark.asyncio
class TestSequential:
    async def test_single_agent_auto_assignment_and_order(self):
        svc = _mock_service([LLMResult(text="first"), LLMResult(text="second")])
        solo = Agent(llm_service=svc)
        crew = Crew(agents=[solo], tasks=[Task("step one"), Task("step two")])
        result = await crew.run()
        assert isinstance(result, CrewResult)
        assert [r.text for r in result.task_results] == ["first", "second"]
        assert result.final == "second"

    async def test_prior_outputs_flow_into_later_prompts(self):
        svc = _mock_service([LLMResult(text="ALPHA"), LLMResult(text="beta")])
        solo = Agent(llm_service=svc)
        crew = Crew(agents=[solo], tasks=[Task("research"), Task("write summary")])
        await crew.run()
        second_prompt = svc.generate.await_args_list[1].args[0]
        assert "write summary" in second_prompt
        assert "ALPHA" in second_prompt  # context chaining

    async def test_inputs_templating_and_missing_keys_safe(self):
        svc = _mock_service([LLMResult(text="ok")])
        solo = Agent(llm_service=svc)
        crew = Crew(agents=[solo], tasks=[Task("analyze {topic} for {missing}")])
        await crew.run(inputs={"topic": "RAG"})
        prompt = svc.generate.await_args_list[0].args[0]
        assert "analyze RAG" in prompt
        assert "{missing}" in prompt  # left intact, no KeyError

    async def test_expected_output_appended(self):
        svc = _mock_service([LLMResult(text="ok")])
        solo = Agent(llm_service=svc)
        crew = Crew(
            agents=[solo],
            tasks=[Task("summarize", expected_output="three bullet points")],
        )
        await crew.run()
        prompt = svc.generate.await_args_list[0].args[0]
        assert "three bullet points" in prompt

    async def test_explicit_agent_assignment(self):
        researcher = _agent("research-out")
        writer = _agent("write-out")
        crew = Crew(
            agents=[researcher, writer],
            tasks=[
                Task("research", agent=researcher),
                Task("write", agent=writer),
            ],
        )
        result = await crew.run()
        assert [r.text for r in result.task_results] == ["research-out", "write-out"]
        assert result.task_results[0].agent_index == 0
        assert result.task_results[1].agent_index == 1


@pytest.mark.asyncio
class TestParallel:
    async def test_parallel_runs_concurrently_without_context(self):
        started = []
        release = asyncio.Event()

        def _service(tag, text):
            svc = AsyncMock()

            async def _generate(prompt, **kwargs):
                started.append(tag)
                await release.wait()
                return LLMResult(text=text)

            svc.generate = AsyncMock(side_effect=_generate)
            return svc

        a = Agent(llm_service=_service("a", "out-a"))
        b = Agent(llm_service=_service("b", "out-b"))
        crew = Crew(
            agents=[a, b],
            tasks=[Task("t1", agent=a), Task("t2", agent=b)],
            process="parallel",
        )
        run = asyncio.create_task(crew.run())
        # Both generates must start before either finishes → concurrent.
        for _ in range(100):
            if len(started) == 2:
                break
            await asyncio.sleep(0.01)
        assert sorted(started) == ["a", "b"]
        release.set()
        result = await run
        assert [r.text for r in result.task_results] == ["out-a", "out-b"]
