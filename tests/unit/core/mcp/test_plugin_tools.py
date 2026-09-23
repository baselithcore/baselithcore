"""Per-plugin MCP tool exposure, driven by plugin activation.

Regression: ``create_app()`` registered "every plugin tool" while building the
HTTP-mounted server, before the lifespan had activated a single plugin, so no
plugin tool ever reached ``/mcp``.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from core.api._plugin_runtime import PluginRuntimeHooks
from core.mcp.plugin_tools import (
    PluginToolUnavailableError,
    register_plugin_mcp_tools,
    unregister_plugin_mcp_tools,
)
from core.mcp.server import MCPServer


class _Plugin:
    def __init__(self, name: str, tools: list[dict[str, Any]]) -> None:
        self.metadata = SimpleNamespace(name=name)
        self._tools = tools
        self.initialized = True

    def is_initialized(self) -> bool:
        return self.initialized

    def get_mcp_tools(self) -> list[dict[str, Any]]:
        return self._tools

    def get_router_prefix(self) -> str:
        return ""

    def get_routers(self) -> list[Any]:
        return []


async def _echo(text: str) -> str:
    return text


def _echo_tool(name: str = "echo_text", **extra: Any) -> dict[str, Any]:
    return {
        "name": name,
        "description": "Echo",
        "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}},
        "handler": _echo,
        **extra,
    }


def test_registers_valid_definitions_and_skips_malformed() -> None:
    server = MCPServer()
    plugin = _Plugin("p", [_echo_tool(category="read_only"), {"name": "no_handler"}])

    names = register_plugin_mcp_tools(server, plugin)

    assert names == ["echo_text"]
    assert server._tools["echo_text"].category == "read_only"
    assert "no_handler" not in server._tools


def test_undeclared_category_is_gated_as_destructive() -> None:
    server = MCPServer()
    register_plugin_mcp_tools(server, _Plugin("p", [_echo_tool()]))
    assert server._tools["echo_text"].category == "destructive"


def test_a_raising_plugin_contributes_nothing() -> None:
    server = MCPServer()
    plugin = _Plugin("p", [])
    plugin.get_mcp_tools = lambda: (_ for _ in ()).throw(RuntimeError("boom"))  # type: ignore[method-assign]
    assert register_plugin_mcp_tools(server, plugin) == []


@pytest.mark.asyncio
async def test_handler_refuses_once_the_plugin_is_down() -> None:
    server = MCPServer()
    plugin = _Plugin("p", [_echo_tool()])
    register_plugin_mcp_tools(server, plugin)
    handler = server._tools["echo_text"].handler

    assert await handler(text="hi") == "hi"
    plugin.initialized = False
    with pytest.raises(PluginToolUnavailableError):
        await handler(text="hi")


def test_unregister_withdraws_tools() -> None:
    server = MCPServer()
    names = register_plugin_mcp_tools(server, _Plugin("p", [_echo_tool()]))
    unregister_plugin_mcp_tools(server, names)
    assert "echo_text" not in server._tools
    assert server.unregister_tool("echo_text") is False


class _Lifecycle:
    def __init__(self) -> None:
        self.hooks: dict[tuple[str, str], list[Any]] = {}

    def register_hook(self, name: str, hook_type: str, callback: Any) -> None:
        self.hooks.setdefault((name, hook_type), []).append(callback)


class _Registry:
    def get_all_static_paths(self) -> dict[str, Any]:
        return {}


def _hooks(server: MCPServer | None) -> tuple[PluginRuntimeHooks, _Lifecycle]:
    app = SimpleNamespace(state=SimpleNamespace(), include_router=lambda *a, **k: None)
    if server is not None:
        app.state.mcp_server = server
    lifecycle = _Lifecycle()
    hooks = PluginRuntimeHooks(
        app=app,  # type: ignore[arg-type]
        plugin_registry=_Registry(),
        plugin_configs={},
        lifecycle_manager=lifecycle,
        hot_reload_controller=SimpleNamespace(),
    )
    return hooks, lifecycle


@pytest.mark.asyncio
async def test_activation_exposes_tools_and_disable_withdraws_them() -> None:
    server = MCPServer()
    hooks, lifecycle = _hooks(server)
    plugin = _Plugin("coding-agent", [_echo_tool()])

    await hooks.on_plugin_activated(plugin)
    assert "echo_text" in server._tools

    (withdraw,) = lifecycle.hooks[("coding-agent", "on_after_disable")]
    await withdraw(None)  # the lifecycle may pass no plugin object
    assert "echo_text" not in server._tools


@pytest.mark.asyncio
async def test_reactivation_does_not_stack_disable_hooks() -> None:
    server = MCPServer()
    hooks, lifecycle = _hooks(server)
    plugin = _Plugin("p", [_echo_tool()])

    await hooks.on_plugin_activated(plugin)
    hooks.expose_plugin_mcp_tools(plugin)

    assert len(lifecycle.hooks[("p", "on_after_disable")]) == 1
    assert "echo_text" in server._tools


@pytest.mark.asyncio
async def test_no_mcp_server_mounted_is_a_no_op() -> None:
    hooks, lifecycle = _hooks(None)
    await hooks.on_plugin_activated(_Plugin("p", [_echo_tool()]))
    assert lifecycle.hooks == {}
