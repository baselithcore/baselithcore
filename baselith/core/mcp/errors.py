"""JSON-RPC error types carrying the codes the MCP spec assigns.

Handlers raise these instead of bare exceptions so the dispatcher can emit the
right code: a client distinguishes "you asked for something that isn't here"
(``-32602`` / ``-32002``) from "the server broke" (``-32603``), and only the
former is worth retrying with different parameters.
"""

from __future__ import annotations

from typing import Any


class MCPProtocolError(Exception):
    """Base class for errors that map to a JSON-RPC error code."""

    code = -32603
    # Code to use under 2026-07-28 semantics when the revision renumbered it.
    modern_code: int | None = None

    def __init__(self, message: str, data: Any | None = None) -> None:
        super().__init__(message)
        self.data = data

    def code_for(self, modern: bool) -> int:
        """The code to emit for the era serving this request."""
        if modern and self.modern_code is not None:
            return self.modern_code
        return self.code


class InvalidParams(MCPProtocolError):
    """Unknown tool, malformed arguments container, or an unusable cursor."""

    code = -32602


class ResourceNotFound(MCPProtocolError):
    """No registered resource or resource template matches the requested URI.

    2026-07-28 retired ``-32002`` and folded this case into Invalid params;
    earlier revisions still expect the old code, so both are kept.
    """

    code = -32002
    modern_code = -32602


class HeaderMismatch(MCPProtocolError):
    """HTTP headers do not match the request body, or a required one is absent."""

    code = -32020


class MissingRequiredClientCapability(MCPProtocolError):
    """Serving the request needs a capability the client did not declare."""

    code = -32021


class UnsupportedProtocolVersion(MCPProtocolError):
    """The requested protocol version is not one this server implements."""

    code = -32022


__all__ = [
    "HeaderMismatch",
    "InvalidParams",
    "MCPProtocolError",
    "MissingRequiredClientCapability",
    "ResourceNotFound",
    "UnsupportedProtocolVersion",
]
