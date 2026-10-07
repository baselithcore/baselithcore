"""Item 1: MCPToolInfo carries title, annotations, outputSchema and a spec hash."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import httpx

from core.mcp.client import MCPClient
from core.mcp.client_types import MCPToolAnnotations, MCPToolInfo

from .fakes.http_server import default_methods, json_response, make_transport, rpc

FULL_TOOL: dict[str, Any] = {
    "name": "send_mail",
    "title": "Send mail",
    "description": "Sends a mail",
    "inputSchema": {"type": "object", "properties": {"to": {"type": "string"}}},
    "outputSchema": {"type": "object", "properties": {"id": {"type": "string"}}},
    "annotations": {
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": True,
        "title": "ignored here, kept raw",
        "vendorField": 1,
    },
}


def _expected_hash(tool: dict[str, Any]) -> str:
    canonical = json.dumps(
        {
            "name": tool["name"],
            "description": tool.get("description", ""),
            "inputSchema": tool.get("inputSchema", {}),
            "outputSchema": tool.get("outputSchema"),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def _list(tools: list[dict[str, Any]]) -> list[MCPToolInfo]:
    async def handler(req: httpx.Request, body: dict[str, Any]) -> httpx.Response:
        if body.get("method") == "tools/list":
            return json_response(rpc(body["id"], {"tools": tools}))
        return default_methods(body) or httpx.Response(404)

    transport, _ = make_transport(handler)
    async with MCPClient(
        url="https://mcp.example.com/mcp", httpx_transport=transport
    ) as client:
        return await client.list_tools()


async def test_list_tools_exposes_metadata(internal_ok: None) -> None:
    [info] = await _list([FULL_TOOL])
    assert info.title == "Send mail"
    assert info.output_schema == FULL_TOOL["outputSchema"]
    assert info.annotations.read_only_hint is False
    assert info.annotations.destructive_hint is True
    assert info.annotations.idempotent_hint is False
    assert info.annotations.open_world_hint is True
    assert info.annotations.raw["vendorField"] == 1


async def test_missing_metadata_defaults_to_none(internal_ok: None) -> None:
    [info] = await _list([{"name": "t", "inputSchema": {}}])
    assert info.title is None
    assert info.output_schema is None
    assert info.annotations == MCPToolAnnotations()
    assert info.annotations.read_only_hint is None
    assert info.annotations.raw == {}


async def test_non_boolean_hints_are_ignored(internal_ok: None) -> None:
    [info] = await _list(
        [{"name": "t", "annotations": {"readOnlyHint": "yes", "openWorldHint": 1}}]
    )
    assert info.annotations.read_only_hint is None
    assert info.annotations.open_world_hint is None


async def test_spec_hash_is_the_documented_canonical_sha256(internal_ok: None) -> None:
    [info] = await _list([FULL_TOOL])
    assert info.spec_hash == _expected_hash(FULL_TOOL)


def test_spec_hash_ignores_key_order_and_title_but_not_substance() -> None:
    a = MCPToolInfo("t", "d", {"a": 1, "b": 2}, output_schema={"x": 1, "y": 2})
    b = MCPToolInfo("t", "d", {"b": 2, "a": 1}, output_schema={"y": 2, "x": 1})
    assert a.spec_hash == b.spec_hash
    assert len(a.spec_hash) == 64
    assert MCPToolInfo("t", "d", {"a": 1}, title="x").spec_hash == (
        MCPToolInfo("t", "d", {"a": 1}, title="y").spec_hash
    )
    for changed in (
        MCPToolInfo("t2", "d", {"a": 1, "b": 2}, output_schema={"x": 1, "y": 2}),
        MCPToolInfo("t", "d!", {"a": 1, "b": 2}, output_schema={"x": 1, "y": 2}),
        MCPToolInfo("t", "d", {"a": 1}, output_schema={"x": 1, "y": 2}),
        MCPToolInfo("t", "d", {"a": 1, "b": 2}, output_schema={"x": 1}),
        MCPToolInfo("t", "d", {"a": 1, "b": 2}),
    ):
        assert changed.spec_hash != a.spec_hash


def test_positional_construction_stays_compatible() -> None:
    info = MCPToolInfo("n", "d", {})
    assert (info.name, info.description, info.input_schema) == ("n", "d", {})
