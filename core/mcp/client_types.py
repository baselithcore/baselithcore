"""Value objects the MCP client hands back to callers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class MCPToolInfo:
    """Information about an MCP tool."""

    name: str
    description: str
    input_schema: dict[str, Any]


__all__ = ["MCPToolInfo"]
