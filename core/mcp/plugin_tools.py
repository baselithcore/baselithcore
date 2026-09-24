"""Expose a plugin's ``get_mcp_tools()`` on an MCP server, and withdraw them.

Plugins are activated in the app lifespan, after ``create_app()`` has built
the HTTP-mounted server — so registering "every plugin tool" once, at server
construction, sees an empty registry and exposes nothing. Registration is
therefore per plugin and driven by activation: the runtime hooks call
:func:`register_plugin_mcp_tools` as each plugin comes up (at startup or via
hot reload) and :func:`unregister_plugin_mcp_tools` when it is disabled.

Each handler is wrapped so a call that races a disable is refused instead of
running against a plugin that has already shut down.
"""

from __future__ import annotations

import functools
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any

from core.observability.logging import get_logger

if TYPE_CHECKING:
    from core.mcp.server import MCPServer

logger = get_logger(__name__)

ToolHandler = Callable[..., Coroutine[Any, Any, Any]]


class PluginToolUnavailableError(RuntimeError):
    """Raised when a plugin tool is called after its plugin was disabled."""


def _guarded(plugin: Any, tool_name: str, handler: ToolHandler) -> ToolHandler:
    @functools.wraps(handler)
    async def _call(**arguments: Any) -> Any:
        if not plugin.is_initialized():
            raise PluginToolUnavailableError(
                f"Tool {tool_name!r} is unavailable: plugin "
                f"{plugin.metadata.name!r} is not active"
            )
        return await handler(**arguments)

    return _call


def register_plugin_mcp_tools(server: MCPServer, plugin: Any) -> list[str]:
    """Register the tools a plugin exposes through ``get_mcp_tools()``.

    A malformed definition (no ``name`` or ``handler``) is skipped; a plugin
    whose ``get_mcp_tools()`` raises contributes nothing. Neither stops the
    caller, because one plugin's tool list must not block another's
    activation.

    Args:
        server: The server to register on.
        plugin: An initialized plugin.

    Returns:
        The names of the tools registered, for a later withdrawal.
    """
    plugin_name = plugin.metadata.name
    try:
        definitions = plugin.get_mcp_tools()
    except Exception as exc:
        logger.error(
            "mcp_plugin_tool_registration_failed", plugin=plugin_name, error=str(exc)
        )
        return []

    registered: list[str] = []
    for tool_def in definitions:
        name = tool_def.get("name")
        handler = tool_def.get("handler")
        if not name or handler is None:
            logger.warning(
                "mcp_plugin_tool_definition_skipped", plugin=plugin_name, tool=name
            )
            continue
        try:
            server.register_tool(
                name=name,
                description=tool_def.get("description") or "",
                input_schema=tool_def.get("input_schema") or {},
                handler=_guarded(plugin, name, handler),
                # Undeclared tools are gated as destructive (fail-safe).
                category=tool_def.get("category", "destructive"),
            )
        except Exception as exc:
            logger.error(
                "mcp_plugin_tool_registration_failed",
                plugin=plugin_name,
                tool=name,
                error=str(exc),
            )
            continue
        registered.append(name)
        logger.info("mcp_plugin_tool_registered", plugin=plugin_name, tool=name)
    return registered


def unregister_plugin_mcp_tools(server: MCPServer, names: list[str]) -> None:
    """Withdraw tools previously returned by :func:`register_plugin_mcp_tools`."""
    for name in names:
        server.unregister_tool(name)


__all__ = [
    "PluginToolUnavailableError",
    "register_plugin_mcp_tools",
    "unregister_plugin_mcp_tools",
]
