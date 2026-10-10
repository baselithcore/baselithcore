"""Official Coding Agent plugin."""

from __future__ import annotations

from typing import Any

from baselith.core.plugins import Plugin

from .agent import CodingAgent
from .tools import build_coding_tool_definitions


class CodingAgentPlugin(Plugin):
    """Plugin exposing coding agent capabilities."""

    async def initialize(self, config: dict[str, Any]) -> None:
        """Activate, then register the agent in the AI-system inventory (never fatal)."""
        await super().initialize(config)
        from .compliance import register_ai_system

        await register_ai_system(str(self.metadata.version or ""))

    def create_agent(self, **kwargs: Any) -> CodingAgent:
        """Create a coding agent instance."""
        return CodingAgent(**kwargs)

    def get_mcp_tools(self) -> list[dict[str, Any]]:
        """Expose coding tools to the core MCP server."""
        return build_coding_tool_definitions()
