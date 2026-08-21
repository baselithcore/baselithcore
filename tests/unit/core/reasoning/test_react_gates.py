"""Tests for ReActAgent contract / autonomy / budget gating of tool calls."""

from __future__ import annotations

import pytest

from core.orchestration.autonomy import (
    ApprovalPendingError,
    AutonomyLevel,
    AutonomyPolicy,
)
from core.orchestration.contract import (
    AgentContract,
    Capabilities,
    ContractValidator,
)
from core.orchestration.limits import BudgetExceededError, LoopBudget, LoopLimits
from core.reasoning.react import ReActAgent, ToolDefinition


class _Approver:
    """Stub HumanIntervention channel with a canned decision."""

    def __init__(self, decision: bool) -> None:
        self.decision = decision
        self.requests: list[str] = []

    async def request_approval(self, description, timeout=None, context=None):
        self.requests.append(description)
        return self.decision


class _PendingCheckpoint:
    """Stub CheckpointManager: no recorded decision, records the pause."""

    run_id = "run-123"

    def __init__(self) -> None:
        self.awaiting: list[tuple[str, str]] = []

    def approval_decision(self, tool_name, category):
        return None

    async def await_approval(self, tool_name, category):
        self.awaiting.append((tool_name, category))


def _tool(calls: list, category: str = "read_only") -> ToolDefinition:
    async def fn(*args, **kwargs):
        calls.append(1)
        return "done"

    return ToolDefinition(name="t", fn=fn, description="test tool", category=category)


class TestAutonomyGate:
    async def test_gated_category_fails_closed_without_channel(self) -> None:
        calls: list = []
        agent = ReActAgent(
            tools=[_tool(calls, category="destructive")],
            autonomy_policy=AutonomyPolicy(level=AutonomyLevel.SUPERVISED),
        )
        result = await agent._execute_tool("t", "")
        assert "requires human" in result
        assert calls == []

    async def test_read_only_default_needs_no_approval(self) -> None:
        calls: list = []
        agent = ReActAgent(
            tools=[_tool(calls)],
            autonomy_policy=AutonomyPolicy(level=AutonomyLevel.SUPERVISED),
        )
        result = await agent._execute_tool("t", "")
        assert result == "done"
        assert calls == [1]

    async def test_human_approval_allows_execution(self) -> None:
        calls: list = []
        channel = _Approver(True)
        agent = ReActAgent(
            tools=[_tool(calls, category="mutating")],
            autonomy_policy=AutonomyPolicy(level=AutonomyLevel.SUPERVISED),
            human_intervention=channel,
        )
        result = await agent._execute_tool("t", "")
        assert result == "done"
        assert calls == [1]
        assert len(channel.requests) == 1

    async def test_human_denial_blocks_execution(self) -> None:
        calls: list = []
        agent = ReActAgent(
            tools=[_tool(calls, category="mutating")],
            autonomy_policy=AutonomyPolicy(level=AutonomyLevel.SUPERVISED),
            human_intervention=_Approver(False),
        )
        result = await agent._execute_tool("t", "")
        assert "denied by human reviewer" in result
        assert calls == []

    async def test_checkpoint_pause_propagates(self) -> None:
        calls: list = []
        checkpoint = _PendingCheckpoint()
        agent = ReActAgent(
            tools=[_tool(calls, category="destructive")],
            autonomy_policy=AutonomyPolicy(level=AutonomyLevel.SUPERVISED),
            checkpoint=checkpoint,
        )
        with pytest.raises(ApprovalPendingError):
            await agent._execute_tool("t", "")
        assert checkpoint.awaiting == [("t", "destructive")]
        assert calls == []

    async def test_fully_autonomous_skips_gate(self) -> None:
        calls: list = []
        agent = ReActAgent(
            tools=[_tool(calls, category="destructive")],
            autonomy_policy=AutonomyPolicy(level=AutonomyLevel.FULLY_AUTONOMOUS),
        )
        result = await agent._execute_tool("t", "")
        assert result == "done"


class TestContractGate:
    def _validator(self) -> ContractValidator:
        contract = AgentContract(
            name="test",
            version="1.0",
            identity="test agent",
            capabilities=Capabilities(allowed_tools=["allowed"]),
        )
        return ContractValidator(contract)

    async def test_tool_outside_contract_blocked(self) -> None:
        calls: list = []
        agent = ReActAgent(
            tools=[_tool(calls)],
            contract_validator=self._validator(),
        )
        result = await agent._execute_tool("t", "")
        assert result.startswith("Error executing 't'")
        assert calls == []


