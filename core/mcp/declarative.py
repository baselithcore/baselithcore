"""
Declarative external MCP server registry.

Operators name external MCP servers in configuration
(``MCPConfig.mcp_servers``, env-configurable as a JSON object via
``MCP_SERVERS``) and :func:`mount_configured_servers` connects each one
through the existing :class:`core.mcp.pool.MCPConnectionPool` / client
machinery — this module is wiring only, transports stay inside the client.

Safety posture:

- Stdio commands are gated by the ``mcp_allowed_commands`` executable
  allowlist; a non-allowlisted command is refused with a log (fail-closed).
- One failing server never aborts the mount of the others (fail-soft, the
  same discipline as :meth:`MCPConnectionPool.list_all_tools`).
- Every tool exposed via :func:`make_mcp_tool_fns` carries the server's
  configured ``autonomy_category``, so the approval gate treats external
  tools with the caution the operator declared.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from core.config import get_mcp_config
from core.config.mcp import MCPConfig, MCPServerSpec
from core.mcp.client import MCPClient
from core.mcp.client_types import MCPToolInfo
from core.mcp.pool import MCPConnectionPool
from core.mcp.stdio_client_transport import validate_command
from core.observability.logging import get_logger

if TYPE_CHECKING:
    from core.reasoning.react_types import ToolDefinition

logger = get_logger(__name__)


def _build_client(spec: MCPServerSpec, config: MCPConfig) -> MCPClient:
    """Construct an unconnected client for *spec*, enforcing the allowlist.

    Raises:
        ValueError: The stdio command's executable is not allowlisted.
    """
    if spec.url:
        return MCPClient(url=spec.url)
    cmd = [spec.command or "", *spec.args]
    validate_command(cmd, allowed=config.allowed_command_basenames)
    return MCPClient(command=cmd)


async def mount_configured_servers(
    pool: MCPConnectionPool, config: MCPConfig | None = None
) -> dict[str, list[str]]:
    """Connect every configured external MCP server and list its tools.

    Args:
        pool: Connection pool that will own the mounted clients.
        config: MCP configuration; the global singleton when omitted.

    Returns:
        Mapping of successfully mounted server name to the names of the
        tools it advertises. A server refused by the command allowlist or
        failing to connect is logged and omitted (fail-closed / fail-soft
        respectively) — its absence from the map is the signal.
    """
    cfg = config if config is not None else get_mcp_config()
    mounted: dict[str, list[str]] = {}
    for name, spec in cfg.mcp_servers.items():
        try:
            client = _build_client(spec, cfg)
        except ValueError as exc:
            logger.warning(
                "mcp_declarative_server_refused", server_name=name, error=str(exc)
            )
            continue
        try:
            await pool.add_client(name, client, env=spec.env or None)
            tools = await pool.get_client(name).list_tools()
        except Exception as exc:
            logger.warning(
                "mcp_declarative_mount_failed", server_name=name, error=str(exc)
            )
            continue
        mounted[name] = [tool.name for tool in tools]
        logger.info(
            "mcp_declarative_server_mounted",
            server_name=name,
            tool_count=len(mounted[name]),
            autonomy_category=spec.autonomy_category,
        )
    return mounted


def _configured_category(server_name: str) -> str:
    """The server's configured autonomy category; ``read_only`` when unknown."""
    spec = get_mcp_config().mcp_servers.get(server_name)
    return spec.autonomy_category if spec is not None else "read_only"


def _as_tool_info(tool: MCPToolInfo | str) -> MCPToolInfo:
    if isinstance(tool, MCPToolInfo):
        return tool
    return MCPToolInfo(name=tool, description="", input_schema={})


def make_mcp_tool_fns(
    pool: MCPConnectionPool,
    server_name: str,
    tools: Iterable[MCPToolInfo | str],
    *,
    autonomy_category: str | None = None,
) -> list[ToolDefinition]:
    """Build per-tool ToolDefinitions proxying to a mounted MCP server.

    Each definition is named ``<server_name>.<tool_name>`` (collision-safe
    across servers), calls through :meth:`MCPConnectionPool.call_tool`, and
    is tagged with the server's autonomy category so the approval gate
    applies the operator's declared caution.

    Args:
        pool: Pool holding the mounted server connection.
        server_name: Name the server was mounted under.
        tools: Tool descriptors (``MCPToolInfo``) or plain tool names — the
            latter compose directly with :func:`mount_configured_servers`
            output, at the cost of an empty description/schema.
        autonomy_category: Explicit category override; when omitted it is
            read from the global config's ``mcp_servers`` entry, defaulting
            to ``read_only``.

    Returns:
        ToolDefinitions ready for registration in a tool registry.
    """
    # Lazy import: core.reasoning pulls the whole reasoning stack, which
    # this wiring module must not load at import time.
    from core.reasoning.react import ToolDefinition

    category = (
        autonomy_category
        if autonomy_category is not None
        else _configured_category(server_name)
    )

    def _make_fn(tool_name: str) -> Any:
        async def call_mcp_tool(**arguments: Any) -> str:
            result = await pool.call_tool(server_name, tool_name, dict(arguments))
            if isinstance(result, str):
                return result
            return json.dumps(result, default=str, ensure_ascii=False)

        return call_mcp_tool

    definitions: list[ToolDefinition] = []
    for tool in tools:
        info = _as_tool_info(tool)
        definitions.append(
            ToolDefinition(
                name=f"{server_name}.{info.name}",
                fn=_make_fn(info.name),
                description=info.description
                or f"Tool '{info.name}' on external MCP server '{server_name}'.",
                parameters=info.input_schema or None,
                category=category,
            )
        )
    return definitions


__all__ = [
    "make_mcp_tool_fns",
    "mount_configured_servers",
]
