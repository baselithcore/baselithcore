"""Durable human-in-the-loop approvals: checkpoint pause/resume flow."""

import pytest

from core.orchestration.autonomy import (
    ApprovalPendingError,
    ApprovalRequiredError,
    AutonomyLevel,
    AutonomyPolicy,
    enforce_approval,
)
from core.orchestration.checkpoint import (
    STATUS_AWAITING_APPROVAL,
    STATUS_RUNNING,
    Checkpoint,
    CheckpointManager,
    InMemoryCheckpointStore,
    record_approval_decision,
)
from core.orchestration.enforcement import enforce_tool_invocation

SUPERVISED = AutonomyPolicy(level=AutonomyLevel.SUPERVISED)


def _manager(store=None, run_id="run-1"):
    store = store or InMemoryCheckpointStore()
    return CheckpointManager(store, Checkpoint(run_id=run_id, query="q")), store


# ---------------------------------------------------------------------------
# Checkpoint primitives
# ---------------------------------------------------------------------------


async def test_await_approval_persists_paused_state():
    manager, store = _manager()
    await manager.await_approval("deploy", "destructive")

    loaded = await store.load("run-1")
    assert loaded.status == STATUS_AWAITING_APPROVAL
    assert loaded.pending_approval["tool_name"] == "deploy"
    assert loaded.pending_approval["category"] == "destructive"


async def test_awaiting_runs_are_listed_resumable():
    manager, store = _manager()
    await manager.await_approval("deploy", "destructive")
    assert await store.list_resumable() == ["run-1"]


async def test_record_decision_roundtrip():
    manager, store = _manager()
    await manager.await_approval("deploy", "destructive")

    assert await record_approval_decision(store, "run-1", True, approver="giovanni")
    resumed = CheckpointManager(store, await store.load("run-1"))
    assert resumed.approval_decision("deploy", "destructive") is True
    # A different tool/category never consumes someone else's decision.
    assert resumed.approval_decision("other", "destructive") is None


async def test_record_decision_unknown_run_or_no_pending():
    store = InMemoryCheckpointStore()
    assert await record_approval_decision(store, "ghost", True) is False
    manager, _ = _manager(store, run_id="r2")
    await store.save(manager.checkpoint)  # running, no pending approval
    assert await record_approval_decision(store, "r2", True) is False


async def test_pending_approval_survives_serialization():
    checkpoint = Checkpoint(run_id="r", pending_approval={"tool_name": "t"})
    rebuilt = Checkpoint.from_dict(checkpoint.to_dict())
    assert rebuilt.pending_approval == {"tool_name": "t"}


# ---------------------------------------------------------------------------
# enforce_approval durable gate
# ---------------------------------------------------------------------------


async def test_no_channel_with_checkpoint_pauses_run():
    manager, store = _manager()
    with pytest.raises(ApprovalPendingError) as exc_info:
        await enforce_approval(
            SUPERVISED, "destructive", "deploy", None, checkpoint=manager
        )
    assert exc_info.value.run_id == "run-1"
    loaded = await store.load("run-1")
    assert loaded.status == STATUS_AWAITING_APPROVAL


async def test_no_channel_without_checkpoint_keeps_terminal_denial():
    with pytest.raises(ApprovalRequiredError) as exc_info:
        await enforce_approval(SUPERVISED, "destructive", "deploy", None)
    assert not isinstance(exc_info.value, ApprovalPendingError)


async def test_recorded_approval_lets_tool_proceed():
    manager, store = _manager()
    await manager.await_approval("deploy", "destructive")
    await record_approval_decision(store, "run-1", True)

    resumed = CheckpointManager(store, await store.load("run-1"))
    # No exception: the recorded decision is consumed.
    await enforce_approval(
        SUPERVISED, "destructive", "deploy", None, checkpoint=resumed
    )


async def test_recorded_denial_is_terminal():
    manager, store = _manager()
    await manager.await_approval("deploy", "destructive")
    await record_approval_decision(store, "run-1", False, reason="too risky")

    resumed = CheckpointManager(store, await store.load("run-1"))
    with pytest.raises(ApprovalRequiredError) as exc_info:
        await enforce_approval(
            SUPERVISED, "destructive", "deploy", None, checkpoint=resumed
        )
    assert not isinstance(exc_info.value, ApprovalPendingError)


async def test_read_only_never_pauses():
    manager, _ = _manager()
    await enforce_approval(SUPERVISED, "read_only", "search", None, checkpoint=manager)
    assert manager.checkpoint.status == STATUS_RUNNING


# ---------------------------------------------------------------------------
# enforcement chokepoint end-to-end (pause -> decide -> resume)
# ---------------------------------------------------------------------------


async def test_enforce_tool_invocation_pause_then_resume():
    manager, store = _manager()
    context = {"autonomy_policy": SUPERVISED, "checkpoint": manager}

    with pytest.raises(ApprovalPendingError):
        await enforce_tool_invocation(context, "deploy", "destructive")

    assert await record_approval_decision(store, "run-1", True)

    resumed_ctx = {
        "autonomy_policy": SUPERVISED,
        "checkpoint": CheckpointManager(store, await store.load("run-1")),
    }
    # Gate passes on resume; no exception.
    await enforce_tool_invocation(resumed_ctx, "deploy", "destructive")


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
