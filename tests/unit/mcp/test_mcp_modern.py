"""Protocol revision 2026-07-28 — the stateless, per-request-metadata era.

The server is *dual-era*: a request carrying
``_meta["io.modelcontextprotocol/protocolVersion"]`` is served under modern
semantics (no handshake, `resultType`, caching hints, renumbered errors), while
an ``initialize`` handshake still selects the legacy behaviour for older
clients. These tests pin both sides of that split.
"""

from __future__ import annotations

from typing import Any

import pytest

from core.mcp.modern import (
    LOG_LEVEL_KEY,
    MODERN_PROTOCOL_VERSION,
    PROTOCOL_VERSION_KEY,
    SERVER_INFO_KEY,
)
from core.mcp.server import MCPServer

CLIENT_CAPABILITIES_KEY = "io.modelcontextprotocol/clientCapabilities"
CLIENT_INFO_KEY = "io.modelcontextprotocol/clientInfo"


def _modern_meta(
    version: str = MODERN_PROTOCOL_VERSION, **extra: Any
) -> dict[str, Any]:
    meta: dict[str, Any] = {
        PROTOCOL_VERSION_KEY: version,
        CLIENT_CAPABILITIES_KEY: {},
        CLIENT_INFO_KEY: {"name": "ExampleClient", "version": "1.0.0"},
    }
    meta.update(extra)
    return meta


def _modern(method: str, params: dict[str, Any] | None = None, **meta: Any) -> dict:
    body = dict(params or {})
    body["_meta"] = _modern_meta(**meta)
    return {"jsonrpc": "2.0", "id": 1, "method": method, "params": body}


def _server() -> MCPServer:
    server = MCPServer()

    async def echo(message: str) -> str:
        return f"Echo: {message}"

    server.register_tool(
        name="echo",
        description="Echo a message",
        input_schema={
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
        },
        handler=echo,
    )
    return server


class TestEraDetection:
    @pytest.mark.asyncio
    async def test_modern_result_carries_result_type_and_server_info(self) -> None:
        response = await _server().handle_message(_modern("tools/list"))

        assert response is not None
        result = response["result"]
        assert result["resultType"] == "complete"
        assert result["_meta"][SERVER_INFO_KEY] == {
            "name": "baselith-core",
            "version": "2.0.0",
        }

    @pytest.mark.asyncio
    async def test_legacy_result_is_untouched(self) -> None:
        """Legacy clients must not see fields their revision never defined."""
        response = await _server().handle_message(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        )

        assert response is not None
        result = response["result"]
        assert "resultType" not in result
        assert "_meta" not in result
        assert "ttlMs" not in result

    @pytest.mark.asyncio
    async def test_legacy_initialize_still_negotiates(self) -> None:
        response = await _server().handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "clientInfo": {"name": "old"},
                },
            }
        )

        assert response is not None
        assert response["result"]["protocolVersion"] == "2025-11-25"


class TestVersionNegotiation:
    @pytest.mark.asyncio
    async def test_unsupported_version_lists_supported_ones(self) -> None:
        response = await _server().handle_message(
            _modern("tools/list", version="1900-01-01")
        )

        assert response is not None
        error = response["error"]
        assert error["code"] == -32022
        assert error["data"]["requested"] == "1900-01-01"
        assert MODERN_PROTOCOL_VERSION in error["data"]["supported"]

    @pytest.mark.asyncio
    async def test_missing_client_capabilities_is_invalid_params(self) -> None:
        """clientCapabilities is a required per-request field."""
        message = _modern("tools/list")
        del message["params"]["_meta"][CLIENT_CAPABILITIES_KEY]

        response = await _server().handle_message(message)

        assert response is not None
        assert response["error"]["code"] == -32602


