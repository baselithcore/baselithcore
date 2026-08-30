"""Durable workflow execution + the orchestrator bridge.

The graph executor had no persistence (a crash mid-workflow re-ran every
node) and no consumer wiring it into the Orchestrator. These tests pin:

* node executions recorded through ``CheckpointManager.run_step`` and
  replayed (not re-executed) on resume;
* ``WorkflowFlowHandler`` exposing a ``WorkflowDefinition`` as a standard
  ``FlowHandler`` so a graph can be registered for an intent.
"""

from __future__ import annotations

import pytest

from core.orchestration.checkpoint import Checkpoint, CheckpointManager
from core.orchestration.checkpoint_memory import InMemoryCheckpointStore
from core.workflows.builder import WorkflowBuilder
from core.workflows.executor import ExecutionStatus, WorkflowExecutor
from core.workflows.flow_handler import WorkflowFlowHandler


def _workflow():
    return (
        WorkflowBuilder("durable-test")
        .start()
        .tool("uppercase", tool_id="upper")
        .tool("exclaim", tool_id="exclaim")
        .end()
        .build()
    )


def _executor(calls: dict[str, int]) -> WorkflowExecutor:
    async def upper(value):
        calls["upper"] = calls.get("upper", 0) + 1
        return str(value).upper()

    async def exclaim(value):
        calls["exclaim"] = calls.get("exclaim", 0) + 1
        return f"{value}!"

    return WorkflowExecutor(tools={"upper": upper, "exclaim": exclaim})


async def _manager(store: InMemoryCheckpointStore, run_id: str) -> CheckpointManager:
    checkpoint = Checkpoint(run_id=run_id, query="q")
    await store.save(checkpoint)
    return CheckpointManager(store, checkpoint)


@pytest.mark.asyncio
async def test_node_executions_recorded_in_checkpoint():
    store = InMemoryCheckpointStore()
    manager = await _manager(store, "wf-run-1")
    calls: dict[str, int] = {}

    result = await _executor(calls).execute(
        _workflow(), initial_input="hello", checkpoint=manager
    )

    assert result.status == ExecutionStatus.COMPLETED
    assert result.output == "HELLO!"
    stored = await store.load("wf-run-1")
    assert stored is not None
    assert len(stored.steps) >= 2  # both TOOL nodes recorded


@pytest.mark.asyncio
async def test_resume_replays_nodes_without_reexecuting():
    store = InMemoryCheckpointStore()
    manager = await _manager(store, "wf-run-2")
    calls: dict[str, int] = {}
    workflow = _workflow()

    first = await _executor(calls).execute(
        workflow, initial_input="hello", checkpoint=manager
    )
    assert first.output == "HELLO!"
    assert calls == {"upper": 1, "exclaim": 1}

    # Crash + resume: fresh manager rebuilt from the store, same workflow.
    loaded = await store.load("wf-run-2")
    assert loaded is not None
    resumed = CheckpointManager(store, loaded)
    second = await _executor(calls).execute(
        workflow, initial_input="hello", checkpoint=resumed
    )

    assert second.output == "HELLO!"
    assert calls == {"upper": 1, "exclaim": 1}  # nothing re-executed


@pytest.mark.asyncio
async def test_without_checkpoint_behavior_unchanged():
    calls: dict[str, int] = {}
    workflow = _workflow()
    executor = _executor(calls)

    first = await executor.execute(workflow, initial_input="hello")
    second = await executor.execute(workflow, initial_input="hello")

    assert first.output == second.output == "HELLO!"
    assert calls == {"upper": 2, "exclaim": 2}


@pytest.mark.asyncio
async def test_flow_handler_bridges_workflow_to_orchestrator_contract():
    calls: dict[str, int] = {}
    handler = WorkflowFlowHandler(_workflow(), executor=_executor(calls))

    result = await handler.handle("hello", {})

    assert result["response"] == "HELLO!"
    assert result.get("error") is not True
    assert result["metadata"]["workflow"] == "durable-test"


@pytest.mark.asyncio
async def test_flow_handler_uses_context_checkpoint():
    store = InMemoryCheckpointStore()
    manager = await _manager(store, "wf-run-3")
    calls: dict[str, int] = {}
    handler = WorkflowFlowHandler(_workflow(), executor=_executor(calls))

    await handler.handle("hello", {"checkpoint": manager})

    stored = await store.load("wf-run-3")
    assert stored is not None
    assert len(stored.steps) >= 2


@pytest.mark.asyncio
async def test_flow_handler_reports_failure_as_error_result():
    async def broken(value):
        raise RuntimeError("tool exploded")

    workflow = (
        WorkflowBuilder("broken").start().tool("boom", tool_id="boom").end().build()
    )
    handler = WorkflowFlowHandler(
        workflow, executor=WorkflowExecutor(tools={"boom": broken})
    )

    result = await handler.handle("hello", {})

    assert result["error"] is True
    assert "tool exploded" in result["response"]
