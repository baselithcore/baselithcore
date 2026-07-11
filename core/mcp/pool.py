"""Connection pool for multiple MCP servers.

Split out of ``client.py`` to respect the module size cap;
``MCPConnectionPool`` is re-exported from :mod:`core.mcp.client` for
backward compatibility.
"""

from __future__ import annotations

import asyncio
from typing import Any

from core.mcp.client import MCPClient, MCPServerInfo, MCPToolInfo
from core.observability.logging import get_logger

logger = get_logger(__name__)


class MCPConnectionPool:
    """
    Pool of MCP client connections for managing multiple servers.

    Example:
        pool = MCPConnectionPool()
        await pool.add_server("weather", "./weather_server.py")
        await pool.add_server("database", "./db_server.py")

        result = await pool.call_tool("weather", "get_forecast", {...})
    """

    def __init__(self) -> None:
        """Initialize connection pool."""
        self._clients: dict[str, MCPClient] = {}
        self._lock = asyncio.Lock()

    async def add_server(self, name: str, server_script: str) -> MCPServerInfo:
        """Add and connect to a server."""
        async with self._lock:
            if name in self._clients:
                raise ValueError(f"Server '{name}' already exists")

            client = MCPClient(server_script)
            info = await client.connect()
            self._clients[name] = client

            return info

    async def remove_server(self, name: str) -> None:
        """Disconnect and remove a server."""
        async with self._lock:
            if name not in self._clients:
                return

            await self._clients[name].disconnect()
            del self._clients[name]

    def get_client(self, name: str) -> MCPClient:
        """Get a client by server name."""
        if name not in self._clients:
            raise KeyError(f"Server '{name}' not found")
        return self._clients[name]

    async def call_tool(
        self, server_name: str, tool_name: str, arguments: dict[str, Any] | None = None
    ) -> Any:
        """Call a tool on a specific server."""
        client = self.get_client(server_name)
        return await client.call_tool(tool_name, arguments)

    async def list_all_tools(self) -> dict[str, list[MCPToolInfo]]:
        """List tools from all connected servers concurrently.

        Servers are queried in parallel. A failure on one server is logged and
        yields an empty tool list for that server rather than aborting the
        whole call.
        """
        if not self._clients:
            return {}
        names = list(self._clients.keys())
        outcomes = await asyncio.gather(
            *(self._clients[name].list_tools() for name in names),
            return_exceptions=True,
        )
        result: dict[str, list[MCPToolInfo]] = {}
        for name, outcome in zip(names, outcomes):
            if isinstance(outcome, BaseException):
                logger.warning(
                    "mcp_list_tools_failed", server_name=name, error=str(outcome)
                )
                result[name] = []
            else:
                result[name] = outcome
        return result

    async def close_all(self) -> None:
        """Close all connections."""
        for client in self._clients.values():
            await client.disconnect()
        self._clients.clear()

    async def __aenter__(self) -> MCPConnectionPool:
        """Async context manager entry."""
        return self

    async def __aexit__(self, *args: Any) -> None:
        """Async context manager exit."""
        await self.close_all()


__all__ = ["MCPConnectionPool"]
