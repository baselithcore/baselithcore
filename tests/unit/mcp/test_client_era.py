"""The client detects which protocol era a server speaks, and adapts.

A dual-era client probes with ``server/discover``: a ``DiscoverResult`` means
the server is modern (stateless, per-request `_meta`), anything else means it
still wants the ``initialize`` handshake. Getting this wrong is not a graceful
degradation — a modern request to a legacy server can be silently mis-served.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from core.mcp.client import MCPClient
from core.mcp.modern import MODERN_PROTOCOL_VERSION, PROTOCOL_VERSION_KEY


class _FakeWriter:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    def write(self, data: bytes) -> None:
        for line in data.decode().splitlines():
            if line.strip():
                self.sent.append(json.loads(line))

    async def drain(self) -> None:
        return None


class _ScriptedServer:
    """Replies to each request from a canned table, matching the request id."""

    def __init__(self, writer: _FakeWriter, replies: dict[str, Any]) -> None:
        self._writer = writer
        self._replies = replies
        self._cursor = 0

    async def readline(self) -> bytes:
        if self._cursor >= len(self._writer.sent):
            # The client is single-flight, so a read always follows its write;
            # nothing pending means the test scripted the exchange wrong.
            raise AssertionError("read with no pending request")
        request = self._writer.sent[self._cursor]
        self._cursor += 1
        if "id" not in request:
            return await self.readline()  # notification: nothing to answer
        reply = self._replies.get(request["method"])
        if reply is None:
            body: dict[str, Any] = {
                "error": {"code": -32601, "message": "Method not found"}
            }
        else:
            body = {"result": reply}
        return (
            json.dumps({"jsonrpc": "2.0", "id": request["id"], **body}).encode() + b"\n"
        )


def _client(replies: dict[str, Any], monkeypatch: Any) -> tuple[MCPClient, _FakeWriter]:
    config = SimpleNamespace(
        mcp_client_request_timeout=1.0,
        mcp_server_name="test-client",
        mcp_server_version="9.9.9",
    )
    monkeypatch.setattr("core.mcp.client.get_mcp_config", lambda: config)
    monkeypatch.setattr("core.mcp.client_handshake.get_mcp_config", lambda: config)
    client = MCPClient()
    writer = _FakeWriter()
    client._writer = writer  # type: ignore[assignment]
    client._reader = _ScriptedServer(writer, replies)  # type: ignore[assignment]
    return client, writer


_DISCOVER = {
    "resultType": "complete",
    "supportedVersions": [MODERN_PROTOCOL_VERSION, "2025-11-25"],
    "capabilities": {"tools": {}},
    "ttlMs": 60000,
    "cacheScope": "public",
}
_INITIALIZE = {
    "protocolVersion": "2025-11-25",
    "serverInfo": {"name": "legacy-server", "version": "1.0.0"},
    "capabilities": {"tools": {}},
}


class TestEraProbe:
    @pytest.mark.asyncio
    async def test_modern_server_skips_the_handshake(self, monkeypatch: Any) -> None:
        client, writer = _client({"server/discover": _DISCOVER}, monkeypatch)

        info = await client.handshake()

        assert client.is_modern
        assert info.name == "test-server" or info.capabilities == {"tools": {}}
        methods = [m["method"] for m in writer.sent]
        assert methods == ["server/discover"]
        # No initialize, and no notifications/initialized: both are retired.
        assert "initialize" not in methods
        assert "notifications/initialized" not in methods

    @pytest.mark.asyncio
    async def test_legacy_server_falls_back_to_initialize(
        self, monkeypatch: Any
    ) -> None:
        client, writer = _client({"initialize": _INITIALIZE}, monkeypatch)

        info = await client.handshake()

        assert not client.is_modern
        assert info.name == "legacy-server"
        methods = [m["method"] for m in writer.sent]
        assert methods == [
            "server/discover",
            "initialize",
            "notifications/initialized",
        ]

    @pytest.mark.asyncio
    async def test_version_is_negotiated_from_the_discover_list(
        self, monkeypatch: Any
    ) -> None:
        """A server that does not speak our newest version still works."""
        older = dict(_DISCOVER, supportedVersions=["2025-11-25", "2025-06-18"])
        client, writer = _client(
            {"server/discover": older, "initialize": _INITIALIZE}, monkeypatch
        )

        info = await client.handshake()

        # No mutually supported *modern* version: fall back to the handshake.
        assert not client.is_modern
        assert info.name == "legacy-server"
        assert "initialize" in [m["method"] for m in writer.sent]


class TestModernRequests:
    @pytest.mark.asyncio
    async def test_every_request_carries_per_request_metadata(
        self, monkeypatch: Any
    ) -> None:
        client, writer = _client(
            {"server/discover": _DISCOVER, "tools/list": {"tools": []}}, monkeypatch
        )
        await client.handshake()

        await client.list_tools()

        meta = writer.sent[-1]["params"]["_meta"]
        assert meta[PROTOCOL_VERSION_KEY] == MODERN_PROTOCOL_VERSION
        assert meta["io.modelcontextprotocol/clientCapabilities"] == {}
        assert meta["io.modelcontextprotocol/clientInfo"] == {
            "name": "test-client",
            "version": "9.9.9",
        }

    @pytest.mark.asyncio
    async def test_legacy_requests_carry_no_metadata(self, monkeypatch: Any) -> None:
        client, writer = _client(
            {"initialize": _INITIALIZE, "tools/list": {"tools": []}}, monkeypatch
        )
        await client.handshake()

        await client.list_tools()

        assert "_meta" not in writer.sent[-1]["params"]


class TestHttpStandardHeaders:
    """Modern requests over HTTP must mirror body fields into headers."""

    @staticmethod
    def _transport(seen: list[Any]):
        import httpx

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}
            )

        return httpx.MockTransport(handler)

    @pytest.mark.asyncio
    async def test_modern_message_carries_method_and_name_headers(
        self, monkeypatch: Any
    ) -> None:
        from core.mcp.http_client_transport import HTTPClientTransport
        from core.mcp.modern import client_request_meta

        monkeypatch.setattr(
            "core.mcp.http_client_transport.get_mcp_config",
            lambda: SimpleNamespace(
                mcp_client_request_timeout=5.0, mcp_allow_internal_endpoints=True
            ),
        )
        seen: list[Any] = []
        transport = HTTPClientTransport(
            "https://server.test/mcp", httpx_transport=self._transport(seen)
        )

        await transport.send(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "get_weather",
                    "arguments": {},
                    "_meta": client_request_meta(MODERN_PROTOCOL_VERSION),
                },
            }
        )
        await transport.close()

        headers = seen[0].headers
        assert headers["mcp-method"] == "tools/call"
        assert headers["mcp-name"] == "get_weather"
        assert headers["mcp-protocol-version"] == MODERN_PROTOCOL_VERSION

    @pytest.mark.asyncio
    async def test_non_ascii_name_uses_the_base64_sentinel(
        self, monkeypatch: Any
    ) -> None:
        import base64

        from core.mcp.http_client_transport import HTTPClientTransport
        from core.mcp.modern import client_request_meta

        monkeypatch.setattr(
            "core.mcp.http_client_transport.get_mcp_config",
            lambda: SimpleNamespace(
                mcp_client_request_timeout=5.0, mcp_allow_internal_endpoints=True
            ),
        )
        seen: list[Any] = []
        transport = HTTPClientTransport(
            "https://server.test/mcp", httpx_transport=self._transport(seen)
        )
        uri = "mcp://docs/città"

        await transport.send(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "resources/read",
                "params": {
                    "uri": uri,
                    "_meta": client_request_meta(MODERN_PROTOCOL_VERSION),
                },
            }
        )
        await transport.close()

        expected = base64.b64encode(uri.encode()).decode()
        assert seen[0].headers["mcp-name"] == f"=?base64?{expected}?="

    @pytest.mark.asyncio
    async def test_legacy_message_sends_no_standard_headers(
        self, monkeypatch: Any
    ) -> None:
        from core.mcp.http_client_transport import HTTPClientTransport

        monkeypatch.setattr(
            "core.mcp.http_client_transport.get_mcp_config",
            lambda: SimpleNamespace(
                mcp_client_request_timeout=5.0, mcp_allow_internal_endpoints=True
            ),
        )
        seen: list[Any] = []
        transport = HTTPClientTransport(
            "https://server.test/mcp", httpx_transport=self._transport(seen)
        )

        await transport.send(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        )
        await transport.close()

        assert "mcp-method" not in seen[0].headers


class TestStructuredContent:
    @pytest.mark.asyncio
    async def test_structured_content_is_preferred_over_the_text_mirror(
        self, monkeypatch: Any
    ) -> None:
        """A modern server sends both; the typed payload is the better one."""
        client, _ = _client(
            {
                "server/discover": _DISCOVER,
                "tools/call": {
                    "content": [{"type": "text", "text": '{"temp_c": 21.5}'}],
                    "structuredContent": {"temp_c": 21.5},
                    "isError": False,
                },
            },
            monkeypatch,
        )
        await client.handshake()

        assert await client.call_tool("weather", {}) == {"temp_c": 21.5}
