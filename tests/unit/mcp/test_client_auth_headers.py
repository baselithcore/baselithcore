"""Item 6: per-request header provider and a single 401 retry hook."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import httpx
import pytest

from core.mcp.client import MCPClient
from core.mcp.client_errors import MCPAuthError

from .fakes.http_server import default_methods, json_response, make_transport, rpc

URL = "https://mcp.example.com/mcp"


def _auth_required(valid: set[str]):
    async def handler(req: httpx.Request, b: dict[str, Any]) -> httpx.Response:
        if req.headers.get("authorization") not in valid:
            return httpx.Response(401, headers={"WWW-Authenticate": 'Bearer realm="x"'})
        if b.get("method") == "tools/list":
            return json_response(rpc(b["id"], {"tools": [{"name": "ok"}]}))
        return default_methods(b) or httpx.Response(202)

    return handler


async def test_provider_is_evaluated_for_every_request(internal_ok: None) -> None:
    calls = {"n": 0}

    async def provider() -> Mapping[str, str]:
        calls["n"] += 1
        return {"Authorization": f"Bearer tok-{calls['n']}"}

    transport, seen = make_transport(None)
    client = MCPClient(url=URL, httpx_transport=transport, auth=provider)
    await client.connect()
    await client.list_tools()
    await client.disconnect()
    tokens = [r.headers.get("authorization") for r, _ in seen]
    assert len(tokens) >= 3
    assert len(set(tokens)) == len(tokens)  # a fresh header every request
    assert tokens[0] == "Bearer tok-1"


async def test_static_headers_keep_working(internal_ok: None) -> None:
    transport, _ = make_transport(_auth_required({"Bearer static"}))
    async with MCPClient(
        url=URL,
        httpx_transport=transport,
        http_headers={"Authorization": "Bearer static"},
    ) as client:
        assert [t.name for t in await client.list_tools()] == ["ok"]


async def test_on_unauthorized_refreshes_once_and_retries(internal_ok: None) -> None:
    hook_calls: list[MCPAuthError] = []
    state = {"token": "stale"}

    async def provider() -> Mapping[str, str]:
        return {"Authorization": f"Bearer {state['token']}"}

    async def refresh(error: MCPAuthError) -> Mapping[str, str] | None:
        hook_calls.append(error)
        state["token"] = "fresh"  # the caller's token source is refreshed
        return {"Authorization": "Bearer fresh"}

    transport, _ = make_transport(_auth_required({"Bearer fresh"}))
    async with MCPClient(
        url=URL, httpx_transport=transport, auth=provider, on_unauthorized=refresh
    ) as client:
        assert [t.name for t in await client.list_tools()] == ["ok"]
    # one 401 (the first request), one retry, then the provider serves fresh
    assert len(hook_calls) == 1
    assert hook_calls[0].status_code == 401


async def test_second_401_after_refresh_is_final(internal_ok: None) -> None:
    hook_calls = {"n": 0}

    async def refresh(error: MCPAuthError) -> Mapping[str, str] | None:
        hook_calls["n"] += 1
        return {"Authorization": "Bearer still-bad"}

    transport, seen = make_transport(_auth_required({"Bearer never"}))
    client = MCPClient(url=URL, httpx_transport=transport, on_unauthorized=refresh)
    with pytest.raises(MCPAuthError):
        await client.connect()
    assert hook_calls["n"] == 1
    # first attempt + exactly one retry for the probe that failed
    assert len(seen) == 2


async def test_hook_returning_none_means_no_retry(internal_ok: None) -> None:
    async def refresh(error: MCPAuthError) -> Mapping[str, str] | None:
        return None

    transport, seen = make_transport(_auth_required({"Bearer never"}))
    with pytest.raises(MCPAuthError):
        await MCPClient(
            url=URL, httpx_transport=transport, on_unauthorized=refresh
        ).connect()
    assert len(seen) == 1


async def test_403_does_not_trigger_the_hook(internal_ok: None) -> None:
    called = {"n": 0}

    async def refresh(error: MCPAuthError) -> Mapping[str, str] | None:
        called["n"] += 1
        return {"Authorization": "Bearer x"}

    async def handler(req: httpx.Request, b: dict[str, Any]) -> httpx.Response:
        return httpx.Response(403)

    transport, _ = make_transport(handler)
    with pytest.raises(MCPAuthError) as info:
        await MCPClient(
            url=URL, httpx_transport=transport, on_unauthorized=refresh
        ).connect()
    assert info.value.status_code == 403 and called["n"] == 0


async def test_credentials_are_never_logged(
    internal_ok: None,
    caplog: pytest.LogCaptureFixture,
    capfd: pytest.CaptureFixture[str],
) -> None:
    caplog.set_level(logging.DEBUG)

    async def provider() -> Mapping[str, str]:
        return {"Authorization": "Bearer SUPERSECRETTOKEN"}

    transport, _ = make_transport(None)
    async with MCPClient(url=URL, httpx_transport=transport, auth=provider) as client:
        await client.list_tools()
    out = capfd.readouterr()
    assert "SUPERSECRETTOKEN" not in caplog.text + out.out + out.err
