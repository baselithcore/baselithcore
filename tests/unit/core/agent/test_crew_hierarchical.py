"""Unit tests for the hierarchical crew process (manager-led delegation)."""

from unittest.mock import AsyncMock

import pytest

from core.agent import Agent, Crew, Task
from core.services.llm.tool_calling import LLMResult


def _mock_service(results):
    svc = AsyncMock()
    svc.generate = AsyncMock(side_effect=list(results))
    return svc


def _agent(*texts):
    return Agent(llm_service=_mock_service([LLMResult(text=t) for t in texts]))


APPROVED_JSON = '{"reasoning": "solid work", "verdict": "APPROVED"}'
REVISE_JSON = (
    '{"reasoning": "missing data", "verdict": "REVISE",'
    ' "feedback": "add concrete numbers"}'
)


class TestConstruction:
    def test_hierarchical_requires_manager(self):
        with pytest.raises(ValueError, match="manager"):
            Crew(
                agents=[_agent("x")],
                tasks=[Task("t")],
                process="hierarchical",
            )

    def test_hierarchical_accepted_with_manager(self):
        crew = Crew(
            agents=[_agent("x")],
            tasks=[Task("t")],
            process="hierarchical",
            manager=_agent("brief", APPROVED_JSON),
        )
        assert crew.process == "hierarchical"


@pytest.mark.asyncio
class TestHierarchicalRun:
    async def test_approved_path_single_execution(self):
        worker_svc = _mock_service([LLMResult(text="draft")])
        worker = Agent(llm_service=worker_svc)
        manager_svc = _mock_service(
            [LLMResult(text="focus brief"), LLMResult(text=APPROVED_JSON)]
        )
        crew = Crew(
            agents=[worker],
            tasks=[Task("write the report")],
            process="hierarchical",
            manager=Agent(llm_service=manager_svc),
        )
        result = await crew.run()
        assert worker_svc.generate.await_count == 1
        assert manager_svc.generate.await_count == 2  # brief + review
        assert result.final == "draft"
        assert result.task_results[0].review == "approved"

    async def test_delegation_brief_reaches_worker_prompt(self):
        worker_svc = _mock_service([LLMResult(text="draft")])
        worker = Agent(llm_service=worker_svc)
        manager = _agent("focus on the Q3 numbers", APPROVED_JSON)
        crew = Crew(
            agents=[worker],
            tasks=[Task("write the report")],
            process="hierarchical",
            manager=manager,
        )
        await crew.run()
        worker_prompt = worker_svc.generate.await_args_list[0].args[0]
        assert "write the report" in worker_prompt
        assert "focus on the Q3 numbers" in worker_prompt

    async def test_revise_reruns_once_with_feedback_and_flags_revised(self):
        worker_svc = _mock_service(
            [LLMResult(text="draft-1"), LLMResult(text="draft-2")]
        )
        worker = Agent(llm_service=worker_svc)
        manager_svc = _mock_service(
            [LLMResult(text="brief"), LLMResult(text=REVISE_JSON)]
        )
        crew = Crew(
            agents=[worker],
            tasks=[Task("write the report")],
            process="hierarchical",
            manager=Agent(llm_service=manager_svc),
        )
        result = await crew.run()
        assert worker_svc.generate.await_count == 2
        second_prompt = worker_svc.generate.await_args_list[1].args[0]
        assert "add concrete numbers" in second_prompt
        # Bounded: exactly one review round — the second output is accepted
        # without a further manager call.
        assert manager_svc.generate.await_count == 2
        assert result.final == "draft-2"
        assert result.task_results[0].review == "revised"

    async def test_manager_llm_failure_fails_open_to_approved(self):
        worker_svc = _mock_service([LLMResult(text="draft")])
        worker = Agent(llm_service=worker_svc)
        manager_svc = AsyncMock()
        manager_svc.generate = AsyncMock(side_effect=RuntimeError("provider down"))
        crew = Crew(
            agents=[worker],
            tasks=[Task("write the report")],
            process="hierarchical",
            manager=Agent(llm_service=manager_svc),
        )
        result = await crew.run()
        assert worker_svc.generate.await_count == 1
        assert result.final == "draft"
        assert result.task_results[0].review == "approved"

    async def test_malformed_review_json_fails_open_to_approved(self):
        worker_svc = _mock_service([LLMResult(text="draft")])
        worker = Agent(llm_service=worker_svc)
        manager = _agent("brief", "definitely not JSON")
        crew = Crew(
            agents=[worker],
            tasks=[Task("write the report")],
            process="hierarchical",
            manager=manager,
        )
        result = await crew.run()
        assert worker_svc.generate.await_count == 1
        assert result.task_results[0].review == "approved"

    async def test_context_threads_between_hierarchical_tasks(self):
        worker_svc = _mock_service(
            [LLMResult(text="ALPHA-FACTS"), LLMResult(text="summary")]
        )
        worker = Agent(llm_service=worker_svc)
        manager = _agent("brief-1", APPROVED_JSON, "brief-2", APPROVED_JSON)
        crew = Crew(
            agents=[worker],
            tasks=[Task("research"), Task("write summary")],
            process="hierarchical",
            manager=manager,
        )
        result = await crew.run()
        second_prompt = worker_svc.generate.await_args_list[1].args[0]
        assert "ALPHA-FACTS" in second_prompt  # accepted output threads forward
        assert result.final == "summary"
