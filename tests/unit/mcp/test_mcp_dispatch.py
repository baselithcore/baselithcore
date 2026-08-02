"""Concurrent request dispatch, cancellation and progress notifications.

A server that awaits each message inline cannot honour `notifications/cancelled`
(nothing is in flight to cancel) and blocks every other request behind a slow
tool. These tests pin the concurrent dispatcher and the two utilities it makes
possible.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from core.mcp.dispatch import RequestDispatcher
from core.mcp.progress import report_progress
from core.mcp.server import MCPServer


class _Outbox:
    """Collects the messages the dispatcher writes back to the client."""

    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    async def send(self, message: dict[str, Any]) -> None:
        self.messages.append(message)

    def results(self) -> list[Any]:
        return [m["result"] for m in self.messages if "result" in m]


def _call(msg_id: Any, tool: str, **meta: Any) -> dict[str, Any]:
    params: dict[str, Any] = {"name": tool, "arguments": {}}
    if meta:
        params["_meta"] = meta
    return {"jsonrpc": "2.0", "id": msg_id, "method": "tools/call", "params": params}


class TestConcurrentDispatch:
    @pytest.mark.asyncio
    async def test_a_slow_tool_does_not_block_a_fast_one(self) -> None:
        server = MCPServer()

        async def slow() -> str:
            await asyncio.sleep(0.05)
            return "slow"

        async def fast() -> str:
            return "fast"

        for name, handler in (("slow", slow), ("fast", fast)):
            server.register_tool(
                name=name,
                description="",
                input_schema={"type": "object", "properties": {}},
                handler=handler,
            )

        outbox = _Outbox()
        dispatcher = RequestDispatcher(server.handle_message, outbox.send)

        await dispatcher.dispatch(_call(1, "slow"))
        await dispatcher.dispatch(_call(2, "fast"))
        await dispatcher.drain()

        # The fast reply is written first: the two ran concurrently.
        assert [m["id"] for m in outbox.messages] == [2, 1]

    @pytest.mark.asyncio
    async def test_notifications_get_no_response(self) -> None:
        server = MCPServer()
        outbox = _Outbox()
        dispatcher = RequestDispatcher(server.handle_message, outbox.send)

        await dispatcher.dispatch(
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
        )
        await dispatcher.drain()

        assert outbox.messages == []


class TestCancellation:
    @pytest.mark.asyncio
    async def test_cancelled_request_produces_no_response(self) -> None:
        server = MCPServer()
        started = asyncio.Event()

        async def forever() -> str:
            started.set()
            await asyncio.sleep(3600)
            return "never"

        server.register_tool(
            name="forever",
            description="",
            input_schema={"type": "object", "properties": {}},
            handler=forever,
        )

        outbox = _Outbox()
        dispatcher = RequestDispatcher(server.handle_message, outbox.send)

        await dispatcher.dispatch(_call(1, "forever"))
        await started.wait()
        await dispatcher.dispatch(
            {
                "jsonrpc": "2.0",
                "method": "notifications/cancelled",
                "params": {"requestId": 1, "reason": "user aborted"},
            }
        )
        await dispatcher.drain()

        # Spec: a cancelled request gets no response at all.
        assert outbox.messages == []

    @pytest.mark.asyncio
    async def test_cancelling_an_unknown_request_is_ignored(self) -> None:
        """A cancellation racing a completed response must not raise."""
        server = MCPServer()
        outbox = _Outbox()
        dispatcher = RequestDispatcher(server.handle_message, outbox.send)

        await dispatcher.dispatch(
            {
                "jsonrpc": "2.0",
                "method": "notifications/cancelled",
                "params": {"requestId": 999},
            }
        )
        await dispatcher.drain()

        assert outbox.messages == []


class TestProgress:
    @staticmethod
    def _server() -> MCPServer:
        server = MCPServer()

        async def work() -> str:
            await report_progress(1, total=2, message="halfway")
            await report_progress(2, total=2)
            return "done"

        server.register_tool(
            name="work",
            description="",
            input_schema={"type": "object", "properties": {}},
            handler=work,
        )
        return server

    @pytest.mark.asyncio
    async def test_progress_is_reported_under_the_request_token(self) -> None:
        outbox = _Outbox()
        dispatcher = RequestDispatcher(self._server().handle_message, outbox.send)

        await dispatcher.dispatch(_call(1, "work", progressToken="tok-1"))
        await dispatcher.drain()

        notifications = [
            m for m in outbox.messages if m.get("method") == "notifications/progress"
        ]
        assert notifications[0]["params"] == {
            "progressToken": "tok-1",
            "progress": 1,
            "total": 2,
            "message": "halfway",
        }
        # Optional fields are omitted rather than sent as null.
        assert notifications[1]["params"] == {
            "progressToken": "tok-1",
            "progress": 2,
            "total": 2,
        }

    @pytest.mark.asyncio
    async def test_no_token_means_no_notifications(self) -> None:
        """Progress is opt-in: without a token the server stays quiet."""
        outbox = _Outbox()
        dispatcher = RequestDispatcher(self._server().handle_message, outbox.send)

        await dispatcher.dispatch(_call(1, "work"))
        await dispatcher.drain()

        assert all("method" not in m for m in outbox.messages)
        assert len(outbox.messages) == 1

    @pytest.mark.asyncio
    async def test_report_progress_outside_a_request_is_a_noop(self) -> None:
        await report_progress(1, total=1)  # must not raise
