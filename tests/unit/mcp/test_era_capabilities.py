"""What the server tells a client it can do, and when it answers at all.

Two JSON-RPC/MCP conformance rules the dispatcher broke:

* a **notification** (a message with no ``id``) never gets a response — not
  even ``method not found``. The server answered unknown notifications with an
  error whose ``id`` was ``null``, which a strict client treats as a response
  to a request it never made.
* **capabilities are a promise**. ``listChanged`` and the tasks extension were
  advertised to every client, including legacy-era ones that have no
  ``subscriptions/listen`` stream to receive a change notification on and get
  ``method not found`` for every ``tasks/*`` call.
"""

from __future__ import annotations

from core.mcp.modern import (
    CLIENT_CAPABILITIES_KEY,
    MODERN_PROTOCOL_VERSION,
    PROTOCOL_VERSION_KEY,
)
from core.mcp.server import MCPServer


def _modern(method: str, msg_id: int | None = 1) -> dict:
    message: dict = {
        "jsonrpc": "2.0",
        "method": method,
        "params": {
            "_meta": {
                PROTOCOL_VERSION_KEY: MODERN_PROTOCOL_VERSION,
                CLIENT_CAPABILITIES_KEY: {},
            }
        },
    }
    if msg_id is not None:
        message["id"] = msg_id
    return message


class TestNotifications:
    async def test_unknown_notification_gets_no_response(self) -> None:
        server = MCPServer()

        response = await server.handle_message(
            {"jsonrpc": "2.0", "method": "notifications/progress", "params": {}}
        )

        assert response is None

    async def test_unknown_request_still_gets_method_not_found(self) -> None:
        server = MCPServer()

        response = await server.handle_message(
            {"jsonrpc": "2.0", "id": 9, "method": "nope/at-all"}
        )

        assert response is not None
        assert response["error"]["code"] == -32601

    async def test_null_id_request_is_still_answered(self) -> None:
        """An explicit ``"id": null`` is a (malformed) request, not a notification."""
        server = MCPServer()

        response = await server.handle_message(
            {"jsonrpc": "2.0", "id": None, "method": "nope/at-all"}
        )

        assert response is not None
        assert response["error"]["code"] == -32601

    async def test_modern_removed_method_as_notification_gets_no_response(self) -> None:
        server = MCPServer()

        response = await server.handle_message(_modern("ping", msg_id=None))

        assert response is None

    async def test_known_notification_is_still_silent(self) -> None:
        server = MCPServer()

        response = await server.handle_message(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}
        )

        assert response is None


class TestEraGatedCapabilities:
    async def test_legacy_initialize_does_not_promise_list_changed(self) -> None:
        """A legacy client has no stream to receive the notification on."""
        server = MCPServer()

        result = await server._handle_initialize({"protocolVersion": "2025-11-25"})
        capabilities = result["capabilities"]

        assert capabilities["tools"] == {}
        assert capabilities["resources"] == {}
        assert "listChanged" not in capabilities.get("prompts", {})

    async def test_legacy_initialize_does_not_advertise_the_tasks_extension(
        self,
    ) -> None:
        """``tasks/*`` is answered with method-not-found outside the modern era."""
        server = MCPServer()

        result = await server._handle_initialize({"protocolVersion": "2025-06-18"})

        assert "extensions" not in result["capabilities"]

    async def test_legacy_initialize_keeps_advertising_logging(self) -> None:
        server = MCPServer()

        result = await server._handle_initialize({"protocolVersion": "2025-06-18"})

        assert result["capabilities"]["logging"] == {}

    async def test_modern_discover_promises_list_changed(self) -> None:
        server = MCPServer()

        result = await server._handle_discover()
        capabilities = result["capabilities"]

        assert capabilities["tools"] == {"listChanged": True}
        assert capabilities["resources"] == {"listChanged": True}
        assert "io.modelcontextprotocol/tasks" in capabilities["extensions"]

    async def test_modern_discover_does_not_advertise_logging_set_level(self) -> None:
        """``logging/setLevel`` was removed in 2026-07-28; the level rides _meta."""
        server = MCPServer()

        result = await server._handle_discover()

        assert "logging" not in result["capabilities"]

    async def test_disabled_capabilities_stay_omitted_in_both_eras(self) -> None:
        server = MCPServer()
        server.info.capabilities.prompts = False
        server.info.capabilities.logging = False

        legacy = await server._handle_initialize({"protocolVersion": "2025-11-25"})
        modern = await server._handle_discover()

        for capabilities in (legacy["capabilities"], modern["capabilities"]):
            assert "prompts" not in capabilities
            assert "logging" not in capabilities
            assert None not in capabilities.values()
