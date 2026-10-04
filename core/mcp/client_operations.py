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


def _scan_part(part: Any, source: str) -> Any:
    """Scan one content part's text, returning a copy when there is any."""
    from core.guardrails import scan_external_content

    if not isinstance(part, dict):
        return part
    scanned = dict(part)
    text = part.get("text")
    if isinstance(text, str):
        scanned["text"] = scan_external_content(text, source=source)
    resource = part.get("resource")
    # An embedded resource carries its text one level down.
    if isinstance(resource, dict) and isinstance(resource.get("text"), str):
        scanned["resource"] = {
            **resource,
            "text": scan_external_content(resource["text"], source=source),
        }
    return scanned


def scan_content_parts(parts: Any, *, source: str) -> Any:
    """Scan every text-bearing part of an MCP ``content``/``contents`` list.

    Only a lone text item used to be scanned, so a server that answered with
    two text parts — or a text part beside an image — handed its text to the
    model unscanned. Every ``text`` field is now scanned with
    :func:`~core.guardrails.scan_external_content` (and sanitized under the
    ``BASELITH_SANITIZE_EXTERNAL_CONTENT`` policy), including the text of an
    embedded ``resource`` part. Binary parts (``image``, ``audio``, ``blob``)
    pass through untouched. The input is never mutated.

    Args:
        parts: The ``content`` (tool result) or ``contents`` (resource read)
            list; anything that is not a list is returned unchanged.
        source: Origin label recorded with any finding.

    Returns:
        A new list with scanned text, or ``parts`` itself when not a list.
    """
    if not isinstance(parts, list):
        return parts
    return [_scan_part(part, source) for part in parts]


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

        # External MCP servers are untrusted: every text part is scanned for
        # indirect prompt injection before it enters the agent's context
        # (sanitized under BASELITH_SANITIZE_EXTERNAL_CONTENT, on by default).
        content = scan_content_parts(content, source=f"mcp_tool:{name}")

        # Return text content if single item
        if len(content) == 1 and content[0].get("type") == "text":
            text = content[0].get("text", "")
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
        resources: list[dict[str, Any]] = response.get("resources", [])
        return resources

    async def read_resource(self, uri: str) -> Any:
        """Read a resource from the server."""
        self._ensure_connected()

        response = await self._send_request("resources/read", {"uri": uri})
        contents = scan_content_parts(
            response.get("contents", []), source=f"mcp_resource:{uri}"
        )

        if contents and len(contents) == 1:
            return contents[0].get("text")

        return contents
