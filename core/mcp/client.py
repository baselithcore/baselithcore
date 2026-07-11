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

from core.config import get_mcp_config
from core.observability.logging import get_logger

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

    Supports stdio transport for local Python/Node.js servers and the
    Streamable HTTP transport (spec 2025-06-18) for remote servers via
    ``url=``.

    Example:
        async with MCPClient("./tools/weather_server.py") as client:
            tools = await client.list_tools()
            result = await client.call_tool("get_weather", {"city": "Rome"})

        async with MCPClient(url="https://host/mcp",
                             http_headers={"Authorization": "Bearer <t>"}) as c:
            tools = await c.list_tools()
    """

    def __init__(
        self,
        server_script: str | None = None,
        command: list[str] | None = None,
        url: str | None = None,
        http_headers: dict[str, str] | None = None,
    ) -> None:
        """
        Initialize MCP client.

        Args:
            server_script: Path to server script (.py or .js)
            command: Custom command to run (overrides server_script)
            url: Streamable HTTP endpoint of a remote MCP server
                (takes precedence over script/command)
            http_headers: Static headers for the HTTP transport
                (e.g. ``{"Authorization": "Bearer <token>"}``)
        """
        self.server_script = server_script
        self.command = command
        self.url = url
        self.http_headers = http_headers
        self._http: Any | None = None
        self._process: asyncio.subprocess.Process | None = None
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._request_id = 0
        self._server_info: MCPServerInfo | None = None
        self._connected = False

    @staticmethod
    def _validate_command(cmd: list[str]) -> None:
        """Reject custom commands whose executable is not allowlisted.

        Compares the basename of ``cmd[0]`` (case-insensitive, ``.exe``
        stripped, version suffixes like ``python3.12`` normalized) against
        ``MCPConfig.allowed_command_basenames``. The current interpreter
        (``sys.executable``) is always permitted.

        Raises:
            ValueError: When the command is empty or not allowlisted.
        """
        if not cmd or not cmd[0]:
            raise ValueError("MCP command must not be empty")
        executable = cmd[0]
        if executable == sys.executable:
            return
        basename = os.path.basename(executable).lower()
        if basename.endswith(".exe"):
            basename = basename[: -len(".exe")]
        allowed = get_mcp_config().allowed_command_basenames
        # Accept versioned interpreter names (python3.12, node22) by also
        # checking the alphabetic prefix.
        prefix = basename.rstrip("0123456789.")
        if basename not in allowed and prefix not in allowed:
            raise ValueError(
                f"MCP command '{executable}' is not in the allowed command "
                f"list ({sorted(allowed)}). Set MCP_ALLOWED_COMMANDS to "
                "extend the allowlist if this binary is trusted."
            )

    async def connect(
        self,
        server_script: str | None = None,
        command: list[str] | None = None,
        env: dict[str, str] | None = None,
        url: str | None = None,
    ) -> MCPServerInfo:
        """
        Connect to an MCP server.

        Args:
            server_script: Path to server script (overrides constructor)
            command: Custom command to run (overrides script and constructor)
            env: Environment variables to pass to the server process
            url: Streamable HTTP endpoint (overrides constructor)

        Returns:
            Server information after handshake
        """
        target_url = url or self.url
        if target_url:
            return await self._connect_http(target_url)

        cmd = command or self.command
        script = server_script or self.server_script

        if not cmd and not script:
            raise ValueError("No server script or command provided")

        if not cmd:
            if not script:
                raise ValueError("No server script or command provided")

            # Determine command based on file extension
            if script.endswith(".py"):
                cmd = [sys.executable, script]
            elif script.endswith(".js"):
                cmd = ["node", script]
            else:
                raise ValueError(
                    "Server script must be .py or .js file (or provide a custom command)"
                )
        else:
            # Custom commands can come from plugin manifests or operator
            # config — never exec an arbitrary binary. Only interpreters in
            # the MCP_ALLOWED_COMMANDS allowlist may be spawned.
            self._validate_command(cmd)

        logger.info("mcp_client_connecting", command=cmd)

        # Merge with current process environment if env is provided
        process_env = os.environ.copy()
        if env:
            process_env.update(env)
        process_env["PYTHONUNBUFFERED"] = "1"  # Ensure Python output is unbuffered

        # Start the server process
        self._process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=sys.stderr,  # Redirect stderr to parent stderr for easier debugging
            env=process_env,
        )

        if self._process.stdout is None or self._process.stdin is None:
            raise RuntimeError("Failed to open process pipes")

        self._reader = self._process.stdout
        self._writer = self._process.stdin

        # Perform MCP handshake
        config = get_mcp_config()

        from core.mcp.handlers import LATEST_PROTOCOL_VERSION

        init_response = await self._send_request(
            "initialize",
            {
                "protocolVersion": LATEST_PROTOCOL_VERSION,
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

    async def _connect_http(self, url: str) -> MCPServerInfo:
        """Connect over the Streamable HTTP transport (spec 2025-06-18)."""
        from core.mcp.handlers import LATEST_PROTOCOL_VERSION
        from core.mcp.http_client_transport import HTTPClientTransport

        config = get_mcp_config()
        transport = HTTPClientTransport(url, headers=self.http_headers)
        self._http = transport
        try:
            init_result = await transport.initialize(
                {
                    "protocolVersion": LATEST_PROTOCOL_VERSION,
                    "clientInfo": {
                        "name": config.mcp_server_name,
                        "version": config.mcp_server_version,
                    },
                    "capabilities": {},
                }
            )
            await transport.send(
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/initialized",
                    "params": {},
                }
            )
        except Exception:
            await transport.close()
            self._http = None
            raise

        server_info = init_result.get("serverInfo", {})
        self._server_info = MCPServerInfo(
            name=server_info.get("name", "unknown"),
            version=server_info.get("version", "unknown"),
            capabilities=init_result.get("capabilities", {}),
        )
        self._connected = True
        logger.info(
            "mcp_client_connected",
            transport="http",
            url=url,
            server_name=self._server_info.name,
        )
        return self._server_info

    async def disconnect(self) -> None:
        """Disconnect from the MCP server."""
        if self._http is not None:
            await self._http.close()
            self._http = None
        if self._process:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except TimeoutError:
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
            # External MCP servers are untrusted: scan tool output for indirect
            # prompt injection before it enters the agent's context. Log-only by
            # default (additive); sanitizes when BASELITH_SANITIZE_EXTERNAL_CONTENT.
            from core.guardrails import scan_external_content

            text = scan_external_content(text, source=f"mcp_tool:{name}")
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
        self._request_id += 1
        request = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            "params": params,
        }

        if self._http is not None:
            response = await self._http.send(request)
            if not isinstance(response, dict):
                raise RuntimeError(f"Empty MCP response for '{method}'")
            if "error" in response:
                error = response["error"]
                raise RuntimeError(
                    f"MCP error {error.get('code')}: {error.get('message')}"
                )
            return response.get("result", {})

        if self._writer is None or self._reader is None:
            raise RuntimeError("Not connected")

        # Send request
        request_line = json.dumps(request) + "\n"
        self._writer.write(request_line.encode())
        await self._writer.drain()

        # Read response, bounded by a timeout so a hung server cannot block the
        # agent loop indefinitely. The transport is single-flight stdio, so on
        # timeout we mark the connection unusable rather than risk consuming a
        # late reply as the answer to a subsequent request.
        timeout = get_mcp_config().mcp_client_request_timeout
        try:
            response_line = await asyncio.wait_for(
                self._reader.readline(), timeout=timeout
            )
        except TimeoutError as exc:
            self._connected = False
            raise RuntimeError(
                f"MCP server timed out after {timeout}s waiting for "
                f"response to '{method}'"
            ) from exc
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
        notification = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }

        if self._http is not None:
            await self._http.send(notification)
            return

        if self._writer is None:
            raise RuntimeError("Not connected")

        notification_line = json.dumps(notification) + "\n"
        self._writer.write(notification_line.encode())
        await self._writer.drain()


def __getattr__(name: str) -> Any:
    """PEP 562 lazy re-export: MCPConnectionPool moved to core.mcp.pool,
    which imports this module — an eager import here would be circular."""
    if name == "MCPConnectionPool":
        from core.mcp.pool import MCPConnectionPool

        return MCPConnectionPool
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "MCPClient",
    "MCPServerInfo",
    "MCPToolInfo",
]
