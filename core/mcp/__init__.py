"""
MCP (Model Context Protocol) Module.

Provides MCP Server and Client implementations for tool interoperability
with Claude Desktop, IDEs, and other MCP-compatible clients.
"""

from core.mcp.server import MCPServer
from core.mcp.client import MCPClient
from core.mcp.tools import MCPToolAdapter

__all__ = [
    "MCPServer",
    "MCPClient",
    "MCPToolAdapter",
]
