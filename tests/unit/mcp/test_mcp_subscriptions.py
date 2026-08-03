"""`subscriptions/listen`: the only server-initiated stream in 2026-07-28."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from core.mcp.modern import MODERN_PROTOCOL_VERSION, PROTOCOL_VERSION_KEY
from core.mcp.server import MCPServer
from core.mcp.subscriptions import (
    ACKNOWLEDGED,
    RESOURCES_UPDATED,
    SUBSCRIPTION_ID_KEY,
    TOOLS_LIST_CHANGED,
)


class _Stream:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    async def send(self, message: dict[str, Any]) -> None:
        self.messages.append(message)

    def methods(self) -> list[str]:
        return [m.get("method") for m in self.messages]


def _listen(notifications: dict[str, Any], msg_id: Any = 1) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "method": "subscriptions/listen",
        "params": {
            "notifications": notifications,
            "_meta": {
                PROTOCOL_VERSION_KEY: MODERN_PROTOCOL_VERSION,
                "io.modelcontextprotocol/clientCapabilities": {},
            },
        },
    }


async def _open(server: MCPServer, notifications: dict[str, Any], msg_id: Any = 1):
    stream = _Stream()
    listener = asyncio.create_task(
        server.handle_message(_listen(notifications, msg_id), send=stream.send)
    )
    for _ in range(100):
        if stream.messages:
            break
        await asyncio.sleep(0)
    return stream, listener


async def _echo(server: MCPServer, name: str = "later") -> None:
    async def handler() -> str:
        return "ok"

    server.register_tool(
        name=name,
        description="",
        input_schema={"type": "object", "properties": {}},
        handler=handler,
    )


class TestOpeningAStream:
    @pytest.mark.asyncio
    async def test_acknowledgement_is_the_first_message(self) -> None:
        server = MCPServer()
        stream, listener = await _open(server, {"toolsListChanged": True})

        ack = stream.messages[0]
        assert ack["method"] == ACKNOWLEDGED
        assert ack["params"]["_meta"][SUBSCRIPTION_ID_KEY] == 1
        assert ack["params"]["notifications"] == {"toolsListChanged": True}

        server._subscriptions.close(1)
        await listener

    @pytest.mark.asyncio
    async def test_unsupported_types_are_omitted_from_the_ack(self) -> None:
        """The ack reports what the server agreed to, not what was asked."""
        server = MCPServer()
        stream, listener = await _open(
            server, {"toolsListChanged": True, "somethingElse": True}
        )

        assert "somethingElse" not in stream.messages[0]["params"]["notifications"]

        server._subscriptions.close(1)
        await listener

    @pytest.mark.asyncio
    async def test_graceful_closure_answers_the_listen_request(self) -> None:
        server = MCPServer()
        _, listener = await _open(server, {"toolsListChanged": True})

        server._subscriptions.close(1)
        response = await listener

        assert response is not None
        assert response["id"] == 1
        assert response["result"]["_meta"][SUBSCRIPTION_ID_KEY] == 1

    @pytest.mark.asyncio
    async def test_listen_without_a_stream_is_rejected(self) -> None:
        """No transport stream means nowhere to deliver: say so, don't hang."""
        response = await MCPServer().handle_message(_listen({"toolsListChanged": True}))

        assert response is not None
        assert response["error"]["code"] == -32601


class TestNotifications:
    @pytest.mark.asyncio
    async def test_registering_a_tool_notifies_subscribers(self) -> None:
        server = MCPServer()
        stream, listener = await _open(server, {"toolsListChanged": True})

        await _echo(server)
        await asyncio.sleep(0)

        assert TOOLS_LIST_CHANGED in stream.methods()
        server._subscriptions.close(1)
        await listener

    @pytest.mark.asyncio
    async def test_a_stream_never_receives_what_it_did_not_request(self) -> None:
        server = MCPServer()
        stream, listener = await _open(server, {"promptsListChanged": True})

        await _echo(server)
        await asyncio.sleep(0)

        assert TOOLS_LIST_CHANGED not in stream.methods()
        server._subscriptions.close(1)
        await listener

    @pytest.mark.asyncio
    async def test_resource_updates_are_filtered_by_uri(self) -> None:
        server = MCPServer()
        stream, listener = await _open(
            server, {"resourceSubscriptions": ["mcp://watched"]}
        )

        await server.notify_resource_updated("mcp://other")
        await server.notify_resource_updated("mcp://watched")

        updates = [m for m in stream.messages if m.get("method") == RESOURCES_UPDATED]
        assert [u["params"]["uri"] for u in updates] == ["mcp://watched"]
        server._subscriptions.close(1)
        await listener

    @pytest.mark.asyncio
    async def test_every_notification_carries_its_subscription_id(self) -> None:
        """On stdio all subscriptions share a channel; the id demultiplexes."""
        server = MCPServer()
        first, first_task = await _open(server, {"toolsListChanged": True}, msg_id=7)
        second, second_task = await _open(server, {"toolsListChanged": True}, msg_id=9)

        await _echo(server)
        await asyncio.sleep(0)

        assert all(
            m["params"]["_meta"][SUBSCRIPTION_ID_KEY] == 7 for m in first.messages
        )
        assert all(
            m["params"]["_meta"][SUBSCRIPTION_ID_KEY] == 9 for m in second.messages
        )

        server._subscriptions.close_all()
        await first_task
        await second_task


class TestCapability:
    @pytest.mark.asyncio
    async def test_list_changed_is_now_advertised(self) -> None:
        """The server does emit the notifications, so the flag is honest."""
        result = await MCPServer()._handle_initialize({})

        assert result["capabilities"]["tools"] == {"listChanged": True}
        assert result["capabilities"]["resources"] == {"listChanged": True}
