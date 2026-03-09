"""
MCP Client Implementation.

Allows the Baselith-Core to consume tools from external MCP servers.

Usage:
    from core.mcp import MCPClient

    async with MCPClient("path/to/server.py") as client:
        tools = await client.list_tools()
        result = await client.call_tool("tool_name", {"arg": "value"})
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import dataclass
from typing import Any

from core.observability.logging import get_logger
from core.config import get_mcp_config

logger = get_logger(__name__)


@dataclass
class MCPToolInfo:
    """Information about an MCP tool."""

    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass
class MCPServerInfo:
    """Information about a connected MCP server."""

    name: str
    version: str
    capabilities: dict[str, Any]


class MCPClient:
    """
    Client for connecting to MCP servers.

    Supports stdio transport for local Python/Node.js servers.

    Example:
        async with MCPClient("./tools/weather_server.py") as client:
            tools = await client.list_tools()
            result = await client.call_tool("get_weather", {"city": "Rome"})
    """

    def __init__(self, server_script: str | None = None) -> None:
        """
        Initialize MCP client.

        Args:
            server_script: Path to server script (.py or .js)
        """
        self.server_script = server_script
        self._process: asyncio.subprocess.Process | None = None
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._request_id = 0
        self._server_info: MCPServerInfo | None = None
        self._connected = False

    async def connect(self, server_script: str | None = None) -> MCPServerInfo:
        """
        Connect to an MCP server.

        Args:
            server_script: Path to server script (overrides constructor)

        Returns:
            Server information after handshake
        """
        script = server_script or self.server_script
        if not script:
            raise ValueError("No server script provided")

        # Determine command based on file extension
        if script.endswith(".py"):
            command = [sys.executable, script]
        elif script.endswith(".js"):
            command = ["node", script]
        else:
            raise ValueError("Server script must be .py or .js file")

        logger.info("mcp_client_connecting", script=script)

        # Start the server process
        self._process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )

        if self._process.stdout is None or self._process.stdin is None:
            raise RuntimeError("Failed to open process pipes")

        self._reader = self._process.stdout
        self._writer = self._process.stdin

        # Perform MCP handshake
        config = get_mcp_config()

        init_response = await self._send_request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "clientInfo": {
                    "name": config.mcp_server_name,  # Reuse server name? Or add client name to config?
                    "version": config.mcp_server_version,
                },
                "capabilities": {},
            },
        )

        server_info = init_response.get("serverInfo", {})
        self._server_info = MCPServerInfo(
            name=server_info.get("name", "unknown"),
            version=server_info.get("version", "unknown"),
            capabilities=init_response.get("capabilities", {}),
        )

        # Send initialized notification
        await self._send_notification("notifications/initialized", {})

        self._connected = True
        logger.info(
            "mcp_client_connected",
            server_name=self._server_info.name,
            server_version=self._server_info.version,
        )

        return self._server_info

    async def disconnect(self) -> None:
        """Disconnect from the MCP server."""
        if self._process:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self._process.kill()
            self._process = None

        self._reader = None
        self._writer = None
        self._connected = False
        logger.info("mcp_client_disconnected")

    async def __aenter__(self) -> MCPClient:
        """Async context manager entry."""
        await self.connect()
        return self

    async def __aexit__(self, *args: Any) -> None:
        """Async context manager exit."""
        await self.disconnect()

    # -------------------------------------------------------------------------
    # Tool Operations
    # -------------------------------------------------------------------------

    async def list_tools(self) -> list[MCPToolInfo]:
        """
        List available tools from the server.

        Returns:
            List of tool information
        """
        self._ensure_connected()

        response = await self._send_request("tools/list", {})
        tools = response.get("tools", [])

        return [
            MCPToolInfo(
                name=t["name"],
                description=t.get("description", ""),
                input_schema=t.get("inputSchema", {}),
            )
            for t in tools
        ]

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> Any:
        """
        Call a tool on the server.

        Args:
            name: Tool name
            arguments: Tool arguments

        Returns:
            Tool result
        """
        self._ensure_connected()

        response = await self._send_request(
            "tools/call",
            {
                "name": name,
                "arguments": arguments or {},
            },
        )

        # Extract content from response
        content = response.get("content", [])
        if not content:
            return None

        # Return text content if single item
        if len(content) == 1 and content[0].get("type") == "text":
            text = content[0].get("text", "")
            # Try to parse as JSON
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return text

        return content

    # -------------------------------------------------------------------------
    # Resource Operations
    # -------------------------------------------------------------------------

    async def list_resources(self) -> list[dict[str, Any]]:
        """List available resources from the server."""
        self._ensure_connected()

        response = await self._send_request("resources/list", {})
        return response.get("resources", [])

    async def read_resource(self, uri: str) -> Any:
        """Read a resource from the server."""
        self._ensure_connected()

        response = await self._send_request("resources/read", {"uri": uri})
        contents = response.get("contents", [])

        if contents and len(contents) == 1:
            return contents[0].get("text")

        return contents

    # -------------------------------------------------------------------------
    # Internal Methods
    # -------------------------------------------------------------------------

    def _ensure_connected(self) -> None:
        """Ensure the client is connected."""
        if not self._connected:
            raise RuntimeError("Not connected to MCP server. Call connect() first.")

    async def _send_request(
        self, method: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        """Send a JSON-RPC request and wait for response."""
        if self._writer is None or self._reader is None:
            raise RuntimeError("Not connected")

        self._request_id += 1
        request = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            "params": params,
        }

        # Send request
        request_line = json.dumps(request) + "\n"
        self._writer.write(request_line.encode())
        await self._writer.drain()

        # Read response
        response_line = await self._reader.readline()
        if not response_line:
            raise RuntimeError("Server closed connection")

        response = json.loads(response_line.decode().strip())

        # Check for error
        if "error" in response:
            error = response["error"]
            raise RuntimeError(f"MCP error {error.get('code')}: {error.get('message')}")

        return response.get("result", {})

    async def _send_notification(self, method: str, params: dict[str, Any]) -> None:
        """Send a JSON-RPC notification (no response expected)."""
        if self._writer is None:
            raise RuntimeError("Not connected")

        notification = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }

        notification_line = json.dumps(notification) + "\n"
        self._writer.write(notification_line.encode())
        await self._writer.drain()


# ============================================================================
# Connection Pool for Multiple Servers
# ============================================================================


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
        """List tools from all connected servers."""
        result: dict[str, list[MCPToolInfo]] = {}
        for name, client in self._clients.items():
            result[name] = await client.list_tools()
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
