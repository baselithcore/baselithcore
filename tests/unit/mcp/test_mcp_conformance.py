"""MCP wire-conformance regressions (spec revisions 2025-06-18 / 2025-11-25).

Each test pins a rule the previous implementation broke on the wire:
capability advertisement, the ``ping`` result shape, the JSON Schema dialect
used to validate tool arguments, tool-execution error propagation on the
client, and stdio response demultiplexing.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest

from core.mcp.client import MCPClient, MCPToolError
from core.mcp.server import MCPServer


class _FakeWriter:
    """StreamWriter stub that records the frames written to it."""

    def __init__(self) -> None:
        self.frames: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.frames.append(data)

    async def drain(self) -> None:
        return None


class _ScriptedReader:
    """StreamReader stub replaying pre-scripted newline-delimited frames."""

    def __init__(self, messages: list[dict[str, Any]]) -> None:
        self._lines = [json.dumps(m).encode() + b"\n" for m in messages]

    async def readline(self) -> bytes:
        if not self._lines:
            await asyncio.sleep(3600)
        return self._lines.pop(0)


def _stdio_client(messages: list[dict[str, Any]], monkeypatch: Any) -> MCPClient:
    monkeypatch.setattr(
        "core.mcp.client.get_mcp_config",
        lambda: SimpleNamespace(mcp_client_request_timeout=1.0),
    )
    client = MCPClient()
    client._reader = _ScriptedReader(messages)  # type: ignore[assignment]
    client._writer = _FakeWriter()  # type: ignore[assignment]
    client._connected = True
    return client


class TestServerCapabilities:
    """`ServerCapabilities` members are objects or absent — never JSON null."""

    @pytest.mark.asyncio
    async def test_disabled_capabilities_are_omitted_not_null(self) -> None:
        server = MCPServer()
        server.info.capabilities.prompts = False
        server.info.capabilities.logging = False

        result = await server._handle_initialize({"protocolVersion": "2025-11-25"})
        capabilities = result["capabilities"]

        assert "prompts" not in capabilities
        assert "logging" not in capabilities
        assert None not in capabilities.values()
        # `listChanged` is advertised because the server does emit the
        # notifications — on any subscriptions/listen stream that opted in.
        assert capabilities["tools"] == {"listChanged": True}
        assert capabilities["resources"] == {"listChanged": True}

    @pytest.mark.asyncio
    async def test_logging_capability_implies_set_level_support(self) -> None:
        """Advertising `logging` obliges the server to answer logging/setLevel."""
        server = MCPServer()
        assert server.info.capabilities.logging is True

        response = await server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 7,
                "method": "logging/setLevel",
                "params": {"level": "debug"},
            }
        )

        assert response is not None
        assert "error" not in response
        assert response["result"] == {}


class TestPing:
    """Spec: the receiver responds to `ping` with an *empty* result object."""

    @pytest.mark.asyncio
    async def test_ping_result_is_empty_object(self) -> None:
        server = MCPServer()

        response = await server.handle_message(
            {"jsonrpc": "2.0", "id": 5, "method": "ping", "params": {}}
        )

        assert response is not None
        assert response["result"] == {}


class TestSchemaDialect:
    """SEP-1613: JSON Schema 2020-12 is the default dialect for MCP schemas."""

    @pytest.mark.asyncio
    async def test_2020_12_keywords_are_enforced(self) -> None:
        server = MCPServer()

        async def handler(coords: list[Any]) -> str:
            return "ok"

        server.register_tool(
            name="plot",
            description="Plot a labelled point",
            input_schema={
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "properties": {
                    # `prefixItems` exists only from 2019-09 onwards: a Draft-7
                    # validator ignores it and accepts anything.
                    "coords": {
                        "type": "array",
                        "prefixItems": [{"type": "string"}, {"type": "number"}],
                    }
                },
                "required": ["coords"],
            },
            handler=handler,
        )

        result = await server._handle_call_tool(
            {"name": "plot", "arguments": {"coords": [123, 4.5]}}
        )

        assert result["isError"] is True
        assert "Invalid arguments" in result["content"][0]["text"]


class TestClientToolExecutionErrors:
    """`isError: true` is a failed call, not content to hand back as a result."""

    @pytest.mark.asyncio
    async def test_call_tool_raises_on_is_error(self, monkeypatch: Any) -> None:
        client = _stdio_client(
            [
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {
                        "content": [{"type": "text", "text": "boom: missing arg"}],
                        "isError": True,
                    },
                }
            ],
            monkeypatch,
        )

        with pytest.raises(MCPToolError, match="boom: missing arg"):
            await client.call_tool("broken", {})

    @pytest.mark.asyncio
    async def test_call_tool_returns_content_when_not_error(
        self, monkeypatch: Any
    ) -> None:
        client = _stdio_client(
            [
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {
                        "content": [{"type": "text", "text": '{"temp": 21}'}],
                        "isError": False,
                    },
                }
            ],
            monkeypatch,
        )

        assert await client.call_tool("weather", {}) == {"temp": 21}


class TestStdioDemultiplexing:
    """Server-initiated frames must not be mistaken for a request's reply."""

    @pytest.mark.asyncio
    async def test_interleaved_notification_is_skipped(self, monkeypatch: Any) -> None:
        client = _stdio_client(
            [
                # A log notification the server may emit at any time.
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/message",
                    "params": {"level": "info", "data": "warming up"},
                },
                {"jsonrpc": "2.0", "id": 1, "result": {"tools": [{"name": "search"}]}},
            ],
            monkeypatch,
        )

        result = await client._send_request("tools/list", {})

        assert result == {"tools": [{"name": "search"}]}

    @pytest.mark.asyncio
    async def test_stale_response_id_is_skipped(self, monkeypatch: Any) -> None:
        """A late reply to an abandoned request never answers the current one."""
        client = _stdio_client(
            [
                {"jsonrpc": "2.0", "id": 99, "result": {"stale": True}},
                {"jsonrpc": "2.0", "id": 1, "result": {"fresh": True}},
            ],
            monkeypatch,
        )

        assert await client._send_request("tools/list", {}) == {"fresh": True}
