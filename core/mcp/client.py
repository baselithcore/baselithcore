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
from dataclasses import dataclass
from typing import Any

from core.config import get_mcp_config
from core.mcp.client_handshake import HandshakeMixin
from core.mcp.modern import MODERN_PROTOCOL_VERSION, client_request_meta
from core.mcp.stdio_client_transport import (
    read_response,
    resolve_command,
    spawn,
    validate_command,
    write_message,
)
from core.observability.logging import get_logger

logger = get_logger(__name__)

# Modern protocol versions this client can drive, newest first. Legacy
# revisions are reached through the `initialize` handshake instead.
MODERN_CLIENT_VERSIONS = (MODERN_PROTOCOL_VERSION,)


def _error_text(result: dict[str, Any]) -> str:
    """Join the text blocks of an ``isError`` tools/call result."""
    return "\n".join(
        block.get("text", "")
        for block in result.get("content", [])
        if isinstance(block, dict) and block.get("type") == "text"
    ).strip()


class MCPToolError(RuntimeError):
    """A ``tools/call`` that completed but reported ``isError: true``.

    Distinct from transport/protocol failures: the server executed the tool and
    the tool itself failed, so the message is meant to be surfaced to the model
    for self-correction rather than treated as a broken connection.
    """


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


class MCPClient(HandshakeMixin):
    """
    Client for connecting to MCP servers.

    Supports stdio transport for local Python/Node.js servers and the
    Streamable HTTP transport for remote servers via ``url=``. Dual-era:
    the protocol era is probed on connect (see
    :class:`~core.mcp.client_handshake.HandshakeMixin`).

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
        # Set once the era probe finds a mutually supported modern version;
        # None means the server is legacy and wants the `initialize` handshake.
        self._protocol_version: str | None = None
        self._client_info: dict[str, Any] = {}

    # Kept as a staticmethod for call sites that gate a command before
    # constructing a client; the implementation lives with the transport.
    _validate_command = staticmethod(validate_command)

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

        # A custom command can come from a plugin manifest or operator config —
        # `resolve_command` allowlists it before anything is executed.
        cmd = resolve_command(
            server_script or self.server_script, command or self.command
        )

        logger.info("mcp_client_connecting", command=cmd)

        self._process = await spawn(cmd, env)

        if self._process.stdout is None or self._process.stdin is None:
            raise RuntimeError("Failed to open process pipes")

        self._reader = self._process.stdout
        self._writer = self._process.stdin

        return await self.handshake()

    async def _connect_http(self, url: str) -> MCPServerInfo:
        """Connect over the Streamable HTTP transport."""
        from core.mcp.http_client_transport import HTTPClientTransport

        transport = HTTPClientTransport(url, headers=self.http_headers)
        self._http = transport
        try:
            # Same era probe as stdio: a modern server answers server/discover
            # and needs no handshake; a legacy one rejects it and gets one.
            info = await self.handshake()
        except Exception:
            await transport.close()
            self._http = None
            raise

        logger.info(
            "mcp_client_connected",
            transport="http",
            url=url,
            era="modern" if self.is_modern else "legacy",
            server_name=info.name,
        )
        return info

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

        # A tool that reported `isError: true` executed and failed: surfacing
        # its content as a normal result would hand the model a failure message
        # dressed as data.
        if response.get("isError"):
            raise MCPToolError(_error_text(response) or f"Tool '{name}' failed")

        # A tool declaring an outputSchema returns the typed payload directly;
        # prefer it over re-parsing the text mirror sent for older clients.
        if "structuredContent" in response:
            return response["structuredContent"]

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
        if self._protocol_version is not None:
            # Modern era: version, identity and capabilities travel with every
            # request — there is no session to carry them.
            params = {
                **params,
                "_meta": {
                    **params.get("_meta", {}),
                    **client_request_meta(self._protocol_version, self._client_info),
                },
            }
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

        await write_message(self._writer, request)

        # Read until the reply carrying our id arrives, bounded by a timeout so
        # a hung server cannot block the agent loop indefinitely. On timeout we
        # mark the connection unusable rather than risk consuming a late reply
        # as the answer to a subsequent request.
        timeout = get_mcp_config().mcp_client_request_timeout
        try:
            response = await read_response(self._reader, request["id"], timeout=timeout)
        except TimeoutError as exc:
            self._connected = False
            raise RuntimeError(
                f"MCP server timed out after {timeout}s waiting for "
                f"response to '{method}'"
            ) from exc

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

        await write_message(self._writer, notification)


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