class TestBudgetGate:
    async def test_explicit_budget_cap_raises(self) -> None:
        calls: list = []
        budget = LoopBudget(limits=LoopLimits(max_tool_calls=1))
        agent = ReActAgent(tools=[_tool(calls)], loop_budget=budget)
        assert await agent._execute_tool("t", "") == "done"
        with pytest.raises(BudgetExceededError):
            await agent._execute_tool("t", "")
        assert calls == [1]

    async def test_ambient_budget_consumed(self) -> None:
        from core.orchestration.budget_context import activate_budget

        calls: list = []
        budget = LoopBudget(limits=LoopLimits(max_tool_calls=1))
        agent = ReActAgent(tools=[_tool(calls)])
        token = activate_budget(budget)
        try:
            assert await agent._execute_tool("t", "") == "done"
            assert budget.tool_calls == 1
            with pytest.raises(BudgetExceededError):
                await agent._execute_tool("t", "")
        finally:
            from core.orchestration.budget_context import deactivate_budget

            deactivate_budget(token)


class TestConsecutiveFailureEscalation:
    async def test_streak_escalates_and_stops_loop(self) -> None:
        calls: list = []

        async def broken(*args, **kwargs):
            calls.append(1)
            raise ValueError("always broken")

        agent = ReActAgent(
            tools=[ToolDefinition(name="t", fn=broken, description="broken", category="read_only")],
            max_iterations=10,
            max_consecutive_tool_failures=3,
        )
        agent._llm_service = type(
            "_LLM",
            (),
            {
                "generate_response": staticmethod(
                    lambda *a, **k: _async_return("Thought: retry.\nAction: t()")
                )
            },
        )()
        result = await agent.run("do the thing")
        assert result.hit_limit is True
        assert "3 consecutive times" in result.final_answer
        assert len(calls) == 3  # escalated well before max_iterations=10

    async def test_success_resets_streak(self) -> None:
        agent = ReActAgent(tools=[], max_consecutive_tool_failures=2)
        assert agent._note_tool_outcome("Error executing 't': boom") is None
        assert agent._note_tool_outcome("fine") is None  # reset
        assert agent._note_tool_outcome("Error executing 't': boom") is None
        escalation = agent._note_tool_outcome("Error executing 't': boom")
        assert escalation is not None and "2 consecutive" in escalation

    async def test_guard_disabled_with_none(self) -> None:
        agent = ReActAgent(tools=[], max_consecutive_tool_failures=None)
        for _ in range(50):
            assert agent._note_tool_outcome("Error executing 't': boom") is None


class TestStallGuard:
    """Futility detection: the same failure coming back, not just many failures."""

    async def test_disabled_by_default(self) -> None:
        # Opt-in: the historical behavior (streak only) is unchanged.
        agent = ReActAgent(tools=[], max_consecutive_tool_failures=None)
        assert agent._stall_guard is None
        for _ in range(20):
            assert agent._note_tool_outcome("Error executing 't': boom") is None

    async def test_identical_failures_escalate(self) -> None:
        agent = ReActAgent(
            tools=[], max_consecutive_tool_failures=None, stall_threshold=3
        )
        assert agent._note_tool_outcome("Error executing 't': boom") is None
        assert agent._note_tool_outcome("Error executing 't': boom") is None
        escalation = agent._note_tool_outcome("Error executing 't': boom")
        assert escalation is not None and "no progress" in escalation

    async def test_distinct_failures_do_not_escalate(self) -> None:
        # A tool failing differently each time is still converging on
        # information; only an unchanged fingerprint means futility.
        agent = ReActAgent(
            tools=[], max_consecutive_tool_failures=None, stall_threshold=2
        )
        assert agent._note_tool_outcome("Error executing 't': timeout") is None
        assert agent._note_tool_outcome("Error executing 't': bad request") is None
        assert agent._note_tool_outcome("Error executing 't': not found") is None

    async def test_success_between_failures_still_counts_the_repeat(self) -> None:
        # The streak resets on success; the fingerprint does not — a tool
        # that alternates one good call with the same error is not healthy.
        agent = ReActAgent(
            tools=[], max_consecutive_tool_failures=None, stall_threshold=2
        )
        assert agent._note_tool_outcome("Error executing 't': boom") is None
        assert agent._note_tool_outcome("all good") is None
        escalation = agent._note_tool_outcome("Error executing 't': boom")
        assert escalation is not None and "no progress" in escalation

    async def test_streak_guard_still_wins_when_it_trips_first(self) -> None:
        agent = ReActAgent(
            tools=[], max_consecutive_tool_failures=2, stall_threshold=10
        )
        assert agent._note_tool_outcome("Error: a") is None
        escalation = agent._note_tool_outcome("Error: b")
        assert escalation is not None and "2 consecutive times" in escalation


async def _async_return(value):
    return value
