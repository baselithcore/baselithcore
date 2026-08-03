"""Tool, resource and prompt operations for :class:`~core.mcp.client.MCPClient`.

The wire calls a caller actually makes. Two behaviours live here rather than in
the transport because they are protocol semantics, not framing: a
``tools/call`` that comes back ``isError`` is raised rather than returned, and
an ``InputRequiredResult`` is answered and retried instead of surfacing as a
failure.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from core.mcp.client_errors import MCPToolError, error_text
from core.mcp.client_types import MCPToolInfo
from core.observability.logging import get_logger

logger = get_logger(__name__)


class OperationsMixin:
    """Tool and resource calls over whichever transport is connected."""

    input_provider: Any
    cache: Any
    # Supplied by MCPClient.
    _http: Any
    _ensure_connected: Callable[[], None]
    _send_request: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]

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

        if self._http is not None:
            # The HTTP transport needs the schemas to mirror `x-mcp-header`
            # parameters into headers on the next tools/call.
            self._http.tool_schemas = {
                t["name"]: t.get("inputSchema", {}) for t in tools
            }

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

        response = await self._round_trip(
            "tools/call", {"name": name, "arguments": arguments or {}}
        )

        # A tool that reported `isError: true` executed and failed: surfacing
        # its content as a normal result would hand the model a failure message
        # dressed as data.
        if response.get("isError"):
            raise MCPToolError(error_text(response) or f"Tool '{name}' failed")

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

    async def _round_trip(
        self, method: str, params: dict[str, Any], max_rounds: int = 4
    ) -> dict[str, Any]:
        """Send *method*, fulfilling any input the server asks for, and return
        the final result.

        An ``InputRequiredResult`` is not a failure: the server is asking for
        elicitation, sampling or roots (MRTR, 2026-07-28). Each retry is an
        independent request with a fresh id, echoing ``requestState`` verbatim —
        the value is opaque and must never be inspected or altered.

        Raises:
            MCPToolError: The server kept asking after *max_rounds*, or asked
                while this client declared no way to answer.
        """
        payload = dict(params)
        for _ in range(max_rounds):
            response = await self._send_request(method, payload)
            if response.get("resultType") != "input_required":
                return response

            requests = response.get("inputRequests") or {}
            if requests and self.input_provider is None:
                raise MCPToolError(
                    f"Server asked for input on '{method}' but this client has "
                    "no input_provider configured"
                )

            payload = dict(params)
            if requests:
                payload["inputResponses"] = await self.input_provider(requests)
            if "requestState" in response:
                payload["requestState"] = response["requestState"]

        raise MCPToolError(
            f"Server still requesting input for '{method}' after {max_rounds} rounds"
        )

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
