"""MCP Protocol Types.

Dataclasses and enums defining the Model Context Protocol structures.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Coroutine


class MCPMessageType(str, Enum):
    """MCP JSON-RPC message types."""

    REQUEST = "request"
    RESPONSE = "response"
    NOTIFICATION = "notification"


@dataclass
class MCPTool:
    """Represents an MCP tool definition."""

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[..., Coroutine[Any, Any, Any]] | None = None


@dataclass
class MCPResource:
    """Represents an MCP resource."""

    uri: str
    name: str
    description: str
    mime_type: str = "text/plain"


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
        capabilities: Server capability flags
    """

    name: str = "baselith-core"
    version: str = "2.0.0"
    capabilities: MCPServerCapabilities = field(default_factory=MCPServerCapabilities)


__all__ = [
    "MCPMessageType",
    "MCPTool",
    "MCPResource",
    "MCPServerCapabilities",
    "MCPServerInfo",
]
