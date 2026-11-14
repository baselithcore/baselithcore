"""A durable approval covers the call it was asked for, not the tool in general.

Regression: a recorded approval was matched on ``(tool name, category)`` only,
and nothing consumed or cleared it. After a reviewer approved ``deploy`` for
``{"env": "staging"}``, every later ``deploy`` call in the same run -- including
``{"env": "prod"}`` -- found the recorded decision and went through without a
second look. The approval authorised a tool; the reviewer thought they were
authorising one call.

The request now carries the SHA-256 of the call's arguments (the digest the audit
trail already records). A decision matches only the digest it was recorded for;
the decisions of earlier calls are carried forward, so replaying an approved
call after a later one paused still finds its approval.
"""

from __future__ import annotations

import pytest

from baselith.core.orchestration.autonomy import (
    ApprovalPendingError,
    ApprovalRequiredError,
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

STAGING = {"env": "staging"}
PROD = {"env": "prod"}


def _manager(store: InMemoryCheckpointStore | None = None) -> CheckpointManager:
    store = store or InMemoryCheckpointStore()
    return CheckpointManager(store, Checkpoint(run_id="run-1", query="q"))


async def _resume(manager: CheckpointManager) -> CheckpointManager:
    """A fresh manager over the persisted checkpoint, as a resumed run builds it."""
    loaded = await manager.store.load("run-1")
    assert loaded is not None
    return CheckpointManager(manager.store, loaded)


async def _call(manager: CheckpointManager, args: dict[str, str]) -> None:
    context = {
        "autonomy_policy": AutonomyPolicy(level=AutonomyLevel.SUPERVISED),
        "checkpoint": manager,
    }
    await enforce_tool_invocation(context, "deploy", "destructive", args=args)


async def _approve(manager: CheckpointManager, approved: bool = True) -> None:
    assert await record_approval_decision(
        manager.store, "run-1", approved, approver="reviewer"
    )


async def test_approving_one_call_does_not_cover_a_different_one() -> None:
    manager = _manager()
    with pytest.raises(ApprovalPendingError):
        await _call(manager, STAGING)
    await _approve(manager)

    resumed = await _resume(manager)
    await _call(resumed, STAGING)  # the approved call goes through
    with pytest.raises(ApprovalPendingError):
        await _call(resumed, PROD)  # a different call needs its own approval


async def test_replaying_an_approved_call_after_a_later_pause_stays_approved() -> None:
    manager = _manager()
    with pytest.raises(ApprovalPendingError):
        await _call(manager, STAGING)
    await _approve(manager)
    resumed = await _resume(manager)
    with pytest.raises(ApprovalPendingError):
        await _call(resumed, PROD)
    await _approve(resumed)

    # Resume replays the first call before it reaches the second one.
    replay = await _resume(resumed)
    await _call(replay, STAGING)
    await _call(replay, PROD)


async def test_a_denial_does_not_deny_a_different_call() -> None:
    manager = _manager()
    with pytest.raises(ApprovalPendingError):
        await _call(manager, PROD)
    await _approve(manager, approved=False)

    resumed = await _resume(manager)
    with pytest.raises(ApprovalRequiredError) as denied:
        await _call(resumed, PROD)
    assert not isinstance(denied.value, ApprovalPendingError)
    with pytest.raises(ApprovalPendingError):
        await _call(resumed, STAGING)


async def test_a_request_without_a_digest_still_matches_by_name() -> None:
    # Callers that never pass arguments (the HUMAN workflow node, plugin gates,
    # a run paused by an earlier release) keep their name-and-category contract.
    manager = _manager()
    await manager.await_approval("deploy", "destructive")
    await _approve(manager)

    resumed = await _resume(manager)
    assert resumed.approval_decision("deploy", "destructive") is True
    assert resumed.approval_decision("deploy", "destructive", "any-digest") is True


async def test_a_bound_request_is_matched_only_by_its_own_digest() -> None:
    manager = _manager()
    await manager.await_approval("deploy", "destructive", "digest-a")
    await _approve(manager)

    resumed = await _resume(manager)
    assert resumed.approval_decision("deploy", "destructive", "digest-a") is True
    assert resumed.approval_decision("deploy", "destructive", "digest-b") is None
    assert resumed.approval_decision("deploy", "destructive") is None


async def test_the_scope_forwards_the_digest() -> None:
    manager = _manager()
    scope = manager.scope("turn", {"n": 1})
    await scope.await_approval("deploy", "destructive", "digest-a")
    await _approve(manager)

    resumed = await _resume(manager)
    resumed_scope = resumed.scope("turn", {"n": 1})
    assert resumed_scope.approval_decision("deploy", "destructive", "digest-a") is True
    assert resumed_scope.approval_decision("deploy", "destructive", "digest-b") is None


async def test_the_decision_history_stays_bounded() -> None:
    manager = _manager()
    for index in range(80):
        await manager.await_approval("deploy", "destructive", f"digest-{index}")
        await _approve(manager)
        manager = await _resume(manager)

    pending = manager.checkpoint.pending_approval or {}
    assert len(pending.get("decided", [])) <= 64
    # The newest decisions are the ones kept.
    assert manager.approval_decision("deploy", "destructive", "digest-79") is True
    assert manager.approval_decision("deploy", "destructive", "digest-0") is None


async def test_the_decision_audit_names_the_call_it_approved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded: list[dict[str, object]] = []

    class _Audit:
        async def log(self, event_type: object, **kwargs: object) -> None:
            recorded.append(kwargs)

    monkeypatch.setattr(
        "baselith.core.observability.audit.get_audit_logger", lambda: _Audit()
    )
    manager = _manager()
    await manager.await_approval("deploy", "destructive", "digest-a")

    await _approve(manager)

    # "Who approved what" needs the what: the digest ties the decision to one call.
    assert recorded[0]["details"]["args_digest"] == "digest-a"  # type: ignore[index]
