"""Crews, hierarchical crews and group chats resume without redoing finished work.

Every test crashes a run part-way (the model raises on a chosen call), rebuilds
the checkpoint manager from the store — what a restarted process does — runs
the same crew again, and counts the model and tool invocations of the second
pass: completed tasks, manager turns and chat rounds must replay, not re-run.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel

from baselith.core.agent import Agent, Crew, Task
from baselith.core.agent.group_chat import ChatMessage, GroupChat, RoundRobinSelector
from baselith.core.orchestration.checkpoint import Checkpoint, CheckpointManager
from baselith.core.orchestration.checkpoint_memory import InMemoryCheckpointStore
from baselith.core.orchestration.checkpoint_scope import bind_checkpoint
from baselith.core.reasoning.react import ToolDefinition
from baselith.core.services.llm.tool_calling import LLMResult, ToolCall
from baselith.core.workflows.adapters import CrewNodeAdapter
from baselith.core.workflows.builder import WorkflowBuilder
from baselith.core.workflows.executor import ExecutionStatus, WorkflowExecutor


class _Crash(RuntimeError):
    """The simulated failure that ends a pass."""


class _Death(BaseException):
    """A process death mid-call: no ``except Exception`` fail-open catches it."""


def _service(*items: Any) -> AsyncMock:
    """A model that answers ``items`` in order; an exception item is raised."""
    svc = AsyncMock()
    svc.generate = AsyncMock(side_effect=list(items))
    return svc


def _text(value: str) -> LLMResult:
    return LLMResult(text=value)


def _tool_turn(name: str, args: dict[str, Any]) -> LLMResult:
    return LLMResult(text="", tool_calls=[ToolCall(id="t", name=name, arguments=args)])


async def _fresh(store: InMemoryCheckpointStore, run_id: str) -> CheckpointManager:
    checkpoint = Checkpoint(run_id=run_id, query="q")
    await store.save(checkpoint)
    return CheckpointManager(store, checkpoint)


async def _reloaded(store: InMemoryCheckpointStore, run_id: str) -> CheckpointManager:
    loaded = await store.load(run_id)
    assert loaded is not None
    return CheckpointManager(store, loaded)


def _crew_steps(checkpoint: Checkpoint) -> int:
    """Recorded crew calls; each agent call also records its model turns."""
    return sum(1 for step in checkpoint.steps.values() if step["category"] == "crew")


def _three_task_crew(agent: Agent, process: str = "sequential") -> Crew:
    return Crew(
        agents=[agent],
        tasks=[Task("research {topic}"), Task("draft"), Task("polish")],
        process=process,
    )


@pytest.mark.asyncio
async def test_sequential_crew_resume_skips_completed_tasks() -> None:
    store = InMemoryCheckpointStore()
    first = _service(_text("A"), _text("B"), _Crash("boom"))
    with pytest.raises(_Crash):
        await _three_task_crew(Agent(llm_service=first)).run(
            {"topic": "x"}, checkpoint=await _fresh(store, "crew-1")
        )
    assert first.generate.await_count == 3

    second = _service(_text("C"))
    result = await _three_task_crew(Agent(llm_service=second)).run(
        {"topic": "x"}, checkpoint=await _reloaded(store, "crew-1")
    )

    assert second.generate.await_count == 1  # only the interrupted task ran
    assert [r.text for r in result.task_results] == ["A", "B", "C"]
    # The replayed outputs still thread into the next prompt.
    assert "B" in second.generate.await_args_list[0].args[0]


@pytest.mark.asyncio
async def test_parallel_crew_records_concurrently_and_resumes() -> None:
    store = InMemoryCheckpointStore()
    mgr = await _fresh(store, "crew-par")
    texts = iter(["A", "B"])

    async def flaky(*_a: Any, **_k: Any) -> LLMResult:
        try:
            return _text(next(texts))
        except StopIteration:
            raise _Crash("boom") from None

    first = AsyncMock()
    first.generate = AsyncMock(side_effect=flaky)
    with pytest.raises(_Crash):
        await _three_task_crew(Agent(llm_service=first), "parallel").run(
            {"topic": "x"}, checkpoint=mgr
        )
    stored = await store.load("crew-par")
    assert stored is not None and _crew_steps(stored) == 2  # no CAS conflict

    second = _service(_text("Z"))
    result = await _three_task_crew(Agent(llm_service=second), "parallel").run(
        {"topic": "x"}, checkpoint=await _reloaded(store, "crew-par")
    )
    assert second.generate.await_count == 1
    assert sorted(r.text for r in result.task_results) == ["A", "B", "Z"]


@pytest.mark.asyncio
async def test_interrupted_task_replays_its_tool_calls_and_scopes_do_not_collide() -> (
    None
):
    ran: list[str] = []

    def lookup(q: str) -> str:
        ran.append(q)
        return f"found {q}"

    tool = ToolDefinition(
        name="lookup", fn=lookup, description="d", category="mutating"
    )
    store = InMemoryCheckpointStore()

    # Pass 1: both tasks call lookup(q=1); task two crashes after its call.
    first = _service(
        _tool_turn("lookup", {"q": "1"}),
        _text("A"),
        _tool_turn("lookup", {"q": "1"}),
        _Crash("boom"),
    )
    agent = Agent(llm_service=first, tools=[tool])
    with pytest.raises(_Crash):
        await Crew(agents=[agent], tasks=[Task("one"), Task("two")]).run(
            checkpoint=await _fresh(store, "crew-tools")
        )
    assert ran == ["1", "1"]  # same call, two tasks: both really ran

    # Pass 2: task one replays whole; task two's model turn and its call
    # replay from ITS scope, and only the interrupted turn reaches the model.
    second = _service(_text("B"))
    agent = Agent(llm_service=second, tools=[tool])
    result = await Crew(agents=[agent], tasks=[Task("one"), Task("two")]).run(
        checkpoint=await _reloaded(store, "crew-tools")
    )
    assert ran == ["1", "1"]
    assert result.final == "B"
    assert second.generate.await_count == 1


@pytest.mark.asyncio
async def test_hierarchical_resume_after_review_crash_reruns_nothing_recorded() -> None:
    store = InMemoryCheckpointStore()
    approve = '{"reasoning": "fine", "verdict": "APPROVED"}'
    manager_first = _service(_text("brief"), _Death("killed"))
    worker_first = _service(_text("draft"))
    with pytest.raises(_Death):
        await Crew(
            agents=[Agent(llm_service=worker_first)],
            tasks=[Task("write")],
            process="hierarchical",
            manager=Agent(llm_service=manager_first),
        ).run(checkpoint=await _fresh(store, "crew-h"))

    manager_second = _service(_text(approve))
    worker_second = _service()
    result = await Crew(
        agents=[Agent(llm_service=worker_second)],
        tasks=[Task("write")],
        process="hierarchical",
        manager=Agent(llm_service=manager_second),
    ).run(checkpoint=await _reloaded(store, "crew-h"))

    assert worker_second.generate.await_count == 0  # worker output replayed
    assert manager_second.generate.await_count == 1  # brief replayed, review ran
    assert result.task_results[0].text == "draft"
    assert result.task_results[0].review == "approved"


class _Verdict(BaseModel):
    score: int


@pytest.mark.asyncio
async def test_structured_output_replays_as_model_instance() -> None:
    store = InMemoryCheckpointStore()
    agent = Agent(llm_service=_service(_text('{"score": 7}')), output_type=_Verdict)
    crew = Crew(agents=[agent], tasks=[Task("rate")])
    await crew.run(checkpoint=await _fresh(store, "crew-typed"))

    replay_agent = Agent(llm_service=_service(), output_type=_Verdict)
    result = await Crew(agents=[replay_agent], tasks=[Task("rate")]).run(
        checkpoint=await _reloaded(store, "crew-typed")
    )
    assert result.final == _Verdict(score=7)


@pytest.mark.asyncio
async def test_ambient_checkpoint_is_used_when_none_is_passed() -> None:
    store = InMemoryCheckpointStore()
    mgr = await _fresh(store, "crew-ambient")
    with bind_checkpoint(mgr):
        await _three_task_crew(
            Agent(llm_service=_service(_text("A"), _text("B"), _text("C")))
        ).run({"topic": "x"})
    stored = await store.load("crew-ambient")
    assert stored is not None and _crew_steps(stored) == 3


@pytest.mark.asyncio
async def test_without_a_checkpoint_nothing_is_recorded_or_replayed() -> None:
    svc = _service(_text("A"), _text("B"), _text("C"))
    result = await _three_task_crew(Agent(llm_service=svc)).run({"topic": "x"})
    assert [r.text for r in result.task_results] == ["A", "B", "C"]


class _Speaker:
    def __init__(self, name: str, replies: list[Any]) -> None:
        self.name = name
        self.capabilities: list[str] = []
        self.calls = 0
        self._replies = replies

    async def respond(self, topic: str, transcript: list[ChatMessage]) -> str:
        reply = self._replies[self.calls]
        self.calls += 1
        if isinstance(reply, Exception):
            raise reply
        return str(reply)


@pytest.mark.asyncio
async def test_group_chat_resume_replays_recorded_rounds() -> None:
    store = InMemoryCheckpointStore()
    alice, bob = _Speaker("alice", ["a1", _Crash("x")]), _Speaker("bob", ["b1"])
    chat = GroupChat([alice, bob], RoundRobinSelector(), max_rounds=4, budget=None)
    with pytest.raises(_Crash):
        await chat.run("topic", checkpoint=await _fresh(store, "chat-1"))
    assert (alice.calls, bob.calls) == (2, 1)

    alice2, bob2 = _Speaker("alice", ["a2"]), _Speaker("bob", ["b2"])
    chat = GroupChat([alice2, bob2], RoundRobinSelector(), max_rounds=4, budget=None)
    result = await chat.run("topic", checkpoint=await _reloaded(store, "chat-1"))

    assert (alice2.calls, bob2.calls) == (1, 1)  # rounds 1-2 replayed
    assert [m.content for m in result.transcript] == ["a1", "b1", "a2", "b2"]


@pytest.mark.asyncio
async def test_crew_workflow_node_resumes_mid_crew() -> None:
    store = InMemoryCheckpointStore()
    workflow = (
        WorkflowBuilder("crew-node")
        .start()
        .agent("team", agent_id="crew")
        .end()
        .build()
    )

    def executor(svc: AsyncMock) -> WorkflowExecutor:
        crew = Crew(
            agents=[Agent(llm_service=svc)],
            tasks=[Task("research {input}"), Task("summarise")],
        )
        return WorkflowExecutor(agents={"crew": CrewNodeAdapter(crew)})

    first = _service(_text("facts"), _Crash("boom"))
    failed = await executor(first).execute(
        workflow, initial_input="rag", checkpoint=await _fresh(store, "wf-crew")
    )
    assert failed.status == ExecutionStatus.FAILED

    second = _service(_text("summary"))
    done = await executor(second).execute(
        workflow, initial_input="rag", checkpoint=await _reloaded(store, "wf-crew")
    )
    assert done.status == ExecutionStatus.COMPLETED
    assert done.output == "summary"
    assert second.generate.await_count == 1  # the research task replayed
