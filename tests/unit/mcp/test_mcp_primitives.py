"""Server-side MCP primitives: structured output, pagination, templates, errors.

Covers the 2025-06-18 structured tool output contract, cursor-based pagination
on the list operations, resource templates, and the JSON-RPC error codes the
spec assigns to unknown tools and missing resources.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from core.mcp.server import MCPServer

_WEATHER_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {"temp_c": {"type": "number"}, "city": {"type": "string"}},
    "required": ["temp_c", "city"],
}


def _server_with_tools(count: int) -> MCPServer:
    server = MCPServer()
    for index in range(count):
        name = f"tool_{index:02d}"

        async def handler(_name: str = name) -> str:
            return _name

        server.register_tool(
            name=name,
            description=f"Tool {index}",
            input_schema={"type": "object", "properties": {}},
            handler=handler,
        )
    return server


class TestStructuredOutput:
    """2025-06-18: tools may declare an `outputSchema` and return structured data."""

    @staticmethod
    def _weather_server() -> MCPServer:
        server = MCPServer()

        async def weather(city: str) -> dict[str, Any]:
            return {"temp_c": 21.5, "city": city}

        server.register_tool(
            name="weather",
            description="Current weather",
            input_schema={
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
            handler=weather,
            output_schema=_WEATHER_OUTPUT_SCHEMA,
        )
        return server

    @pytest.mark.asyncio
    async def test_output_schema_is_advertised(self) -> None:
        result = await self._weather_server()._handle_list_tools({})

        assert result["tools"][0]["outputSchema"] == _WEATHER_OUTPUT_SCHEMA

    @pytest.mark.asyncio
    async def test_tools_without_output_schema_omit_the_field(self) -> None:
        result = await _server_with_tools(1)._handle_list_tools({})

        assert "outputSchema" not in result["tools"][0]

    @pytest.mark.asyncio
    async def test_call_returns_structured_content_and_text_mirror(self) -> None:
        result = await self._weather_server()._handle_call_tool(
            {"name": "weather", "arguments": {"city": "Rome"}}
        )

        assert result["structuredContent"] == {"temp_c": 21.5, "city": "Rome"}
        # Backwards compatibility: the same payload is mirrored as serialized
        # JSON in a text block for clients that ignore structuredContent.
        assert json.loads(result["content"][0]["text"]) == result["structuredContent"]
        assert result["isError"] is False

    @pytest.mark.asyncio
    async def test_output_violating_its_schema_is_a_tool_error(self) -> None:
        """A declared outputSchema is a contract the server must not break."""
        server = MCPServer()

        async def broken() -> dict[str, Any]:
            return {"city": "Rome"}  # missing the required temp_c

        server.register_tool(
            name="broken",
            description="Returns malformed output",
            input_schema={"type": "object", "properties": {}},
            handler=broken,
            output_schema=_WEATHER_OUTPUT_SCHEMA,
        )

        result = await server._handle_call_tool({"name": "broken", "arguments": {}})

        assert result["isError"] is True
        assert "output" in result["content"][0]["text"].lower()


class TestToolExecutionFailures:
    """Errors raised *by a tool* are results, not JSON-RPC protocol errors."""

    @pytest.mark.asyncio
    async def test_handler_exception_is_reported_in_the_result(self) -> None:
        server = MCPServer()

        async def explode() -> str:
            raise RuntimeError("upstream API is down")

        server.register_tool(
            name="explode",
            description="Always fails",
            input_schema={"type": "object", "properties": {}},
            handler=explode,
        )

        response = await server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "explode", "arguments": {}},
            }
        )

        assert response is not None
        assert "error" not in response
        assert response["result"]["isError"] is True
        assert "upstream API is down" in response["result"]["content"][0]["text"]


class TestErrorCodes:
    """Unknown tools and resources carry the codes the spec assigns them."""

    @pytest.mark.asyncio
    async def test_unknown_tool_is_invalid_params(self) -> None:
        server = MCPServer()

        response = await server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "nope", "arguments": {}},
            }
        )

        assert response is not None
        assert response["error"]["code"] == -32602

    @pytest.mark.asyncio
    async def test_unknown_resource_is_resource_not_found(self) -> None:
        server = MCPServer()

        response = await server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "resources/read",
                "params": {"uri": "mcp://missing"},
            }
        )

        assert response is not None
        assert response["error"]["code"] == -32002


class TestPagination:
    """List operations page through an opaque cursor and a deterministic order."""

    @pytest.mark.asyncio
    async def test_tools_list_pages_with_next_cursor(self) -> None:
        server = _server_with_tools(5)
        # Replace the reference, never mutate the process-wide config singleton.
        server.config = SimpleNamespace(mcp_list_page_size=2)

        first = await server._handle_list_tools({})
        assert [t["name"] for t in first["tools"]] == ["tool_00", "tool_01"]
        assert first["nextCursor"]

        second = await server._handle_list_tools({"cursor": first["nextCursor"]})
        assert [t["name"] for t in second["tools"]] == ["tool_02", "tool_03"]

        third = await server._handle_list_tools({"cursor": second["nextCursor"]})
        assert [t["name"] for t in third["tools"]] == ["tool_04"]
        # Last page: no cursor, so the client knows to stop.
        assert "nextCursor" not in third

    @pytest.mark.asyncio
    async def test_single_page_has_no_cursor(self) -> None:
        result = await _server_with_tools(3)._handle_list_tools({})

        assert len(result["tools"]) == 3
        assert "nextCursor" not in result

    @pytest.mark.asyncio
    async def test_invalid_cursor_is_invalid_params(self) -> None:
        server = _server_with_tools(3)

        response = await server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/list",
                "params": {"cursor": "not-a-real-cursor"},
            }
        )

        assert response is not None
        assert response["error"]["code"] == -32602

    @pytest.mark.asyncio
    async def test_resources_list_pages_too(self) -> None:
        server = MCPServer()
        for index in range(3):

            async def read(uri: str) -> str:
                return uri

            server.register_resource(
                uri=f"mcp://doc/{index}",
                name=f"Doc {index}",
                description="",
                handler=read,
            )
        # Replace the reference, never mutate the process-wide config singleton.
        server.config = SimpleNamespace(mcp_list_page_size=2)

        first = await server._handle_list_resources({})
        assert len(first["resources"]) == 2
        second = await server._handle_list_resources({"cursor": first["nextCursor"]})
        assert len(second["resources"]) == 1


class TestResourceTemplates:
    """Parameterized resources are discoverable and readable."""

    @staticmethod
    def _server() -> MCPServer:
        server = MCPServer()

        async def read_report(uri: str, year: str, month: str) -> str:
            return f"report {year}-{month}"

        server.register_resource_template(
            uri_template="mcp://reports/{year}/{month}",
            name="Monthly report",
            description="One report per month",
            handler=read_report,
            mime_type="text/markdown",
        )
        return server

    @pytest.mark.asyncio
    async def test_templates_are_listed(self) -> None:
        result = await self._server()._handle_list_resource_templates({})

        template = result["resourceTemplates"][0]
        assert template["uriTemplate"] == "mcp://reports/{year}/{month}"
        assert template["name"] == "Monthly report"
        assert template["mimeType"] == "text/markdown"

    @pytest.mark.asyncio
    async def test_templates_are_not_mixed_into_resources_list(self) -> None:
        """A template is not a concrete resource — it has no readable URI."""
        result = await self._server()._handle_list_resources({})

        assert result["resources"] == []

    @pytest.mark.asyncio
    async def test_reading_a_templated_uri_passes_the_variables(self) -> None:
        result = await self._server()._handle_read_resource(
            {"uri": "mcp://reports/2026/07"}
        )

        contents = result["contents"][0]
        assert contents["text"] == "report 2026-07"
        assert contents["uri"] == "mcp://reports/2026/07"
        assert contents["mimeType"] == "text/markdown"

    @pytest.mark.asyncio
    async def test_uri_not_matching_any_template_is_not_found(self) -> None:
        server = self._server()

        response = await server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "resources/read",
                "params": {"uri": "mcp://reports/2026"},
            }
        )

        assert response is not None
        assert response["error"]["code"] == -32002
