"""The `io.modelcontextprotocol/tasks` extension: durable handles for slow work."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from core.mcp.modern import MODERN_PROTOCOL_VERSION, PROTOCOL_VERSION_KEY
from core.mcp.mrtr import InputRequired, get_input_responses
from core.mcp.server import MCPServer
from core.mcp.tasks import EXTENSION_ID

_ELICIT = {"method": "elicitation/create", "params": {"message": "confirm?"}}


def _message(method: str, params: dict[str, Any], *, tasks: bool = True) -> dict:
    capabilities: dict[str, Any] = {"elicitation": {}}
    if tasks:
        capabilities["extensions"] = {EXTENSION_ID: {}}
    body = dict(params)
    body["_meta"] = {
        PROTOCOL_VERSION_KEY: MODERN_PROTOCOL_VERSION,
        "io.modelcontextprotocol/clientCapabilities": capabilities,
    }
    return {"jsonrpc": "2.0", "id": 1, "method": method, "params": body}


def _server() -> MCPServer:
    server = MCPServer()
    release = asyncio.Event()

    async def slow() -> str:
        await release.wait()
        return "done"

    async def confirmed() -> str:
        answers = get_input_responses()
        if "ok" not in answers:
            raise InputRequired({"ok": _ELICIT})
        return f"confirmed:{answers['ok']['action']}"

    async def boom() -> str:
        raise RuntimeError("upstream exploded")

    for name, handler in (("slow", slow), ("confirmed", confirmed), ("boom", boom)):
        server.register_tool(
            name=name,
            description="",
            input_schema={"type": "object", "properties": {}},
            handler=handler,
            long_running=True,
        )
    server._release = release  # type: ignore[attr-defined]
    return server


async def _result(server: MCPServer, method: str, params: dict, **kw) -> dict:
    response = await server.handle_message(_message(method, params, **kw))
    assert response is not None, "expected a response"
    assert "error" not in response, response.get("error")
    return response["result"]


async def _poll_until(server: MCPServer, task_id: str, status: str) -> dict:
    for _ in range(200):
        task = await _result(server, "tasks/get", {"taskId": task_id})
        if task["status"] == status:
            return task
        await asyncio.sleep(0.005)
    raise AssertionError(f"task never reached {status}: {task}")


class TestCapability:
    @pytest.mark.asyncio
    async def test_extension_is_advertised_by_discover(self) -> None:
        result = await _result(_server(), "server/discover", {})

        assert result["capabilities"]["extensions"][EXTENSION_ID] == {}

    @pytest.mark.asyncio
    async def test_a_client_that_did_not_opt_in_gets_the_plain_result(self) -> None:
        """Handing a task to a client without the extension would strand it."""
        server = _server()
        server._release.set()

        result = await _result(
            server, "tools/call", {"name": "slow", "arguments": {}}, tasks=False
        )

        assert result["resultType"] == "complete"
        assert result["content"][0]["text"] == "done"


class TestTaskLifecycle:
    @pytest.mark.asyncio
    async def test_long_running_call_returns_a_handle_then_completes(self) -> None:
        server = _server()

        created = await _result(server, "tools/call", {"name": "slow", "arguments": {}})
        assert created["resultType"] == "task"
        assert created["status"] == "working"
        assert created["pollIntervalMs"] > 0

        working = await _result(server, "tasks/get", {"taskId": created["taskId"]})
        assert working["status"] == "working"

        server._release.set()
        done = await _poll_until(server, created["taskId"], "completed")
        assert done["result"]["content"][0]["text"] == "done"

    @pytest.mark.asyncio
    async def test_failure_surfaces_as_a_failed_task(self) -> None:
        server = _server()

        created = await _result(server, "tools/call", {"name": "boom", "arguments": {}})
        failed = await _poll_until(server, created["taskId"], "completed")

        # A tool that raises is a *tool* error, so the task completes with an
        # isError result rather than failing at the protocol level.
        assert failed["result"]["isError"] is True
        assert "upstream exploded" in failed["result"]["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_mid_flight_input_parks_the_task_until_update(self) -> None:
        server = _server()

        created = await _result(
            server, "tools/call", {"name": "confirmed", "arguments": {}}
        )
        parked = await _poll_until(server, created["taskId"], "input_required")
        assert parked["inputRequests"]["ok"] == _ELICIT

        await _result(
            server,
            "tasks/update",
            {
                "taskId": created["taskId"],
                "inputResponses": {"ok": {"action": "accept"}},
            },
        )

        done = await _poll_until(server, created["taskId"], "completed")
        assert done["result"]["content"][0]["text"] == "confirmed:accept"

    @pytest.mark.asyncio
    async def test_cancel_moves_the_task_to_cancelled(self) -> None:
        server = _server()
        created = await _result(server, "tools/call", {"name": "slow", "arguments": {}})

        await _result(server, "tasks/cancel", {"taskId": created["taskId"]})

        cancelled = await _poll_until(server, created["taskId"], "cancelled")
        assert cancelled["status"] == "cancelled"

    @pytest.mark.asyncio
    async def test_unknown_task_is_invalid_params(self) -> None:
        response = await _server().handle_message(
            _message("tasks/get", {"taskId": "nope"})
        )

        assert response is not None
        assert response["error"]["code"] == -32602

    @pytest.mark.asyncio
    async def test_task_methods_are_absent_for_legacy_clients(self) -> None:
        response = await _server().handle_message(
            {"jsonrpc": "2.0", "id": 1, "method": "tasks/get", "params": {}}
        )

        assert response is not None
        assert response["error"]["code"] == -32601
