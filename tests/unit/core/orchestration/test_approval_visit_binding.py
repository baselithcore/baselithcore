"""A durable approval covers one visit of a gate, not every later identical one.

Regression: after a reviewer approved ``send_payment({"to": "bob"})`` a model
(or an injected prompt) could repeat the identical call and every repeat found
the recorded decision; a HUMAN gate approved on the first item of a list passed
on all the others. The decision is now bound to the scope the gate ran in and
to the occurrence of that gate within the pass.
"""

from __future__ import annotations

import pytest

from baselith.core.orchestration.autonomy import (
    ApprovalPendingError,
    AutonomyLevel,
    AutonomyPolicy,
)
from baselith.core.orchestration.checkpoint import (
    Checkpoint,
    CheckpointManager,
    InMemoryCheckpointStore,
    record_approval_decision,
)
from baselith.core.orchestration.enforcement import enforce_tool_invocation

PAY = {"to": "bob", "amount": 100}


def _manager(store: InMemoryCheckpointStore | None = None) -> CheckpointManager:
    store = store or InMemoryCheckpointStore()
    return CheckpointManager(store, Checkpoint(run_id="run-1", query="q"))


async def _resume(manager: CheckpointManager) -> CheckpointManager:
    loaded = await manager.store.load("run-1")
    assert loaded is not None
    return CheckpointManager(manager.store, loaded)


async def _pay(manager: CheckpointManager, args: dict = PAY) -> None:
    context = {
        "autonomy_policy": AutonomyPolicy(level=AutonomyLevel.SUPERVISED),
        "checkpoint": manager,
    }
    await enforce_tool_invocation(context, "send_payment", "destructive", args=args)


async def _approve(manager: CheckpointManager) -> None:
    assert await record_approval_decision(
        manager.store, "run-1", True, approver="reviewer"
    )


async def test_an_identical_repeat_asks_again() -> None:
    manager = _manager()
    with pytest.raises(ApprovalPendingError):
        await _pay(manager)
    await _approve(manager)

    resumed = await _resume(manager)
    await _pay(resumed)  # visit 0: the approved call
    with pytest.raises(ApprovalPendingError):
        await _pay(resumed)  # visit 1: a repeat is a new call
    await _approve(resumed)

    # Replaying the whole run finds both visits decided, in order.
    replay = await _resume(resumed)
    await _pay(replay)
    await _pay(replay)
    with pytest.raises(ApprovalPendingError):
        await _pay(replay)  # a third repeat is still undecided


async def test_five_identical_calls_cost_five_approvals() -> None:
    manager = _manager()
    approvals = 0
    for _ in range(5):
        # Each pass replays every visit decided so far, then reaches a new one.
        try:
            for _ in range(5):
                await _pay(manager)
            break
        except ApprovalPendingError:
            approvals += 1
            await _approve(manager)
            manager = await _resume(manager)
    assert approvals == 5


async def test_each_scope_is_its_own_gate() -> None:
    manager = _manager()
    first = manager.scope("item", {"i": 0})
    await first.await_approval("review", "human_gate")
    await _approve(manager)

    resumed = await _resume(manager)
    assert resumed.scope("item", {"i": 0}).approval_decision("review", "human_gate")
    # The next item of the same node is another gate, not an approved one.
    assert (
        resumed.scope("item", {"i": 1}).approval_decision("review", "human_gate")
        is None
    )


async def test_a_gate_revisited_in_a_cycle_asks_again() -> None:
    manager = _manager()
    await manager.await_approval("review", "human_gate")
    await _approve(manager)

    resumed = await _resume(manager)
    assert resumed.approval_decision("review", "human_gate") is True
    assert resumed.approval_decision("review", "human_gate") is None


async def test_a_pause_from_an_earlier_release_keeps_its_contract() -> None:
    """A pending request with no scope/occurrence matches any visit once."""
    manager = _manager()
    manager.checkpoint.pending_approval = {
        "tool_name": "send_payment",
        "category": "destructive",
        "decision": {"approved": True, "approver": "r"},
    }
    assert manager.approval_decision("send_payment", "destructive") is True


async def test_the_reviewer_reads_the_arguments_redacted_and_bounded() -> None:
    manager = _manager()
    args = {
        "to": "mallory",
        "amount": 999999,
        "api_key": "sk-" + "a" * 40,
        "note": "x" * 5000,
    }
    with pytest.raises(ApprovalPendingError):
        await _pay(manager, args)

    pending = manager.checkpoint.pending_approval
    assert pending is not None and pending["args_digest"]
    preview = pending["args_preview"]
    assert "mallory" in preview and "999999" in preview
    assert "a" * 40 not in preview
    assert len(preview) <= 1001
