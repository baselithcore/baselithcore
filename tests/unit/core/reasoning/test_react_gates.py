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
