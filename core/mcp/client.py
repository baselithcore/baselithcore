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
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from core.config import get_mcp_config
from core.mcp.cache import ResultCache
from core.mcp.client_errors import MCPToolError
from core.mcp.client_handshake import HandshakeMixin
from core.mcp.client_operations import OperationsMixin
from core.mcp.client_types import MCPToolInfo
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


@dataclass
class MCPServerInfo:
    """Information about a connected MCP server."""

    name: str
    version: str
    capabilities: dict[str, Any]


class MCPClient(HandshakeMixin, OperationsMixin):
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
        input_provider: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
        | None = None,
        client_capabilities: dict[str, Any] | None = None,
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
            input_provider: Async callback fulfilling an ``InputRequests`` map
                (elicitation / sampling / roots) so multi round-trip requests
                complete transparently. Without one the client declares no such
                capability and a server may not ask.
            client_capabilities: Capabilities advertised on every modern
                request. Derived from *input_provider* when omitted.
        """
        self.server_script = server_script
        self.command = command
        self.url = url
        self.http_headers = http_headers
        self.input_provider = input_provider
        # A server MUST NOT ask for input the client never declared, so the
        # declaration follows what this client can actually fulfil.
        self.client_capabilities = (
            client_capabilities
            if client_capabilities is not None
            else (
                {"elicitation": {}, "sampling": {}, "roots": {}}
                if input_provider
                else {}
            )
        )
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
        # Honours the server's ttlMs/cacheScope hints on the list/read
        # operations; empty until a modern server sends one.
        self.cache = ResultCache()

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
        cached = self.cache.get(method, params)
        if cached is not None:
            logger.debug("mcp_cache_hit", method=method)
            return cached

        self._request_id += 1
        if self._protocol_version is not None:
            # Modern era: version, identity and capabilities travel with every
            # request — there is no session to carry them.
            params = {
                **params,
                "_meta": {
                    **params.get("_meta", {}),
                    **client_request_meta(
                        self._protocol_version,
                        self._client_info,
                        self.client_capabilities,
                    ),
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

        result = response.get("result", {})
        self.cache.store(method, params, result)
        return result

    def _on_notification(self, message: dict[str, Any]) -> None:
        """React to a server notification seen while awaiting a reply.

        A list-changed notification invalidates the matching cached listing
        immediately, which is the point of pairing TTLs with notifications:
        the TTL bounds staleness, the notification ends it.
        """
        self.cache.invalidate(message.get("method", ""))

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
    "MCPToolError",
    "MCPServerInfo",
    "MCPToolInfo",
]
