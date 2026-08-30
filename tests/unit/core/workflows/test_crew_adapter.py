"""Crew-in-graph adapter: a Crew as an AGENT workflow node.

``handle_agent`` expects ``async run(prompt)`` returning an object with
``.output``; ``Crew.run`` takes an ``inputs`` dict and returns a CrewResult.
The adapter bridges the two so multi-agent crews compose into graphs (and
inherit durable execution) without new executor machinery.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from core.workflows.adapters import CrewNodeAdapter
from core.workflows.builder import WorkflowBuilder
from core.workflows.executor import ExecutionStatus, WorkflowExecutor


class _FakeCrew:
    """Duck-typed Crew: records inputs, returns an object with ``.final``."""

    def __init__(self, final: str) -> None:
        self._final = final
        self.seen_inputs: list[dict[str, Any]] = []

    async def run(self, inputs: dict[str, Any] | None = None):
        self.seen_inputs.append(dict(inputs or {}))

        class _Result:
            final = self._final

        return _Result()


@pytest.mark.asyncio
async def test_adapter_maps_prompt_to_inputs_and_final_to_output():
    crew = _FakeCrew(final="the deliverable")
    adapter = CrewNodeAdapter(crew)

    result = await adapter.run("analyze the market")

    assert crew.seen_inputs == [{"input": "analyze the market"}]
    assert result.output == "the deliverable"


@pytest.mark.asyncio
async def test_adapter_custom_input_key():
    crew = _FakeCrew(final="ok")
    adapter = CrewNodeAdapter(crew, input_key="topic")

    await adapter.run("vector databases")

    assert crew.seen_inputs == [{"topic": "vector databases"}]


class _FakeColony:
    """Duck-typed Colony: records batches, returns a canned BatchResult."""

    def __init__(self, completed=None, failed=None, unassigned=None) -> None:
        self._completed = completed
        self._failed = failed or {}
        self._unassigned = unassigned or []
        self.batches: list[list[Any]] = []

    async def execute_batch(self, tasks, execute_fn):
        self.batches.append(list(tasks))
        task_id = tasks[0].id
        result = SimpleNamespace(completed={}, failed={}, unassigned=[])
        if self._completed is not None:
            result.completed = {task_id: self._completed}
        if self._failed:
            result.failed = {task_id: next(iter(self._failed.values()))}
        if self._unassigned:
            result.unassigned = [task_id]
        return result


@pytest.mark.asyncio
async def test_colony_adapter_runs_prompt_as_swarm_task():
    from core.workflows.adapters import ColonyNodeAdapter

    async def execute(task, agent):  # pragma: no cover - not invoked by fake
        return "unused"

    colony = _FakeColony(completed="SWARM OUTPUT")
    adapter = ColonyNodeAdapter(colony, execute, required_capabilities=["research"])

    result = await adapter.run("map the competitive landscape")

    assert result.output == "SWARM OUTPUT"
    (task,) = colony.batches[0]
    assert task.description == "map the competitive landscape"
    assert task.required_capabilities == ["research"]


@pytest.mark.asyncio
async def test_colony_adapter_raises_on_failed_or_unassigned():
    from core.workflows.adapters import ColonyNodeAdapter

    async def execute(task, agent):  # pragma: no cover
        return "unused"

    failing = ColonyNodeAdapter(_FakeColony(failed={"t": "agent exploded"}), execute)
    with pytest.raises(RuntimeError, match="agent exploded"):
        await failing.run("anything")

    unassigned = ColonyNodeAdapter(_FakeColony(unassigned=["t"]), execute)
    with pytest.raises(RuntimeError, match="unassigned"):
        await unassigned.run("anything")


@pytest.mark.asyncio
async def test_crew_runs_as_agent_node_in_a_graph():
    crew = _FakeCrew(final="CREW OUTPUT")
    workflow = (
        WorkflowBuilder("crew-graph")
        .start()
        .agent("analysis", agent_id="crew")
        .end()
        .build()
    )
    executor = WorkflowExecutor(agents={"crew": CrewNodeAdapter(crew)})

    result = await executor.execute(workflow, initial_input="the question")

    assert result.status == ExecutionStatus.COMPLETED
    assert result.output == "CREW OUTPUT"
    assert crew.seen_inputs == [{"input": "the question"}]
