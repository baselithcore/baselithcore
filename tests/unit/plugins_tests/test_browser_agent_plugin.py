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