class TestDiscover:
    @pytest.mark.asyncio
    async def test_discover_advertises_versions_and_capabilities(self) -> None:
        response = await _server().handle_message(_modern("server/discover"))

        assert response is not None
        result = response["result"]
        assert result["resultType"] == "complete"
        assert MODERN_PROTOCOL_VERSION in result["supportedVersions"]
        assert "2025-11-25" in result["supportedVersions"]
        assert result["capabilities"]["tools"] == {}
        assert result["_meta"][SERVER_INFO_KEY]["name"] == "baselith-core"
        assert result["ttlMs"] >= 0

    @pytest.mark.asyncio
    async def test_discover_is_reachable_without_meta(self) -> None:
        """The stdio backward-compatibility probe carries no per-request meta."""
        response = await _server().handle_message(
            {"jsonrpc": "2.0", "id": 1, "method": "server/discover", "params": {}}
        )

        assert response is not None
        assert MODERN_PROTOCOL_VERSION in response["result"]["supportedVersions"]


class TestCachingHints:
    @pytest.mark.parametrize(
        ("method", "params"),
        [
            ("tools/list", {}),
            ("resources/list", {}),
            ("resources/templates/list", {}),
            ("prompts/list", {}),
        ],
    )
    @pytest.mark.asyncio
    async def test_list_results_carry_cache_hints(
        self, method: str, params: dict[str, Any]
    ) -> None:
        response = await _server().handle_message(_modern(method, params))

        assert response is not None
        result = response["result"]
        assert isinstance(result["ttlMs"], int) and result["ttlMs"] >= 0
        assert result["cacheScope"] in ("public", "private")

    @pytest.mark.asyncio
    async def test_resources_read_carries_cache_hints(self) -> None:
        server = _server()

        async def read(uri: str) -> str:
            return "content"

        server.register_resource(
            uri="mcp://doc", name="Doc", description="", handler=read
        )

        response = await server.handle_message(
            _modern("resources/read", {"uri": "mcp://doc"})
        )

        assert response is not None
        assert "ttlMs" in response["result"]

    @pytest.mark.asyncio
    async def test_tool_calls_are_not_cacheable(self) -> None:
        """Only the listing/read operations carry hints; a call is never cached."""
        response = await _server().handle_message(
            _modern("tools/call", {"name": "echo", "arguments": {"message": "hi"}})
        )

        assert response is not None
        assert "ttlMs" not in response["result"]
        assert response["result"]["resultType"] == "complete"


class TestRemovedMethods:
    @pytest.mark.parametrize("method", ["ping", "logging/setLevel"])
    @pytest.mark.asyncio
    async def test_methods_removed_in_2026_07_28(self, method: str) -> None:
        response = await _server().handle_message(_modern(method))

        assert response is not None
        assert response["error"]["code"] == -32601

    @pytest.mark.parametrize("method", ["ping", "logging/setLevel"])
    @pytest.mark.asyncio
    async def test_removed_methods_still_serve_legacy_clients(
        self, method: str
    ) -> None:
        params = {"level": "debug"} if method == "logging/setLevel" else {}
        response = await _server().handle_message(
            {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        )

        assert response is not None
        assert response["result"] == {}


class TestErrorCodesByEra:
    @pytest.mark.asyncio
    async def test_missing_resource_is_invalid_params_for_modern(self) -> None:
        """-32002 is retired in 2026-07-28; the code becomes -32602."""
        response = await _server().handle_message(
            _modern("resources/read", {"uri": "mcp://missing"})
        )

        assert response is not None
        assert response["error"]["code"] == -32602

    @pytest.mark.asyncio
    async def test_missing_resource_keeps_32002_for_legacy(self) -> None:
        response = await _server().handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "resources/read",
                "params": {"uri": "mcp://missing"},
            }
        )

        assert response is not None
        assert response["error"]["code"] == -32002


class TestPerRequestLogLevel:
    @pytest.mark.asyncio
    async def test_log_level_travels_with_the_request(self) -> None:
        """2026-07-28 sets the log level per request, not per connection."""
        server = _server()

        response = await server.handle_message(
            _modern("tools/list", **{LOG_LEVEL_KEY: "debug"})
        )

        assert response is not None
        assert response["result"]["resultType"] == "complete"
        # The connection-wide level set by the retired logging/setLevel is
        # untouched: the request-scoped value must not leak into it.
        assert server._log_level == "info"
