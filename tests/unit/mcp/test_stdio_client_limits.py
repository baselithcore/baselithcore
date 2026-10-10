"""Item 4 (stdio): bounded frames and a total per-request deadline."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

import pytest

from baselith.core.mcp.client import MCPClient
from baselith.core.mcp.client_errors import MCPTransportError

STDIO = str(Path(__file__).parent / "fakes" / "stdio_server.py")


async def _connect(mode: str) -> MCPClient:
    client = MCPClient(command=[sys.executable, STDIO])
    await client.connect(env={"FAKE_MODE": mode})
    return client


async def test_slow_loris_line_hits_the_deadline(mcp_env: Any) -> None:
    mcp_env(MCP_CLIENT_REQUEST_TIMEOUT="0.6")
    client = await _connect("slowloris")
    started = time.monotonic()
    try:
        with pytest.raises(MCPTransportError, match="timed out"):
            await client.list_tools()
    finally:
        await client.disconnect()
    assert time.monotonic() - started < 5.0
    assert client._connected is False


async def test_oversized_line_is_refused(mcp_env: Any) -> None:
    mcp_env(MCP_CLIENT_MAX_LINE_BYTES="65536")
    client = await _connect("hugeline")
    started = time.monotonic()
    try:
        with pytest.raises(MCPTransportError, match="too large|too long"):
            await client.list_tools()
    finally:
        await client.disconnect()
    assert time.monotonic() - started < 10.0
    assert client._connected is False
