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
import re
import unicodedata
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


#: What an undeclared server's tools are treated as: the most restrictive
#: category, matching ``ToolDefinition``'s own default, so an unknown server is
#: gated rather than waved through. (It used to be ``read_only``.)
UNKNOWN_SERVER_CATEGORY = "destructive"

#: A tool name as the MCP spec writes them (1-128 of ``A-Za-z0-9_.-``), tightened
#: to 64 so ``<server>.<tool>`` stays within common model-API name limits.
_TOOL_NAME = re.compile(r"[A-Za-z0-9_.\-]{1,64}")
_SERVER_NAME = re.compile(r"[A-Za-z0-9_\-]{1,64}")


def _valid_name(pattern: re.Pattern[str], name: object) -> bool:
    """Whole-string match on plain ASCII that NFKC leaves unchanged.

    ``fullmatch``, never ``^...$`` + ``match``: ``$`` also matches before a
    trailing newline, so ``"tool\n"`` would pass and then differ from ``"tool"``
    in every downstream comparison.
    """
    return (
        isinstance(name, str)
        and name.isascii()
        and unicodedata.normalize("NFKC", name) == name
        and pattern.fullmatch(name) is not None
    )


MAX_DESCRIPTION_CHARS = 2000


def _configured_category(server_name: str) -> str:
    """The server's configured autonomy category.

    An unknown server falls back to :data:`UNKNOWN_SERVER_CATEGORY` (the most
    restrictive) and logs a deprecation warning: the previous fallback,
    ``read_only``, silently trusted a server nobody had declared. Declare the
    server in ``MCP_SERVERS`` or pass ``autonomy_category=`` explicitly.
    """
    spec = get_mcp_config().mcp_servers.get(server_name)
    if spec is not None:
        return spec.autonomy_category
    logger.warning(
        "mcp_unknown_server_category_defaulted",
        server_name=server_name,
        category=UNKNOWN_SERVER_CATEGORY,
        deprecation=(
            "undeclared MCP servers used to default to read_only; declare the "
            "server or pass autonomy_category explicitly"
        ),
    )
    return UNKNOWN_SERVER_CATEGORY


def _clean_description(text: str, source: str) -> str:
    """A server-written description made safe to show a model.

    Control and format characters (bidi overrides, zero-width, NULs) are
    dropped, the text is scanned and sanitised as untrusted external content,
    and the result is capped at :data:`MAX_DESCRIPTION_CHARS`.
    """
    from core.guardrails import scan_external_content

    printable = "".join(
        ch
        for ch in text
        if ch in "\n\t" or unicodedata.category(ch) not in {"Cc", "Cf", "Cs", "Co"}
    )
    cleaned = scan_external_content(
        printable[: MAX_DESCRIPTION_CHARS * 2], source=source, sanitize=True
    )
    return cleaned[:MAX_DESCRIPTION_CHARS]


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
    across servers; the server name and each tool name are validated, an
    invalid or duplicate tool name is skipped), calls through :meth:`MCPConnectionPool.call_tool`, and
    is tagged with the server's autonomy category so the approval gate
    applies the operator's declared caution.

    Args:
        pool: Pool holding the mounted server connection.
        server_name: Name the server was mounted under.
        tools: Tool descriptors (``MCPToolInfo``) or plain tool names — the
            latter compose directly with :func:`mount_configured_servers`
            output, at the cost of an empty description/schema.
        autonomy_category: Explicit category override; when omitted it is
            read from the global config's ``mcp_servers`` entry. A server not
            in the config defaults to the most restrictive category
            (``destructive``) with a deprecation warning - it used to be
            ``read_only``.

    Tool descriptions are server-written and therefore untrusted: they are
    stripped of control/bidi characters, scanned and sanitised, and capped.

    Returns:
        ToolDefinitions ready for registration in a tool registry.

    Raises:
        ValueError: *server_name* is not 1-64 characters of ``A-Za-z0-9_-``.
    """
    # Lazy import: core.reasoning pulls the whole reasoning stack, which
    # this wiring module must not load at import time.
    from core.reasoning.react import ToolDefinition

    if not _valid_name(_SERVER_NAME, server_name):
        raise ValueError(
            f"invalid MCP server name {server_name!r}: use 1-64 of A-Z a-z 0-9 _ -"
        )
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
    seen: set[str] = set()
    for tool in tools:
        info = _as_tool_info(tool)
        # Duplicates are judged case-insensitively too: ``Tool`` and ``tool`` are one tool.
        if not _valid_name(_TOOL_NAME, info.name) or info.name.lower() in seen:
            logger.warning(
                "mcp_declarative_tool_skipped",
                server_name=server_name,
                reason="duplicate" if info.name.lower() in seen else "invalid_name",
            )
            continue
        seen.add(info.name.lower())
        description = _clean_description(
            info.description, f"mcp_tool_description:{server_name}.{info.name}"
        )
        definitions.append(
            ToolDefinition(
                name=f"{server_name}.{info.name}",
                fn=_make_fn(info.name),
                description=description
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
