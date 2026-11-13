"""HUMAN workflow nodes: durable approval gates in graphs.

Before this suite a HUMAN node had no handler — the executor logged a warning
and passed the last output through, silently waving traffic past an explicit
human gate. The contract now: with a durable checkpoint the node pauses the
run (``awaiting_approval`` + ``ApprovalPendingError``), a recorded approval
lets the resumed run continue past the gate, a denial fails the workflow, and
without a checkpoint the gate fails closed.
"""

from __future__ import annotations

import pytest

from baselith.core.orchestration.autonomy import ApprovalPendingError
from baselith.core.orchestration.checkpoint import (
    Checkpoint,
    CheckpointManager,
    record_approval_decision,
)
from baselith.core.orchestration.checkpoint_memory import InMemoryCheckpointStore
from baselith.core.workflows.builder import WorkflowBuilder
from baselith.core.workflows.executor import ExecutionStatus, WorkflowExecutor


def _gated_workflow():
    return (
        WorkflowBuilder("gated")
        .start()
        .tool("prepare", tool_id="prepare")
        .human("gate")
        .tool("commit", tool_id="commit")
        .end()
        .build()
    )


def _executor(calls: dict[str, int]) -> WorkflowExecutor:
    async def prepare(value):
        calls["prepare"] = calls.get("prepare", 0) + 1
        return f"prepared:{value}"

    async def commit(value):
        calls["commit"] = calls.get("commit", 0) + 1
        return f"committed:{value}"

    return WorkflowExecutor(tools={"prepare": prepare, "commit": commit})


async def _manager(store: InMemoryCheckpointStore, run_id: str) -> CheckpointManager:
    checkpoint = Checkpoint(run_id=run_id, query="q")
    await store.save(checkpoint)
    return CheckpointManager(store, checkpoint)


@pytest.mark.asyncio
async def test_human_node_pauses_durably():
    store = InMemoryCheckpointStore()
    manager = await _manager(store, "hg-1")
    calls: dict[str, int] = {}

    with pytest.raises(ApprovalPendingError) as exc_info:
        await _executor(calls).execute(
            _gated_workflow(), initial_input="x", checkpoint=manager
        )

    assert exc_info.value.run_id == "hg-1"
    assert calls == {"prepare": 1}  # the gated side of the graph never ran
    stored = await store.load("hg-1")
    assert stored is not None
    assert stored.status == "awaiting_approval"
    assert stored.pending_approval is not None


@pytest.mark.asyncio
async def test_approved_resume_continues_past_gate():
    store = InMemoryCheckpointStore()
    manager = await _manager(store, "hg-2")
    calls: dict[str, int] = {}
    workflow = _gated_workflow()
    executor = _executor(calls)

    with pytest.raises(ApprovalPendingError):
        await executor.execute(workflow, initial_input="x", checkpoint=manager)

    gate_id = (await store.load("hg-2")).pending_approval["tool_name"]
    assert await record_approval_decision(store, "hg-2", True, approver="op")

    resumed = CheckpointManager(store, await store.load("hg-2"))
    result = await executor.execute(workflow, initial_input="x", checkpoint=resumed)

    assert result.status == ExecutionStatus.COMPLETED
    assert result.output == "committed:prepared:x"
    assert calls == {"prepare": 1, "commit": 1}  # prepare replayed, not re-run
    assert gate_id  # sanity: the pause referenced the gate node


@pytest.mark.asyncio
async def test_denied_resume_fails_the_workflow():
    store = InMemoryCheckpointStore()
    manager = await _manager(store, "hg-3")
    calls: dict[str, int] = {}
    workflow = _gated_workflow()
    executor = _executor(calls)

    with pytest.raises(ApprovalPendingError):
        await executor.execute(workflow, initial_input="x", checkpoint=manager)
    assert await record_approval_decision(
        store, "hg-3", False, approver="op", reason="nope"
    )

    resumed = CheckpointManager(store, await store.load("hg-3"))
    result = await executor.execute(workflow, initial_input="x", checkpoint=resumed)

    assert result.status == ExecutionStatus.FAILED
    assert "denied" in (result.error or "").lower()
    assert "commit" not in calls  # the gated side never ran


