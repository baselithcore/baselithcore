"""Client-side error types for MCP calls.

Kept apart from the client so the operations mixin and the client itself can
both import them without a cycle.
"""

from __future__ import annotations

from typing import Any


def error_text(result: dict[str, Any]) -> str:
    """Join the text blocks of an ``isError`` tools/call result."""
    return "\n".join(
        block.get("text", "")
        for block in result.get("content", [])
        if isinstance(block, dict) and block.get("type") == "text"
    ).strip()


class MCPToolError(RuntimeError):
    """A ``tools/call`` that completed but reported ``isError: true``.

    Distinct from transport/protocol failures: the server executed the tool and
    the tool itself failed, so the message is meant to be surfaced to the model
    for self-correction rather than treated as a broken connection.
    """


__all__ = ["MCPToolError", "error_text"]
