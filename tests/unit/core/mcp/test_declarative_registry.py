"""Tests for the declarative external MCP server registry."""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import patch

import pytest
from core.mcp.declarative import make_mcp_tool_fns, mount_configured_servers
from pydantic import ValidationError

from core.config.mcp import MCPConfig, MCPServerSpec
from core.mcp.client import MCPServerInfo
from core.mcp.client_types import MCPToolInfo


class _FakeClient:
    def __init__(self, tools: list[MCPToolInfo]) -> None:
        self._tools = tools

    async def list_tools(self) -> list[MCPToolInfo]:
        return self._tools


class _FakePool:
    """Duck-typed stand-in for MCPConnectionPool. No real transport."""

    def __init__(
        self,
        tools_by_name: dict[str, list[MCPToolInfo]] | None = None,
        fail_for: tuple[str, ...] = (),
    ) -> None:
        self.added: dict[str, Any] = {}
        self.envs: dict[str, dict[str, str] | None] = {}
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []
        self._tools_by_name = tools_by_name or {}
        self._fail_for = set(fail_for)

    async def add_client(
        self, name: str, client: Any, env: dict[str, str] | None = None
    ) -> MCPServerInfo:
        if name in self._fail_for:
            raise RuntimeError("connection refused")
        self.added[name] = client
        self.envs[name] = env
        return MCPServerInfo(name=name, version="1.0", capabilities={})

    def get_client(self, name: str) -> _FakeClient:
        return _FakeClient(self._tools_by_name.get(name, []))

    async def call_tool(
        self, server_name: str, tool_name: str, arguments: dict[str, Any] | None = None
    ) -> Any:
        self.calls.append((server_name, tool_name, arguments))
        return {"echo": arguments}


def _config(servers: dict[str, dict[str, Any]]) -> MCPConfig:
    return MCPConfig(MCP_SERVERS=servers)


class TestMCPServerSpec:
    def test_command_only_is_valid_with_defaults(self) -> None:
        spec = MCPServerSpec(command="python")
        assert spec.args == []
        assert spec.env == {}
        assert spec.url is None
        assert spec.autonomy_category == "read_only"

    def test_url_only_is_valid(self) -> None:
        spec = MCPServerSpec(url="https://example.com/mcp")
        assert spec.command is None

    def test_both_command_and_url_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MCPServerSpec(command="python", url="https://example.com/mcp")

    def test_neither_command_nor_url_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MCPServerSpec()


class TestConfigWiring:
    def test_default_is_empty(self) -> None:
        assert MCPConfig().mcp_servers == {}

    def test_env_json_parsing(self) -> None:
        raw = (
            '{"weather": {"command": "python", "args": ["weather_server.py"], '
            '"autonomy_category": "mutating"}}'
        )
        with patch.dict(os.environ, {"MCP_SERVERS": raw}):
            config = MCPConfig()
        spec = config.mcp_servers["weather"]
        assert spec.command == "python"
        assert spec.args == ["weather_server.py"]
        assert spec.autonomy_category == "mutating"


class TestMountConfiguredServers:
    async def test_mounts_and_returns_tool_map(self) -> None:
        config = _config(
            {
                "weather": {"command": "python", "args": ["weather_server.py"]},
                "docs": {"url": "https://example.com/mcp"},
            }
        )
        pool = _FakePool(
            tools_by_name={
                "weather": [MCPToolInfo("get_forecast", "d", {})],
                "docs": [MCPToolInfo("search", "d", {}), MCPToolInfo("read", "d", {})],
            }
        )
        result = await mount_configured_servers(pool, config)  # type: ignore[arg-type]
        assert result == {"weather": ["get_forecast"], "docs": ["search", "read"]}
        assert pool.added["weather"].command == ["python", "weather_server.py"]
        assert pool.added["docs"].url == "https://example.com/mcp"

    async def test_disallowed_command_refused_fail_closed(self) -> None:
        config = _config(
            {
                "evil": {"command": "evilbinary", "args": ["x"]},
                "ok": {"command": "python", "args": ["s.py"]},
            }
        )
        pool = _FakePool(tools_by_name={"ok": []})
        result = await mount_configured_servers(pool, config)  # type: ignore[arg-type]
        assert "evil" not in result
        assert "evil" not in pool.added
        assert result == {"ok": []}

    async def test_env_forwarded_to_connection(self) -> None:
        config = _config(
            {"w": {"command": "python", "args": ["s.py"], "env": {"API_MODE": "test"}}}
        )
        pool = _FakePool()
        await mount_configured_servers(pool, config)  # type: ignore[arg-type]
        assert pool.envs["w"] == {"API_MODE": "test"}

    async def test_connect_failure_skips_only_that_server(self) -> None:
        config = _config(
            {
                "bad": {"command": "python", "args": ["b.py"]},
                "good": {"command": "python", "args": ["g.py"]},
            }
        )
        pool = _FakePool(tools_by_name={"good": []}, fail_for=("bad",))
        result = await mount_configured_servers(pool, config)  # type: ignore[arg-type]
        assert result == {"good": []}


class TestMakeMcpToolFns:
    async def test_tool_fns_call_through_pool_and_carry_category(self) -> None:
        pool = _FakePool()
        tools = [
            MCPToolInfo(
                "get_forecast",
                "Get the forecast.",
                {"type": "object", "properties": {"city": {"type": "string"}}},
            )
        ]
        defs = make_mcp_tool_fns(
            pool,  # type: ignore[arg-type]
            "weather",
            tools,
            autonomy_category="mutating",
        )
        assert len(defs) == 1
        td = defs[0]
        assert td.name == "weather.get_forecast"
        assert td.category == "mutating"
        assert td.parameters == tools[0].input_schema
        out = await td.fn(city="Rome")
        assert pool.calls == [("weather", "get_forecast", {"city": "Rome"})]
        assert "Rome" in out

    def test_default_category_read_only(self) -> None:
        defs = make_mcp_tool_fns(
            _FakePool(),  # type: ignore[arg-type]
            "weather",
            [MCPToolInfo("t", "d", {})],
        )
        assert defs[0].category == "read_only"

    def test_accepts_plain_tool_names(self) -> None:
        defs = make_mcp_tool_fns(
            _FakePool(),  # type: ignore[arg-type]
            "weather",
            ["get_forecast"],
        )
        assert defs[0].name == "weather.get_forecast"
        assert callable(defs[0].fn)
