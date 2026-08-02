"""MCP Protocol Types.

Dataclasses and enums defining the Model Context Protocol structures.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class MCPMessageType(str, Enum):
    """MCP JSON-RPC message types."""

    REQUEST = "request"
    RESPONSE = "response"
    NOTIFICATION = "notification"


@dataclass
class MCPTool:
    """Represents an MCP tool definition.

    ``category`` feeds the autonomy approval matrix
    (``core.orchestration.autonomy``): read_only | mutating | destructive |
    external_side_effect. Defaults to the most permissive category, so tools
    with side effects MUST declare theirs explicitly to be gated.
    """

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[..., Coroutine[Any, Any, Any]] | None = None
    category: str = "read_only"
    # Optional JSON Schema for the tool's structured result (2025-06-18). When
    # set, tools/call returns `structuredContent` validated against it.
    output_schema: dict[str, Any] | None = None
    # Compiled JSON Schema validators, built once at registration so the
    # tools/call hot path skips re-parsing the schemas per invocation.
    validator: Any = field(default=None, compare=False, repr=False)
    output_validator: Any = field(default=None, compare=False, repr=False)


@dataclass
class MCPResource:
    """Represents an MCP resource."""

    uri: str
    name: str
    description: str
    mime_type: str = "text/plain"
    handler: Callable[..., Coroutine[Any, Any, str]] | None = None


@dataclass
class MCPResourceTemplate:
    """A parameterized resource family, e.g. ``mcp://reports/{year}/{month}``.

    Listed by ``resources/templates/list``; a ``resources/read`` whose URI
    matches the template invokes ``handler(uri, **variables)``.
    """

    uri_template: str
    name: str
    description: str
    mime_type: str = "text/plain"
    handler: Callable[..., Coroutine[Any, Any, str]] | None = None
    # Compiled matcher, built once at registration.
    pattern: Any = field(default=None, compare=False, repr=False)


@dataclass
class MCPServerCapabilities:
    """Server capabilities for MCP handshake.

    Attributes:
        tools: Whether the server supports tool invocation
        resources: Whether the server exposes resources
        prompts: Whether the server provides prompt templates
        logging: Whether the server supports logging
    """

    tools: bool = True
    resources: bool = True
    prompts: bool = False
    logging: bool = True


@dataclass
class MCPServerInfo:
    """Server information for MCP handshake.

    Attributes:
        name: Server name identifier
        version: Server version string
        description: Human-readable server context (2025-11-25 optional
            ``Implementation.description``, aligned with registry server.json)
        capabilities: Server capability flags
    """

    name: str = "baselith-core"
    version: str = "2.0.0"
    description: str = (
        "BaselithCore orchestration engine — RAG search/indexing, web scraping "
        "and reasoning tools over the Model Context Protocol"
    )
    capabilities: MCPServerCapabilities = field(default_factory=MCPServerCapabilities)


__all__ = [
    "MCPMessageType",
    "MCPResource",
    "MCPResourceTemplate",
    "MCPServerCapabilities",
    "MCPServerInfo",
    "MCPTool",
]
