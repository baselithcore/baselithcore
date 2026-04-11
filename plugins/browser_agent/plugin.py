"""Official Browser Agent plugin."""

from __future__ import annotations

from typing import Any

from core.plugins import Plugin

from .agent import BrowserAgent
from .tools import build_browser_tool_definitions


class BrowserAgentPlugin(Plugin):
    """Plugin exposing browser automation capabilities."""

    def __init__(self) -> None:
        super().__init__()
        self._agent: BrowserAgent | None = None

    async def initialize(self, config: dict[str, Any]) -> None:
        await super().initialize(config)

    async def shutdown(self) -> None:
        if self._agent is not None:
            await self._agent.stop()
            self._agent = None
        await super().shutdown()

    def create_agent(self, **kwargs: Any) -> BrowserAgent:
        """Create a browser agent instance."""
        return BrowserAgent(**kwargs)

    def get_mcp_tools(self) -> list[dict[str, Any]]:
        """Expose browser tools to the core MCP server."""
        return build_browser_tool_definitions()
