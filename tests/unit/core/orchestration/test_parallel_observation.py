"""The parallel executor must apply the same observation hygiene as the
sequential ReAct path: truncate, sanitize, envelope — and fire post-hooks.

``_execute_single`` sanitized but never truncated, so one oversized parallel
tool result could still overflow the next reasoning turn's context window,
and no ``post`` hook ever fired from core: the deterministic side-effect bus
was write-only.
"""

from __future__ import annotations

import pytest

from core.orchestration.hooks import (
    ToolHookEvent,
    ToolHookRegistry,
    reset_tool_hook_registry,
)
from core.orchestration.parallel import ParallelToolExecutor, ToolCall
from core.orchestration.tool_output import DEFAULT_TOOL_OUTPUT_MAX_CHARS

pytestmark = [pytest.mark.unit]


@pytest.fixture(autouse=True)
def _clean_hook_registry():
    reset_tool_hook_registry()
    yield
    reset_tool_hook_registry()


def _executor() -> ParallelToolExecutor:
    return ParallelToolExecutor()


async def test_oversized_result_is_truncated() -> None:
    executor = _executor()

    async def dump() -> str:
        return "x" * (DEFAULT_TOOL_OUTPUT_MAX_CHARS * 3)

    executor.register_tool("dump", dump, category="read_only")
    results = await executor.execute_parallel([ToolCall(tool_name="dump")])

    assert results[0].success is True
    assert "[truncated" in results[0].result
    assert len(results[0].result) < DEFAULT_TOOL_OUTPUT_MAX_CHARS * 3


async def test_non_string_result_is_left_alone() -> None:
    executor = _executor()

    async def rows() -> list[int]:
        return [1, 2, 3]

    executor.register_tool("rows", rows, category="read_only")
    results = await executor.execute_parallel([ToolCall(tool_name="rows")])
    assert results[0].result == [1, 2, 3]


async def test_post_hooks_fire_with_the_outcome() -> None:
    seen: list[ToolHookEvent] = []

    async def hook(event: ToolHookEvent) -> None:
        seen.append(event)

    registry = ToolHookRegistry()
    registry.register("post", "*", hook)
    executor = ParallelToolExecutor(tool_hooks=registry)

    async def ok_tool() -> str:
        return "fine"

    executor.register_tool("ok_tool", ok_tool, category="read_only")
    await executor.execute_parallel([ToolCall(tool_name="ok_tool")])

    assert len(seen) == 1
    assert seen[0].tool_name == "ok_tool"
    assert seen[0].phase == "post"
    assert seen[0].category == "read_only"
    assert seen[0].metadata["ok"] is True


async def test_post_hook_fires_for_a_failed_tool() -> None:
    seen: list[ToolHookEvent] = []

    async def hook(event: ToolHookEvent) -> None:
        seen.append(event)

    registry = ToolHookRegistry()
    registry.register("post", "*", hook)
    executor = ParallelToolExecutor(tool_hooks=registry)

    async def broken() -> str:
        raise ValueError("boom")

    executor.register_tool("broken", broken, category="read_only")
    results = await executor.execute_parallel([ToolCall(tool_name="broken")])

    assert results[0].success is False
    assert len(seen) == 1
    assert seen[0].metadata["ok"] is False


async def test_broken_post_hook_never_breaks_the_tool() -> None:
    registry = ToolHookRegistry()

    async def hook(event: ToolHookEvent) -> None:
        raise RuntimeError("observer exploded")

    registry.register("post", "*", hook)
    executor = ParallelToolExecutor(tool_hooks=registry)

    async def ok_tool() -> str:
        return "fine"

    executor.register_tool("ok_tool", ok_tool, category="read_only")
    results = await executor.execute_parallel([ToolCall(tool_name="ok_tool")])
    assert results[0].success is True
    assert results[0].result == "fine"
