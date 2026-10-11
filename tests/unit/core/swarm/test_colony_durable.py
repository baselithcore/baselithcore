"""A durable colony batch neither re-auctions nor re-executes finished work.

The batch runs under a checkpoint; the process "dies" while one task is still
executing. A fresh colony — whose agents now rank differently, so a new auction
would pick other winners — resumes the batch on the reloaded checkpoint.
"""

from __future__ import annotations

import asyncio

import pytest

from baselith.core.config.swarm import SwarmConfig
from baselith.core.orchestration.checkpoint import Checkpoint, CheckpointManager
from baselith.core.orchestration.checkpoint_memory import InMemoryCheckpointStore
from baselith.core.orchestration.checkpoint_scope import bind_checkpoint
from baselith.core.swarm.colony import Colony
from baselith.core.swarm.types import AgentProfile, Capability, Task
from baselith.core.workflows.adapters import ColonyNodeAdapter

pytestmark = pytest.mark.unit


class _Death(BaseException):
    """The process dies: nothing in the batch swallows it."""


def _colony(a1: float, a2: float) -> Colony:
    colony = Colony(config=SwarmConfig(), loop_limits=None)
    for agent_id, proficiency in (("a1", a1), ("a2", a2)):
        colony.register_agent(
            AgentProfile(
                id=agent_id,
                name=agent_id,
                capabilities=[Capability(name="research", proficiency=proficiency)],
            )
        )
    return colony


def _tasks() -> list[Task]:
    # Fresh Task objects (fresh random ids) on every pass, like a real resume.
    return [
        Task(description="survey", required_capabilities=["research"]),
        Task(description="summarise", required_capabilities=["research"]),
    ]


async def _manager(store: InMemoryCheckpointStore) -> CheckpointManager:
    loaded = await store.load("colony-run")
    if loaded is None:
        loaded = Checkpoint(run_id="colony-run", query="q")
        await store.save(loaded)
    return CheckpointManager(store, loaded)


async def test_a_resumed_batch_keeps_its_winners_and_finished_results() -> None:
    store = InMemoryCheckpointStore()
    executed: list[tuple[str, str]] = []
    survey_done = asyncio.Event()

    async def first_pass(task: Task, agent: AgentProfile) -> str:
        executed.append((task.description, agent.id))
        if task.description == "survey":
            survey_done.set()
            return f"{task.description} by {agent.id}"
        await survey_done.wait()
        await asyncio.sleep(0)  # let the survey step be recorded first
        raise _Death("worker killed")

    colony = _colony(a1=0.9, a2=0.6)
    with bind_checkpoint(await _manager(store)), pytest.raises(BaseExceptionGroup):
        await colony.execute_batch(_tasks(), first_pass)
    assert executed == [("survey", "a1"), ("summarise", "a2")]

    # The restarted colony ranks a2 first: a fresh auction would swap winners.
    resumed = _colony(a1=0.4, a2=0.95)
    resolved: list[str] = []
    original_resolve = resumed.auction.resolve

    def _spy(task_id: str) -> str | None:
        resolved.append(task_id)
        return original_resolve(task_id)

    resumed.auction.resolve = _spy  # type: ignore[method-assign]

    async def second_pass(task: Task, agent: AgentProfile) -> str:
        executed.append((task.description, agent.id))
        return f"{task.description} by {agent.id}"

    tasks = _tasks()
    with bind_checkpoint(await _manager(store)):
        batch = await resumed.execute_batch(tasks, second_pass)

    assert resolved == []  # no auction ran again
    # Only the interrupted task executed, on the winner recorded for it.
    assert executed[2:] == [("summarise", "a2")]
    assert batch.completed == {
        tasks[0].id: "survey by a1",
        tasks[1].id: "summarise by a2",
    }
    assert [t.assigned_to for t in tasks] == ["a1", "a2"]

    stored = await store.load("colony-run")
    assert stored is not None
    auctions = [s for s in stored.steps.values() if s["category"] == "swarm_auction"]
    assert {a["result"]["winner"] for a in auctions} == {"a1", "a2"}
    assert all(a["result"]["bids"] for a in auctions)


async def test_a_failed_task_is_not_recorded_and_runs_again() -> None:
    store = InMemoryCheckpointStore()
    attempts: list[str] = []

    async def flaky(task: Task, agent: AgentProfile) -> str:
        attempts.append(task.description)
        if len(attempts) == 1:
            raise RuntimeError("transient")
        return "ok"

    task = [Task(description="only", required_capabilities=["research"])]
    with bind_checkpoint(await _manager(store)):
        first = await _colony(0.9, 0.6).execute_batch(task, flaky)
    assert list(first.failed.values()) == ["transient"]

    task = [Task(description="only", required_capabilities=["research"])]
    with bind_checkpoint(await _manager(store)):
        second = await _colony(0.9, 0.6).execute_batch(task, flaky)
    assert list(second.completed.values()) == ["ok"]
    assert attempts == ["only", "only"]


async def test_the_colony_node_adapter_replays_a_finished_task() -> None:
    store = InMemoryCheckpointStore()
    calls: list[str] = []

    async def work(task: Task, agent: AgentProfile) -> str:
        calls.append(agent.id)
        return f"done by {agent.id}"

    adapter = ColonyNodeAdapter(_colony(0.9, 0.6), work, ["research"])
    first = await adapter.run("prompt", checkpoint=await _manager(store))
    again = await ColonyNodeAdapter(_colony(0.1, 0.9), work, ["research"]).run(
        "prompt", checkpoint=await _manager(store)
    )
    assert first.output == again.output == "done by a1"
    assert calls == ["a1"]


async def test_without_a_checkpoint_the_batch_is_unchanged() -> None:
    async def work(task: Task, agent: AgentProfile) -> str:
        return agent.id

    tasks = _tasks()
    batch = await _colony(0.9, 0.6).execute_batch(tasks, work)
    assert sorted(batch.completed.values()) == ["a1", "a2"]
