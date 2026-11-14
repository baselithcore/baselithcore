"""ParallelToolExecutor uses the single tool-enforcement chokepoint.

Regression: the executor re-implemented three of the chokepoint's controls by
hand (agent contract, autonomy approval, tool-call budget) and skipped the
rest -- veto-capable pre-hooks, the plugin capability check, the tool rate
limit and the audit trail -- while ``enforcement.py`` advertised the executor
as one of its callers. A pre-hook that vetoes a tool in a sequential loop was
silently ignored in a parallel one.
"""

from __future__ import annotations

from baselith.core.orchestration.autonomy import AutonomyLevel, AutonomyPolicy
from baselith.core.orchestration.hooks import ToolHookEvent, ToolHookRegistry
from baselith.core.orchestration.parallel import (
    ParallelToolExecutor,
    ToolCall,
    ToolStatus,
)


def _executor(hooks: ToolHookRegistry) -> tuple[ParallelToolExecutor, list[str]]:
    ran: list[str] = []
    executor = ParallelToolExecutor(
        autonomy_policy=AutonomyPolicy(level=AutonomyLevel.FULLY_AUTONOMOUS),
        tool_hooks=hooks,
    )

    async def lookup() -> str:
        ran.append("lookup")
        return "lookup-ok"

    executor.register_tool("lookup", lookup, category="read_only")
    return executor, ran


async def test_pre_hook_veto_blocks_the_parallel_call() -> None:
    hooks = ToolHookRegistry()

    async def veto(event: ToolHookEvent) -> None:
        raise PermissionError(f"{event.tool_name} vetoed by policy")

    hooks.register("pre", "lookup", veto)
    executor, ran = _executor(hooks)
    call = ToolCall(tool_name="lookup")

    results = await executor.execute_parallel([call])

    assert not results[0].success
    assert "vetoed by policy" in (results[0].error or "")
    assert call.status is ToolStatus.SKIPPED
    assert ran == []


async def test_pre_hook_sees_the_registered_category() -> None:
    hooks = ToolHookRegistry()
    seen: list[ToolHookEvent] = []

    async def observe(event: ToolHookEvent) -> None:
        seen.append(event)

    hooks.register("pre", "*", observe)
    executor, ran = _executor(hooks)

    results = await executor.execute_parallel([ToolCall(tool_name="lookup")])

    assert results[0].success
    assert ran == ["lookup"]
    assert [(e.tool_name, e.category, e.phase) for e in seen] == [
        ("lookup", "read_only", "pre")
    ]