@pytest.mark.asyncio
async def test_human_node_without_checkpoint_fails_closed():
    calls: dict[str, int] = {}
    result = await _executor(calls).execute(_gated_workflow(), initial_input="x")

    assert result.status == ExecutionStatus.FAILED
    assert "checkpoint" in (result.error or "").lower()
    assert "commit" not in calls


@pytest.mark.asyncio
async def test_two_gates_approve_sequentially():
    workflow = (
        WorkflowBuilder("double-gated")
        .start()
        .tool("prepare", tool_id="prepare")
        .human("gate1")
        .human("gate2")
        .tool("commit", tool_id="commit")
        .end()
        .build()
    )
    store = InMemoryCheckpointStore()
    manager = await _manager(store, "hg-4")
    calls: dict[str, int] = {}
    executor = _executor(calls)

    with pytest.raises(ApprovalPendingError):
        await executor.execute(workflow, initial_input="x", checkpoint=manager)
    assert await record_approval_decision(store, "hg-4", True, approver="op")

    resumed = CheckpointManager(store, await store.load("hg-4"))
    with pytest.raises(ApprovalPendingError):
        await executor.execute(workflow, initial_input="x", checkpoint=resumed)
    assert await record_approval_decision(store, "hg-4", True, approver="op")

    resumed2 = CheckpointManager(store, await store.load("hg-4"))
    result = await executor.execute(workflow, initial_input="x", checkpoint=resumed2)

    assert result.status == ExecutionStatus.COMPLETED
    assert result.output == "committed:prepared:x"
    assert calls == {"prepare": 1, "commit": 1}


@pytest.mark.asyncio
async def test_one_approval_does_not_open_the_gate_for_every_iteration_item():
    """Regression: approving the first item's HUMAN gate passed all the others."""
    import itertools

    from baselith.core.workflows.builder import (
        NodeType,
        WorkflowDefinition,
        WorkflowEdge,
        WorkflowNode,
    )

    inner = (
        WorkflowBuilder("per-item")
        .start()
        .human("gate")
        .tool("pay", tool_id="pay")
        .end()
        .build()
    )
    outer = WorkflowDefinition(name="batch")
    nodes = [
        WorkflowNode(id="start", type=NodeType.START, label="s"),
        WorkflowNode(
            id="each",
            type=NodeType.ITERATION,
            label="each",
            config={"workflow": inner, "items": "{{ input }}"},
        ),
        WorkflowNode(id="end", type=NodeType.END, label="e"),
    ]
    for node in nodes:
        outer.add_node(node)
    for i, (x, y) in enumerate(itertools.pairwise(nodes)):
        outer.add_edge(WorkflowEdge(id=f"e{i}", source_id=x.id, target_id=y.id))
    paid: list[object] = []

    async def pay(value):
        paid.append(value)
        return value

    store = InMemoryCheckpointStore()
    manager = await _manager(store, "hg-iter")
    executor = WorkflowExecutor(tools={"pay": pay})

    approvals = 0
    for _ in range(6):
        try:
            result = await executor.execute(
                outer, initial_input=["a", "b", "c"], checkpoint=manager
            )
            break
        except ApprovalPendingError:
            approvals += 1
            assert await record_approval_decision(
                store, "hg-iter", True, approver="reviewer"
            )
            loaded = await store.load("hg-iter")
            assert loaded is not None
            manager = CheckpointManager(store, loaded)
    else:  # pragma: no cover
        pytest.fail("run never finished")

    assert approvals == 3, "each item must be approved on its own"
    assert paid == ["a", "b", "c"]
    assert result.status is ExecutionStatus.COMPLETED
