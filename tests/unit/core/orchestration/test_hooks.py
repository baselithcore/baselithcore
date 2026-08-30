"""Tests for the tool-invocation hook bus."""

from __future__ import annotations

import pytest
from core.orchestration.hooks import (
    ToolHookEvent,
    ToolHookRegistry,
    get_tool_hook_registry,
    reset_tool_hook_registry,
)

pytestmark = [pytest.mark.unit]


def _event(tool: str = "search", phase: str = "pre") -> ToolHookEvent:
    return ToolHookEvent(
        tool_name=tool,
        category="read_only",
        phase=phase,
        tenant_id="t1",
        args_digest="abc",
    )


class TestRegistry:
    async def test_pre_hook_receives_matching_event(self):
        registry = ToolHookRegistry()
        seen: list[ToolHookEvent] = []

        async def hook(event: ToolHookEvent) -> None:
            seen.append(event)

        registry.register("pre", "search", hook)
        await registry.dispatch_pre(_event("search"))
        assert len(seen) == 1
        assert seen[0].tool_name == "search"

    async def test_matcher_supports_glob(self):
        registry = ToolHookRegistry()
        seen: list[str] = []

        async def hook(event: ToolHookEvent) -> None:
            seen.append(event.tool_name)

        registry.register("pre", "db_*", hook)
        await registry.dispatch_pre(_event("db_query"))
        await registry.dispatch_pre(_event("web_fetch"))
        assert seen == ["db_query"]

    async def test_pre_hook_exception_propagates(self):
        """A pre-hook that raises blocks the invocation (fail-closed)."""
        registry = ToolHookRegistry()

        async def deny(event: ToolHookEvent) -> None:
            raise PermissionError("blocked by policy")

        registry.register("pre", "*", deny)
        with pytest.raises(PermissionError):
            await registry.dispatch_pre(_event())

    async def test_post_hook_exception_swallowed(self):
        """Post-hooks are observers: a broken one never breaks the loop."""
        registry = ToolHookRegistry()
        seen: list[str] = []

        async def broken(event: ToolHookEvent) -> None:
            raise RuntimeError("boom")

        async def working(event: ToolHookEvent) -> None:
            seen.append(event.tool_name)

        registry.register("post", "*", broken)
        registry.register("post", "*", working)
        await registry.dispatch_post(_event(phase="post"))
        assert seen == ["search"]

    async def test_empty_registry_is_noop(self):
        registry = ToolHookRegistry()
        await registry.dispatch_pre(_event())
        await registry.dispatch_post(_event(phase="post"))


class TestDefaultRegistry:
    def test_singleton_and_reset(self):
        reset_tool_hook_registry()
        first = get_tool_hook_registry()
        assert get_tool_hook_registry() is first
        reset_tool_hook_registry()
        assert get_tool_hook_registry() is not first
