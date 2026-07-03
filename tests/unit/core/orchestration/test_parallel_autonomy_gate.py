"""Tests for the autonomy approval gate in ParallelToolExecutor.

The executor is the core choke point for orchestrated tool calls: with an
``autonomy_policy`` configured, tools whose registered category requires
approval are gated through ``enforce_approval`` before execution.
"""

from __future__ import annotations

from core.orchestration.autonomy import AutonomyLevel, AutonomyPolicy
from core.orchestration.parallel import ParallelToolExecutor, ToolCall, ToolStatus


class _Human:
    def __init__(self, answer: bool) -> None:
        self.answer = answer
        self.requests: list[str] = []

    async def request_approval(self, description, timeout=None, context=None):
        self.requests.append(description)
        return self.answer


def _executor(policy=None, human=None) -> ParallelToolExecutor:
    executor = ParallelToolExecutor(autonomy_policy=policy, human_intervention=human)

    async def read_tool() -> str:
        return "read-ok"

    async def write_tool() -> str:
        return "write-ok"

    executor.register_tool("read_tool", read_tool)  # read_only default
    executor.register_tool("write_tool", write_tool, category="mutating")
    return executor


async def test_no_policy_keeps_legacy_behavior() -> None:
    executor = _executor()
    results = await executor.execute_parallel([ToolCall(tool_name="write_tool")])
    assert results[0].success
    assert results[0].result == "write-ok"


async def test_mutating_blocked_when_supervised_without_channel() -> None:
    executor = _executor(policy=AutonomyPolicy(level=AutonomyLevel.SUPERVISED))
    call = ToolCall(tool_name="write_tool")
    results = await executor.execute_parallel([call])
    assert not results[0].success
    assert "requires human approval" in (results[0].error or "")
    assert call.status is ToolStatus.SKIPPED


async def test_read_only_passes_when_supervised() -> None:
    executor = _executor(policy=AutonomyPolicy(level=AutonomyLevel.SUPERVISED))
    results = await executor.execute_parallel([ToolCall(tool_name="read_tool")])
    assert results[0].success
    assert results[0].result == "read-ok"


async def test_mutating_approved_via_human_channel() -> None:
    human = _Human(answer=True)
    executor = _executor(
        policy=AutonomyPolicy(level=AutonomyLevel.SUPERVISED), human=human
    )
    results = await executor.execute_parallel([ToolCall(tool_name="write_tool")])
    assert results[0].success
    assert human.requests


async def test_mutating_denied_via_human_channel() -> None:
    human = _Human(answer=False)
    executor = _executor(
        policy=AutonomyPolicy(level=AutonomyLevel.SUPERVISED), human=human
    )
    results = await executor.execute_parallel([ToolCall(tool_name="write_tool")])
    assert not results[0].success
    assert "denied" in (results[0].error or "")


async def test_fully_autonomous_skips_gate() -> None:
    executor = _executor(policy=AutonomyPolicy(level=AutonomyLevel.FULLY_AUTONOMOUS))
    results = await executor.execute_parallel([ToolCall(tool_name="write_tool")])
    assert results[0].success


# --- loop budget + contract enforcement ------------------------------------


async def test_loop_budget_caps_tool_calls() -> None:
    from core.orchestration.limits import LoopBudget, LoopLimits

    budget = LoopBudget(limits=LoopLimits(max_tool_calls=1))
    executor = ParallelToolExecutor(loop_budget=budget)

    async def read_tool() -> str:
        return "ok"

    executor.register_tool("read_tool", read_tool)
    results = await executor.execute_parallel(
        [ToolCall(tool_name="read_tool"), ToolCall(tool_name="read_tool")]
    )
    # First call succeeds; second trips the tool-call cap and is skipped.
    successes = [r for r in results if r.success]
    failures = [r for r in results if not r.success]
    assert len(successes) == 1
    assert len(failures) == 1
    assert "budget" in (failures[0].error or "").lower()


async def test_contract_validator_blocks_forbidden_tool() -> None:
    from core.orchestration.contract import (
        AgentContract,
        Capabilities,
        ContractValidator,
    )

    validator = ContractValidator(
        AgentContract(
            name="t",
            version="1.0",
            identity="tester",
            capabilities=Capabilities(allowed_tools=["read_tool"]),
        )
    )
    executor = ParallelToolExecutor(contract_validator=validator)

    async def read_tool() -> str:
        return "ok"

    async def write_tool() -> str:
        return "written"

    executor.register_tool("read_tool", read_tool)
    executor.register_tool("write_tool", write_tool)

    results = await executor.execute_parallel(
        [ToolCall(tool_name="read_tool"), ToolCall(tool_name="write_tool")]
    )
    by_name = {r.tool_name: r for r in results}
    assert by_name["read_tool"].success
    assert not by_name["write_tool"].success
    assert "not in allowed_tools" in (by_name["write_tool"].error or "")
