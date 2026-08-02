"""
MCP (Model Context Protocol) Module.

Provides MCP Server and Client implementations for tool interoperability
with Claude Desktop, IDEs, and other MCP-compatible clients. Transports:
stdio (local processes) and Streamable HTTP on both the server
(:func:`core.mcp.http_transport.create_mcp_http_router`) and client
(``MCPClient(url=...)``) sides. Both are dual-era: the stateless 2026-07-28
revision and the legacy ``initialize`` handshake down to 2024-11-05.
"""

from core.mcp.client import MCPClient, MCPToolError
from core.mcp.pool import MCPConnectionPool
from core.mcp.progress import report_progress
from core.mcp.server import MCPServer
from core.mcp.tools import MCPToolAdapter

__all__ = [
    "MCPClient",
    "MCPConnectionPool",
    "MCPServer",
    "MCPToolAdapter",
    "MCPToolError",
    "report_progress",
]
