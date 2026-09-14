"""Resume must restore the *whole* budget, and approvals must name a principal.

``init_checkpoint`` restored iterations / tool_calls / cost_usd but silently
dropped ``tokens`` and ``context_tokens``, so a resumed run got its token cap
back in full — the one counter a long run is most likely to be near.

``record_approval_decision`` accepted a free-text ``approver`` and wrote no
audit record, so "who approved the destructive tool, and how were they
authenticated?" had no answer.
"""

from __future__ import annotations

import pytest

from core.orchestration.checkpoint import (
    ApprovalPrincipal,
    Checkpoint,
    CheckpointManager,
    InMemoryCheckpointStore,
    init_checkpoint,
    record_approval_decision,
)
from core.orchestration.limits import LoopBudget, LoopLimits

pytestmark = [pytest.mark.unit]


async def test_resume_restores_every_budget_counter() -> None:
    store = InMemoryCheckpointStore()
    checkpoint = Checkpoint(run_id="run-b")
    checkpoint.budget = {
        "iterations": 4,
        "tool_calls": 7,
        "cost_usd": 0.21,
        "tokens": 123_456,
        "context_tokens": 2_048,
    }
    await store.save(checkpoint)

    budget = LoopBudget(limits=LoopLimits(max_tokens=200_000))
    await init_checkpoint(store, "q", {}, None, budget, "run-b", True)

    assert budget.iterations == 4
    assert budget.tool_calls == 7
    assert budget.cost_usd == pytest.approx(0.21)
    assert budget.tokens == 123_456
    assert budget.context_tokens == 2_048


async def test_resume_tolerates_a_legacy_snapshot_without_tokens() -> None:
    store = InMemoryCheckpointStore()
    checkpoint = Checkpoint(run_id="run-legacy")
    checkpoint.budget = {"iterations": 1, "tool_calls": 1, "cost_usd": 0.0}
    await store.save(checkpoint)

    budget = LoopBudget()
    await init_checkpoint(store, "q", {}, None, budget, "run-legacy", True)
    assert budget.tokens == 0
    assert budget.context_tokens == 0


async def test_restored_tokens_keep_the_cap_in_force() -> None:
    from core.orchestration.limits import BudgetExceededError

    store = InMemoryCheckpointStore()
    checkpoint = Checkpoint(run_id="run-cap")
    checkpoint.budget = {"tokens": 99, "iterations": 0, "tool_calls": 0}
    await store.save(checkpoint)

    budget = LoopBudget(limits=LoopLimits(max_tokens=100))
    await init_checkpoint(store, "q", {}, None, budget, "run-cap", True)
    with pytest.raises(BudgetExceededError):
        budget.record_tokens(5)


def _store_with_pending() -> tuple[InMemoryCheckpointStore, Checkpoint]:
    store = InMemoryCheckpointStore()
    checkpoint = Checkpoint(run_id="run-1", tenant_id="acme")
    return store, checkpoint


async def _pending(store: InMemoryCheckpointStore, checkpoint: Checkpoint) -> None:
    manager = CheckpointManager(store, checkpoint)
    await manager.await_approval("deploy", "destructive")


class TestApprovalPrincipal:
    def test_principal_carries_id_and_auth_method(self) -> None:
        principal = ApprovalPrincipal(id="u-7", auth_method="oidc")
        assert principal.id == "u-7"
        assert principal.auth_method == "oidc"
        assert principal.to_dict() == {"id": "u-7", "auth_method": "oidc"}

    def test_principal_rejects_a_blank_id(self) -> None:
        with pytest.raises(ValueError):
            ApprovalPrincipal(id="  ", auth_method="oidc")

    def test_from_value_passes_a_principal_through(self) -> None:
        principal = ApprovalPrincipal(id="u-7", auth_method="oidc")
        assert ApprovalPrincipal.from_value(principal) is principal

    def test_from_value_coerces_a_bare_string(self) -> None:
        coerced = ApprovalPrincipal.from_value("giovanni")
        assert coerced.id == "giovanni"
        assert coerced.auth_method == "programmatic"

    def test_from_value_rejects_none(self) -> None:
        # An approval recorded against nobody looks like an answer to "who
        # approved this?" while being the absence of one.
        with pytest.raises(ValueError, match="requires an approver"):
            ApprovalPrincipal.from_value(None)

    def test_from_value_rejects_a_blank_string(self) -> None:
        with pytest.raises(ValueError, match="requires an approver"):
            ApprovalPrincipal.from_value("   ")


