"""Tests for the orchestration enforcement chokepoint.

Verifies that ``core.orchestration.enforcement`` actually enforces the loop
budget, agent contract, and autonomy approval primitives that
``ExecutionMixin`` places on the context — and that it is a strict no-op when a
primitive is absent (backward compatibility).
"""

from __future__ import annotations

import pytest

from core.orchestration.autonomy import (
    ApprovalRequiredError,
    AutonomyLevel,
    AutonomyPolicy,
)
from core.orchestration.contract import (
    AgentContract,
    Capabilities,
    ContractValidator,
    ContractViolationError,
)
from core.orchestration.enforcement import (
    enforce_iteration,
    enforce_tool_invocation,
)
from core.orchestration.limits import (
    BudgetExceededError,
    LoopBudget,
    LoopLimits,
)


def _contract(allowed=None, forbidden=None) -> ContractValidator:
    contract = AgentContract(
        name="t",
        version="1.0",
        identity="tester",
        capabilities=Capabilities(
            allowed_tools=allowed or [],
            must_not=forbidden or [],
        ),
    )
    return ContractValidator(contract)


# --- enforce_iteration -----------------------------------------------------


def test_enforce_iteration_noop_without_context() -> None:
    enforce_iteration(None)
    enforce_iteration({})  # no loop_budget key


def test_enforce_iteration_ticks_budget() -> None:
    budget = LoopBudget(limits=LoopLimits(max_iterations=2))
    ctx = {"loop_budget": budget}
    enforce_iteration(ctx)
    assert budget.iterations == 1


def test_enforce_iteration_raises_at_cap() -> None:
    budget = LoopBudget(limits=LoopLimits(max_iterations=1))
    ctx = {"loop_budget": budget}
    enforce_iteration(ctx)  # 1 ok
    with pytest.raises(BudgetExceededError):
        enforce_iteration(ctx)  # 2 > cap


# --- enforce_tool_invocation -----------------------------------------------


async def test_tool_invocation_noop_without_primitives() -> None:
    await enforce_tool_invocation(None, "any")
    await enforce_tool_invocation({}, "any", "destructive")


async def test_tool_invocation_records_budget() -> None:
    budget = LoopBudget(limits=LoopLimits(max_tool_calls=1))
    ctx = {"loop_budget": budget}
    await enforce_tool_invocation(ctx, "search")
    assert budget.tool_calls == 1
    with pytest.raises(BudgetExceededError):
        await enforce_tool_invocation(ctx, "search")


async def test_tool_invocation_charges_cost() -> None:
    budget = LoopBudget(limits=LoopLimits(budget_usd=0.10))
    ctx = {"loop_budget": budget}
    with pytest.raises(BudgetExceededError):
        await enforce_tool_invocation(ctx, "expensive", cost_usd=0.5)


async def test_tool_invocation_contract_blocks_forbidden() -> None:
    ctx = {"contract_validator": _contract(forbidden=["delete_db"])}
    with pytest.raises(ContractViolationError):
        await enforce_tool_invocation(ctx, "delete_db")


async def test_tool_invocation_contract_allows_listed() -> None:
    ctx = {"contract_validator": _contract(allowed=["search"])}
    await enforce_tool_invocation(ctx, "search")
    with pytest.raises(ContractViolationError):
        await enforce_tool_invocation(ctx, "other")


async def test_tool_invocation_autonomy_fails_closed() -> None:
    # SUPERVISED requires approval for mutating tools; no human channel present.
    ctx = {"autonomy_policy": AutonomyPolicy(level=AutonomyLevel.SUPERVISED)}
    with pytest.raises(ApprovalRequiredError):
        await enforce_tool_invocation(ctx, "write", "mutating")


async def test_tool_invocation_read_only_passes_supervised() -> None:
    ctx = {"autonomy_policy": AutonomyPolicy(level=AutonomyLevel.SUPERVISED)}
    await enforce_tool_invocation(ctx, "read", "read_only")


async def test_tool_invocation_order_contract_before_budget() -> None:
    # A forbidden tool must be rejected by the contract *before* it consumes
    # any budget (fail-closed ordering).
    budget = LoopBudget()
    ctx = {
        "contract_validator": _contract(forbidden=["delete_db"]),
        "loop_budget": budget,
    }
    with pytest.raises(ContractViolationError):
        await enforce_tool_invocation(ctx, "delete_db")
    assert budget.tool_calls == 0
