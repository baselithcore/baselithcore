"""
MCP (Model Context Protocol) Module.

Provides MCP Server and Client implementations for tool interoperability
with Claude Desktop, IDEs, and other MCP-compatible clients. Transports:
stdio (local processes) and Streamable HTTP (spec 2025-06-18) on both the
server (:func:`core.mcp.http_transport.create_mcp_http_router`) and client
(``MCPClient(url=...)``) sides.
"""

from core.mcp.client import MCPClient
from core.mcp.pool import MCPConnectionPool
from core.mcp.server import MCPServer
from core.mcp.tools import MCPToolAdapter

__all__ = [
    "MCPClient",
    "MCPConnectionPool",
    "MCPServer",
    "MCPToolAdapter",
]
