"""ReAct tool steps must be durable: recorded via CheckpointManager.run_step
and replayed (not re-executed) on resume.

Before this suite the agent accepted a ``checkpoint`` but used it only for the
approval gate — a crash mid-run re-executed every completed tool side effect.
"""

from __future__ import annotations

import pytest

from core.orchestration.autonomy import AutonomyLevel, AutonomyPolicy
from core.orchestration.checkpoint import Checkpoint, CheckpointManager
from core.orchestration.checkpoint_memory import InMemoryCheckpointStore
from core.reasoning.react import ReActAgent, ToolDefinition


def _autonomous_policy() -> AutonomyPolicy:
    return AutonomyPolicy(level=AutonomyLevel.FULLY_AUTONOMOUS)


async def _manager(store: InMemoryCheckpointStore, run_id: str) -> CheckpointManager:
    checkpoint = Checkpoint(run_id=run_id, query="q")
    await store.save(checkpoint)
    return CheckpointManager(store, checkpoint)


def _agent(tool_fn, manager: CheckpointManager) -> ReActAgent:
    return ReActAgent(
        tools=[
            ToolDefinition(
                name="lookup",
                fn=tool_fn,
                description="test tool",
                category="read_only",
            )
        ],
        autonomy_policy=_autonomous_policy(),
        checkpoint=manager,
    )


@pytest.mark.asyncio
async def test_tool_step_is_recorded_in_checkpoint_store():
    store = InMemoryCheckpointStore()
    manager = await _manager(store, "run-1")
    calls = {"n": 0}

    async def tool_fn(x: str) -> str:
        calls["n"] += 1
        return f"result-{x}"

    agent = _agent(tool_fn, manager)
    observation = await agent._execute_tool_call("lookup", {"x": "a"})

    assert observation == "result-a"
    assert calls["n"] == 1
    stored = await store.load("run-1")
    assert stored is not None
    assert len(stored.steps) == 1
    recorded = next(iter(stored.steps.values()))
    assert recorded["tool_name"] == "lookup"
    assert recorded["result"] == "result-a"


@pytest.mark.asyncio
async def test_resume_replays_recorded_step_without_reexecuting():
    store = InMemoryCheckpointStore()
    manager = await _manager(store, "run-2")
    calls = {"n": 0}

    async def tool_fn(x: str) -> str:
        calls["n"] += 1
        return f"result-{x}"

    first = await _agent(tool_fn, manager)._execute_tool_call("lookup", {"x": "a"})
    assert calls["n"] == 1

    # Simulate a crash + resume: a fresh manager rebuilt from the store.
    loaded = await store.load("run-2")
    assert loaded is not None
    resumed_manager = CheckpointManager(store, loaded)
    replayed = await _agent(tool_fn, resumed_manager)._execute_tool_call(
        "lookup", {"x": "a"}
    )

    assert replayed == first
    assert calls["n"] == 1  # the side effect did NOT run again


@pytest.mark.asyncio
async def test_divergent_args_on_resume_execute_fresh():
    store = InMemoryCheckpointStore()
    manager = await _manager(store, "run-3")
    calls = {"n": 0}

    async def tool_fn(x: str) -> str:
        calls["n"] += 1
        return f"result-{x}"

    await _agent(tool_fn, manager)._execute_tool_call("lookup", {"x": "a"})

    loaded = await store.load("run-3")
    assert loaded is not None
    resumed_manager = CheckpointManager(store, loaded)
    fresh = await _agent(tool_fn, resumed_manager)._execute_tool_call(
        "lookup", {"x": "DIFFERENT"}
    )

    assert fresh == "result-DIFFERENT"
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_multi_tool_turn_records_deterministic_cursors_and_replays():
    store = InMemoryCheckpointStore()
    manager = await _manager(store, "run-4")
    calls = {"n": 0}

    async def tool_fn(x: str) -> str:
        calls["n"] += 1
        return f"result-{x}"

    agent = _agent(tool_fn, manager)
    observations = await agent._execute_tool_calls(
        [("lookup", {"x": "a"}), ("lookup", {"x": "b"})]
    )
    assert observations == ["result-a", "result-b"]
    assert calls["n"] == 2

    stored = await store.load("run-4")
    assert stored is not None
    cursors = sorted(key.split(":")[0] for key in stored.steps)
    assert cursors == ["0", "1"]

    loaded = await store.load("run-4")
    resumed = CheckpointManager(store, loaded)
    replayed = await _agent(tool_fn, resumed)._execute_tool_calls(
        [("lookup", {"x": "a"}), ("lookup", {"x": "b"})]
    )
    assert replayed == ["result-a", "result-b"]
    assert calls["n"] == 2  # nothing re-executed


@pytest.mark.asyncio
async def test_without_checkpoint_behavior_unchanged():
    calls = {"n": 0}

    async def tool_fn(x: str) -> str:
        calls["n"] += 1
        return f"result-{x}"

    agent = ReActAgent(
        tools=[
            ToolDefinition(
                name="lookup",
                fn=tool_fn,
                description="test tool",
                category="read_only",
            )
        ],
        autonomy_policy=_autonomous_policy(),
    )
    assert await agent._execute_tool_call("lookup", {"x": "a"}) == "result-a"
    assert await agent._execute_tool_call("lookup", {"x": "a"}) == "result-a"
    assert calls["n"] == 2
