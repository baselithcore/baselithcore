"""
MCP (Model Context Protocol) Module.

Provides MCP Server and Client implementations for tool interoperability
with Claude Desktop, IDEs, and other MCP-compatible clients. Transports:
stdio (local processes) and Streamable HTTP on both the server
(:func:`baselith.core.mcp.http_transport.create_mcp_http_router`) and client
(``MCPClient(url=...)``) sides. Both are dual-era: the stateless 2026-07-28
revision and the legacy ``initialize`` handshake down to 2024-11-05.
"""

from baselith.core.mcp.client import MCPClient, MCPToolError
from baselith.core.mcp.declarative import make_mcp_tool_fns, mount_configured_servers
from baselith.core.mcp.pool import MCPConnectionPool
from baselith.core.mcp.progress import report_progress
from baselith.core.mcp.server import MCPServer
from baselith.core.mcp.tools import MCPToolAdapter

__all__ = [
    "MCPClient",
    "MCPConnectionPool",
    "MCPServer",
    "MCPToolAdapter",
    "MCPToolError",
    "make_mcp_tool_fns",
    "mount_configured_servers",
    "report_progress",
]