class TestRecordApprovalDecision:
    async def test_principal_is_persisted_with_the_decision(self) -> None:
        store, checkpoint = _store_with_pending()
        await _pending(store, checkpoint)

        assert await record_approval_decision(
            store,
            "run-1",
            True,
            approver=ApprovalPrincipal(id="u-7", auth_method="oidc"),
        )
        loaded = await store.load("run-1")
        decision = loaded.pending_approval["decision"]
        assert decision["approved"] is True
        assert decision["approver"] == "u-7"
        assert decision["approver_auth_method"] == "oidc"

    async def test_string_approver_still_works(self) -> None:
        store, checkpoint = _store_with_pending()
        await _pending(store, checkpoint)

        assert await record_approval_decision(store, "run-1", False, approver="gio")
        loaded = await store.load("run-1")
        decision = loaded.pending_approval["decision"]
        assert decision["approver"] == "gio"
        assert decision["approver_auth_method"] == "programmatic"

    async def test_decision_emits_an_audit_event(self, monkeypatch) -> None:
        from core.observability.audit import AuditEventType

        recorded: list[dict] = []

        class _Audit:
            async def log(self, event_type, **kwargs):
                recorded.append({"event_type": event_type, **kwargs})

        monkeypatch.setattr(
            "core.observability.audit.get_audit_logger", lambda: _Audit()
        )
        store, checkpoint = _store_with_pending()
        await _pending(store, checkpoint)

        await record_approval_decision(
            store,
            "run-1",
            True,
            approver=ApprovalPrincipal(id="u-7", auth_method="mtls"),
            reason="looks fine",
        )

        assert len(recorded) == 1
        event = recorded[0]
        assert event["event_type"] is AuditEventType.APPROVAL_DECISION
        assert event["resource"] == "run-1"
        assert event["tenant_id"] == "acme"
        assert event["success"] is True
        assert event["details"]["approver_id"] == "u-7"
        assert event["details"]["auth_method"] == "mtls"
        assert event["details"]["tool_name"] == "deploy"
        assert event["details"]["category"] == "destructive"

    async def test_audit_failure_never_loses_the_decision(self, monkeypatch) -> None:
        class _BrokenAudit:
            async def log(self, *args, **kwargs):
                raise RuntimeError("sink down")

        monkeypatch.setattr(
            "core.observability.audit.get_audit_logger", lambda: _BrokenAudit()
        )
        store, checkpoint = _store_with_pending()
        await _pending(store, checkpoint)

        assert await record_approval_decision(store, "run-1", True, approver="gio")
        loaded = await store.load("run-1")
        assert loaded.pending_approval["decision"]["approved"] is True

    async def test_no_audit_event_for_an_unknown_run(self, monkeypatch) -> None:
        recorded: list = []

        class _Audit:
            async def log(self, event_type, **kwargs):
                recorded.append(event_type)

        monkeypatch.setattr(
            "core.observability.audit.get_audit_logger", lambda: _Audit()
        )
        assert (
            await record_approval_decision(
                InMemoryCheckpointStore(), "ghost", True, approver="op"
            )
            is False
        )
        assert recorded == []

    async def test_an_approver_less_decision_is_refused(self) -> None:
        store, checkpoint = _store_with_pending()
        await _pending(store, checkpoint)

        with pytest.raises(ValueError, match="requires an approver"):
            await record_approval_decision(store, "run-1", True)

        # Nothing half-written: the run is still awaiting a real decision.
        loaded = await store.load("run-1")
        assert "decision" not in loaded.pending_approval
