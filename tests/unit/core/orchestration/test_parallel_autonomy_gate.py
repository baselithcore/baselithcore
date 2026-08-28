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

    executor.register_tool("read_tool", read_tool, category="read_only")
    executor.register_tool("write_tool", write_tool, category="mutating")
    return executor


async def test_default_policy_is_supervised_and_gates_mutating() -> None:
    """No explicit policy => fail-closed SUPERVISED default: mutating tools
    are gated, read-only tools still run."""
    executor = _executor()
    results = await executor.execute_parallel(
        [ToolCall(tool_name="write_tool"), ToolCall(tool_name="read_tool")]
    )
    assert not results[0].success
    assert "requires human approval" in (results[0].error or "")
    assert results[1].success
    assert results[1].result == "read-ok"


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


async def test_undeclared_category_gated_when_supervised() -> None:
    """A tool registered without an explicit category defaults to the most
    restrictive category (destructive) and is gated — an omitted category must
    fail safe, never wave the tool through unsupervised."""
    executor = ParallelToolExecutor(
        autonomy_policy=AutonomyPolicy(level=AutonomyLevel.SUPERVISED)
    )

    async def mystery_tool() -> str:
        return "did-something"

    executor.register_tool("mystery_tool", mystery_tool)  # no category declared
    call = ToolCall(tool_name="mystery_tool")
    results = await executor.execute_parallel([call])

    assert not results[0].success
    assert "requires human approval" in (results[0].error or "")
    assert call.status is ToolStatus.SKIPPED


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


async def test_pending_approval_does_not_hold_concurrency_slot() -> None:
    """A tool awaiting a human approval must NOT occupy a concurrency slot.

    With max_parallel=1 and the approval gate holding the semaphore, a single
    pending approval would stall every other tool call (a practical deadlock in
    SUPERVISED mode). The gate now runs outside the semaphore, so a read-only
    tool executes while a mutating tool's approval is still pending.
    """
    import asyncio

    release = asyncio.Event()
    read_started = asyncio.Event()

    class _BlockingHuman:
        async def request_approval(self, description, timeout=None, context=None):
            await release.wait()  # block until the test releases the approval
            return True

    executor = ParallelToolExecutor(
        autonomy_policy=AutonomyPolicy(level=AutonomyLevel.SUPERVISED),
        human_intervention=_BlockingHuman(),
        max_parallel=1,  # a single slot makes the stall observable
    )

    async def read_tool() -> str:
        read_started.set()
        return "read-ok"

    async def write_tool() -> str:
        return "write-ok"

    executor.register_tool("write_tool", write_tool, category="mutating")
    executor.register_tool("read_tool", read_tool, category="read_only")

    task = asyncio.ensure_future(
        executor.execute_parallel(
            [ToolCall(tool_name="write_tool"), ToolCall(tool_name="read_tool")]
        )
    )
    # read_tool must run even while write_tool's approval is still pending. If
    # the pending approval held the only slot, this would time out.
    await asyncio.wait_for(read_started.wait(), timeout=1.0)

    release.set()  # let the write approval resolve
    results = await asyncio.wait_for(task, timeout=1.0)
    by_name = {r.tool_name: r for r in results}
    assert by_name["read_tool"].success
    assert by_name["write_tool"].success


# --- loop budget + contract enforcement ------------------------------------


async def test_loop_budget_caps_tool_calls() -> None:
    from core.orchestration.limits import LoopBudget, LoopLimits

    budget = LoopBudget(limits=LoopLimits(max_tool_calls=1))
    executor = ParallelToolExecutor(loop_budget=budget)

    async def read_tool() -> str:
        return "ok"

    executor.register_tool("read_tool", read_tool, category="read_only")
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

    executor.register_tool("read_tool", read_tool, category="read_only")
    executor.register_tool("write_tool", write_tool, category="read_only")

    results = await executor.execute_parallel(
        [ToolCall(tool_name="read_tool"), ToolCall(tool_name="write_tool")]
    )
    by_name = {r.tool_name: r for r in results}
    assert by_name["read_tool"].success
    assert not by_name["write_tool"].success
    assert "not in allowed_tools" in (by_name["write_tool"].error or "")
