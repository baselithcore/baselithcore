"""
Model Context Protocol (MCP) Integration Bridge.

Transforms internal BaselithCore capabilities into standardized MCP
endpoints. Enables seamless interoperability with third-party tools
(Claude Desktop, IDEs) by exposing tools and resources via regularized
JSON-RPC transports.
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

from core.config import get_mcp_config
from core.observability.logging import get_logger

from .handlers import MessageHandlerMixin
from .registration import RegistrationMixin
from .types import (
    MCPPrompt,
    MCPResource,
    MCPResourceTemplate,
    MCPServerInfo,
    MCPTool,
)

logger = get_logger(__name__)


class MCPServer(MessageHandlerMixin, RegistrationMixin):
    """
    Protocol adapter for external tool use.

    Implements the core MCP server specification, managing tool
    discovery, schema generation, and request routing. Supports stdio
    transport for local integration and is extensible for network-based
    transports like SSE.
    """

    def __init__(
        self,
        name: str | None = None,
        version: str | None = None,
        autonomy_policy: Any | None = None,
    ) -> None:
        """Initialize MCP Server.

        Args:
            name: Server name for identification (defaults to config)
            version: Server version string (defaults to config)
            autonomy_policy: Optional ``core.orchestration.autonomy.AutonomyPolicy``.
                When set, tool calls whose category requires approval at the
                policy's level are rejected (MCP transports have no human
                approval channel, so the gate is fail-closed).
        """
        config = get_mcp_config()
        self.config = config

        server_name = name or config.mcp_server_name
        server_version = version or config.mcp_server_version

        self.info = MCPServerInfo(name=server_name, version=server_version)
        self._tools: dict[str, MCPTool] = {}
        self._resources: dict[str, MCPResource] = {}
        self._resource_templates: dict[str, MCPResourceTemplate] = {}
        self._prompts: dict[str, MCPPrompt] = {}

        from core.mcp.subscriptions import SubscriptionHub
        from core.mcp.tasks import TaskStore

        self._tasks_store = TaskStore()
        self._subscriptions = SubscriptionHub()
        self._running = False
        self._request_id = 0
        self._autonomy_policy = autonomy_policy

        from core.mcp.mrtr import RequestStateSealer

        secret = config.mcp_request_state_secret
        self._state_sealer = RequestStateSealer(
            secret=secret.get_secret_value().encode() if secret else None,
            ttl_seconds=config.mcp_request_state_ttl_seconds,
        )

    def _announce(self, notifier: Any) -> None:
        """Fire a list-changed notification without blocking registration.

        Registration is synchronous and often happens at import time, before
        any event loop exists — and with no subscribers there is nothing to
        send. Both cases are no-ops rather than errors.
        """
        if not self._subscriptions.active:
            return
        try:
            asyncio.get_running_loop().create_task(notifier())
        except RuntimeError:
            logger.debug("mcp_list_changed_not_announced", reason="no running loop")

    # -------------------------------------------------------------------------
    # Tool Registration
    # -------------------------------------------------------------------------

    # -------------------------------------------------------------------------
    # Stdio Transport (for Claude Desktop)
    # -------------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        """Whether the serve loop should keep reading."""
        return self._running

    async def run_stdio(self) -> None:
        """
        Run the MCP server using stdio transport.

        This is the transport mode used by Claude Desktop. Requests are served
        concurrently and are cancellable via ``notifications/cancelled``.
        """
        from core.mcp.stdio_server import serve_stdio

        self._running = True
        await serve_stdio(self)

    async def run(self, transport: str = "stdio") -> None:
        """
        Run the MCP server.

        Args:
            transport: Transport type ("stdio" or "sse")
        """
        if transport == "stdio":
            await self.run_stdio()
        else:
            raise ValueError(f"Unsupported transport: {transport}")

    def stop(self) -> None:
        """Stop the MCP server."""
        self._running = False


# ============================================================================
# Default Server with Built-in Tools
# ============================================================================


def create_default_server() -> MCPServer:
    """
    Create an MCP server with default tools from the Baselith-Core.

    Returns:
        Configured MCPServer instance
    """
    server = MCPServer()

    @server.tool(
        name="echo",
        description="Echo back the input message",
        input_schema={
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "Message to echo"}
            },
            "required": ["message"],
        },
    )
    async def echo(message: str) -> str:
        return f"Echo: {message}"

    @server.tool(
        name="get_system_info",
        description="Get information about the Baselith-Core",
        input_schema={"type": "object", "properties": {}},
    )
    async def get_system_info() -> dict[str, Any]:
        return {
            "name": "Baselith-Core",
            "version": "2.0.0",
            "capabilities": [
                "RAG",
                "Knowledge Graph",
                "Tree of Thoughts",
                "Code Execution",
                "Web Scraping",
            ],
        }

    return server


# ============================================================================
# Entry Point
# ============================================================================


async def main() -> None:
    """Main entry point for running the MCP server."""
    from core.observability.setup import ensure_logging_configured

    ensure_logging_configured(stream=sys.stderr)

    server = create_default_server()
    await server.run(transport="stdio")


if __name__ == "__main__":
    asyncio.run(main())
