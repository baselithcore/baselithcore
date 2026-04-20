"""MCP tool aggregation for ``BaselithbotPlugin.get_mcp_tools``.

Kept separate so the plugin class stays focused on lifecycle and state,
while the fan-out across tool-definition builders lives in one readable
module.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .computer_tools import build_computer_tool_definitions
from .extra_tools import build_extra_tool_definitions
from .openclaw_tools import build_openclaw_tool_definitions
from .som import build_som_tool_definition
from .tools import build_baselithbot_tool_definitions

if TYPE_CHECKING:
    from .plugin import BaselithbotPlugin


def collect_mcp_tools(plugin: BaselithbotPlugin) -> list[dict[str, Any]]:
    """Gather every MCP tool the plugin currently advertises."""
    browser_tools = build_baselithbot_tool_definitions(agent_factory=lambda: plugin.create_agent())
    cu_config = plugin.effective_computer_use_config()
    computer_tools = build_computer_tool_definitions(cu_config, approvals=plugin.approvals)
    openclaw_tools = build_openclaw_tool_definitions(
        channels=plugin.channels,
        sessions=plugin.sessions,
        chat_commands=plugin.chat_commands,
        skills=plugin.skills,
        cron=plugin.cron,
        pairing=plugin.pairing,
        canvas=plugin.canvas,
    )
    extra_tools = build_extra_tool_definitions(
        config=cu_config,
        usage=plugin.usage,
        workspaces=plugin.workspaces,
        agents=plugin.agent_registry,
        approvals=plugin.approvals,
    )
    som_tool = build_som_tool_definition(plugin)
    return [
        *browser_tools,
        *computer_tools,
        *openclaw_tools,
        *extra_tools,
        som_tool,
    ]


__all__ = ["collect_mcp_tools"]
