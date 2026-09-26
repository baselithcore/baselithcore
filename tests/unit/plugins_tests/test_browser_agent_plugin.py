from core.agents.browser_agent import BrowserAgent as CoreBrowserAgent
from core.agents.browser_tools import (
    register_browser_tools as core_register_browser_tools,
)
from core.agents.browser_types import BrowserAgentResult as CoreBrowserAgentResult
from plugins.browser_agent import BrowserAgent, register_browser_tools
from plugins.browser_agent.plugin import BrowserAgentPlugin
from plugins.browser_agent.types import BrowserAgentResult


def test_legacy_core_imports_resolve_to_plugin_exports() -> None:
    assert CoreBrowserAgent is BrowserAgent
    assert core_register_browser_tools is register_browser_tools
    assert CoreBrowserAgentResult is BrowserAgentResult


def test_browser_agent_plugin_exposes_manifest_metadata() -> None:
    plugin = BrowserAgentPlugin()

    # Manifest name must match the plugin directory (directory-name parity).
    assert plugin.metadata.name == "browser_agent"
    assert "browser" in plugin.metadata.tags


def test_browser_agent_plugin_exposes_mcp_tools() -> None:
    plugin = BrowserAgentPlugin()

    tools = plugin.get_mcp_tools()
    tool_names = {tool["name"] for tool in tools}

    assert "browser_navigate" in tool_names
    assert "browser_execute_task" in tool_names


class TestWaitActionIsBounded:
    """A ``wait`` duration is model output steered by page content, so a
    prompt-injected value must not park the task (and its browser) for hours."""

    def test_clamp_wait_seconds_bounds(self) -> None:
        import math

        from plugins.browser_agent.actions import (
            MAX_WAIT_SECONDS,
            clamp_wait_seconds,
        )

        assert clamp_wait_seconds("1e9") == MAX_WAIT_SECONDS
        assert clamp_wait_seconds("-5") == 0.0
        assert clamp_wait_seconds("nan") == 1.0
        assert clamp_wait_seconds("inf") == 1.0
        assert clamp_wait_seconds("soon") == 1.0
        assert clamp_wait_seconds(None) == 1.0
        assert math.isclose(clamp_wait_seconds("2.5"), 2.5)

    async def test_execute_action_sleeps_at_most_the_cap(self, monkeypatch) -> None:
        from unittest.mock import AsyncMock, MagicMock

        import plugins.browser_agent.agent as agent_module
        from plugins.browser_agent.actions import MAX_WAIT_SECONDS
        from plugins.browser_agent.types import BrowserAction, BrowserActionType

        sleep = AsyncMock()
        monkeypatch.setattr(agent_module.asyncio, "sleep", sleep)
        agent = BrowserAgent(vision_service=MagicMock())
        agent._page = MagicMock()
        ok = await agent.execute_action(
            BrowserAction(action_type=BrowserActionType.WAIT, value="1000000000")
        )
        assert ok is True
        sleep.assert_awaited_once_with(MAX_WAIT_SECONDS)
