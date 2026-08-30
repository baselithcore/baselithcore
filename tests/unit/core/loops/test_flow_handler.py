"""Tests for LoopFlowHandler — the orchestrator bridge for EngineeredLoop."""

from __future__ import annotations

import pytest
from core.loops.flow_handler import LoopFlowHandler

from core.orchestration.checkpoint import Checkpoint, CheckpointManager
from core.orchestration.checkpoint_memory import InMemoryCheckpointStore
from core.orchestration.limits import LoopBudget, LoopLimits

pytestmark = [pytest.mark.unit]


def _handler(verify_results: list[bool], **kwargs) -> LoopFlowHandler:
    calls = {"acts": 0}
    results = iter(verify_results)

    def act_factory(query, context):
        async def act(attempt_context):
            calls["acts"] += 1

        return act

    def verify_factory(query, context):
        async def verify():
            try:
                done = next(results)
            except StopIteration:
                done = False
            return done, "evidence line"

        return verify

    handler = LoopFlowHandler(act_factory, verify_factory, **kwargs)
    handler._test_calls = calls  # type: ignore[attr-defined]
    return handler


class TestSuccess:
    async def test_success_returns_evidence(self):
        handler = _handler([True], max_attempts=3)
        result = await handler.handle("goal: green", {})
        assert not result.get("error")
        assert "evidence line" in result["response"]
        assert result["metadata"]["loop"]["status"] == "success"
        assert result["metadata"]["loop"]["attempts"] == 1


class TestFailure:
    async def test_exhausted_escalates_and_reports_error(self):
        escalated = []

        async def escalate(outcome):
            escalated.append(outcome)

        handler = _handler([False, False], max_attempts=2, escalate=escalate)
        result = await handler.handle("goal: green", {})
        assert result["error"] is True
        assert result["metadata"]["loop"]["status"] == "exhausted"
        assert len(escalated) == 1
        assert escalated[0].attempts == 2


class TestDurability:
    async def test_outcome_and_heartbeat_persisted_to_checkpoint(self):
        store = InMemoryCheckpointStore()
        manager = CheckpointManager(store, Checkpoint(run_id="r1"))
        await store.save(manager.checkpoint)

        handler = _handler([False], max_attempts=1)
        context = {"checkpoint": manager, "run_id": "r1"}
        await handler.handle("goal: green", context)

        loaded = await store.load("r1")
        assert loaded is not None
        assert loaded.plugin_data["loop_outcome"]["status"] == "exhausted"
        assert loaded.plugin_data["loop_last_progress_at"] > 0


class TestBudget:
    async def test_context_budget_bounds_attempts(self):
        budget = LoopBudget(limits=LoopLimits(max_iterations=1))
        handler = _handler([False, False], max_attempts=5)
        result = await handler.handle("goal: green", {"loop_budget": budget})
        assert result["metadata"]["loop"]["status"] == "budget_exceeded"
