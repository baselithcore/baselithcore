"""Tests for MCP client request timeout behavior."""

import asyncio
from types import SimpleNamespace

import pytest

from core.mcp.client import MCPClient


class _HangingReader:
    """StreamReader stub whose readline never completes within the timeout."""

    async def readline(self) -> bytes:
        await asyncio.sleep(3600)
        return b""


class _FakeWriter:
    """StreamWriter stub that accepts writes and drains instantly."""

    def write(self, data: bytes) -> None:
        return None

    async def drain(self) -> None:
        return None


@pytest.mark.asyncio
async def test_send_request_times_out(monkeypatch):
    """A hung server triggers a timeout error and marks the client disconnected."""
    monkeypatch.setattr(
        "core.mcp.client.get_mcp_config",
        lambda: SimpleNamespace(mcp_client_request_timeout=0.05),
    )

    client = MCPClient()
    client._reader = _HangingReader()  # type: ignore[assignment]
    client._writer = _FakeWriter()  # type: ignore[assignment]
    client._connected = True

    with pytest.raises(RuntimeError, match="timed out"):
        await client._send_request("tools/list", {})

    # Connection is marked unusable so a late reply is never mistaken for the
    # response to a subsequent request.
    assert client._connected is False
