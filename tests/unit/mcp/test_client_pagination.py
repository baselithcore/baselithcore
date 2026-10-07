"""Item 2: list_tools/list_resources follow nextCursor, bounded and cycle-safe."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

from core.mcp.client import MCPClient
from core.mcp.client_errors import MCPProtocolError

from .fakes.http_server import default_methods, json_response, make_transport, rpc

STDIO = str(Path(__file__).parent / "fakes" / "stdio_server.py")


def _paged_http(pages: dict[str | None, tuple[list[str], str | None]], key: str):
    async def handler(req: httpx.Request, body: dict[str, Any]) -> httpx.Response:
        if body.get("method") in ("tools/list", "resources/list"):
            cursor = (body.get("params") or {}).get("cursor")
            names, nxt = pages[cursor]
            if key == "tools":
                items: list[Any] = [
                    {"name": n, "inputSchema": {"type": "object"}} for n in names
                ]
            else:
                items = [{"uri": f"file:///{n}", "name": n} for n in names]
            result: dict[str, Any] = {key: items}
            if nxt:
                result["nextCursor"] = nxt
            return json_response(rpc(body["id"], result))
        return default_methods(body) or httpx.Response(404)

    return make_transport(handler)


async def test_http_tools_follow_cursors(internal_ok: None) -> None:
    transport, seen = _paged_http(
        {None: (["a", "b"], "p2"), "p2": (["c"], "p3"), "p3": (["d"], None)}, "tools"
    )
    async with MCPClient(
        url="https://mcp.example.com/mcp", httpx_transport=transport
    ) as c:
        tools = await c.list_tools()
    assert [t.name for t in tools] == ["a", "b", "c", "d"]
    cursors = [
        b["params"].get("cursor") for _, b in seen if b.get("method") == "tools/list"
    ]
    assert cursors == [None, "p2", "p3"]


async def test_http_resources_follow_cursors(internal_ok: None) -> None:
    transport, _ = _paged_http({None: (["a"], "p2"), "p2": (["b"], None)}, "resources")
    async with MCPClient(
        url="https://mcp.example.com/mcp", httpx_transport=transport
    ) as c:
        resources = await c.list_resources()
    assert [r["name"] for r in resources] == ["a", "b"]


async def test_http_cursor_cycle_is_an_error_not_a_loop(internal_ok: None) -> None:
    transport, seen = _paged_http(
        {None: (["a"], "same"), "same": (["b"], "same")}, "tools"
    )
    async with MCPClient(
        url="https://mcp.example.com/mcp", httpx_transport=transport
    ) as c:
        with pytest.raises(MCPProtocolError, match="cursor"):
            await c.list_tools()
    assert len([1 for _, b in seen if b.get("method") == "tools/list"]) <= 3


async def test_http_page_cap(internal_ok: None, mcp_env: Any) -> None:
    mcp_env(MCP_CLIENT_MAX_PAGES="3")

    async def handler(req: httpx.Request, body: dict[str, Any]) -> httpx.Response:
        if body.get("method") == "tools/list":
            n = int(body["params"].get("cursor") or "0") + 1
            return json_response(
                rpc(body["id"], {"tools": [{"name": f"t{n}"}], "nextCursor": str(n)})
            )
        return default_methods(body) or httpx.Response(404)

    transport, seen = make_transport(handler)
    async with MCPClient(
        url="https://mcp.example.com/mcp", httpx_transport=transport
    ) as c:
        with pytest.raises(MCPProtocolError, match="page"):
            await c.list_tools()
    assert len([1 for _, b in seen if b.get("method") == "tools/list"]) == 3


async def test_http_item_cap(internal_ok: None, mcp_env: Any) -> None:
    mcp_env(MCP_CLIENT_MAX_ITEMS="5")
    transport, _ = _paged_http(
        {
            None: ([f"a{i}" for i in range(4)], "p2"),
            "p2": ([f"b{i}" for i in range(4)], None),
        },
        "tools",
    )
    async with MCPClient(
        url="https://mcp.example.com/mcp", httpx_transport=transport
    ) as c:
        with pytest.raises(MCPProtocolError, match="items"):
            await c.list_tools()


async def _stdio(mode: str) -> MCPClient:
    client = MCPClient(command=[sys.executable, STDIO])
    await client.connect(env={"FAKE_MODE": mode})
    return client


async def test_stdio_tools_follow_cursors() -> None:
    client = await _stdio("pages")
    try:
        assert [t.name for t in await client.list_tools()] == ["a", "b", "c"]
    finally:
        await client.disconnect()


async def test_stdio_cursor_cycle() -> None:
    client = await _stdio("cursor_loop")
    try:
        with pytest.raises(MCPProtocolError, match="cursor"):
            await client.list_tools()
    finally:
        await client.disconnect()


async def test_stdio_unbounded_pages_hit_the_cap(mcp_env: Any) -> None:
    mcp_env(MCP_CLIENT_MAX_PAGES="4")
    client = await _stdio("infinite_pages")
    try:
        with pytest.raises(MCPProtocolError, match="page"):
            await client.list_tools()
    finally:
        await client.disconnect()
