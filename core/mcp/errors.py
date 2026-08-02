"""JSON-RPC error types carrying the codes the MCP spec assigns.

Handlers raise these instead of bare exceptions so the dispatcher can emit the
right code: a client distinguishes "you asked for something that isn't here"
(``-32602`` / ``-32002``) from "the server broke" (``-32603``), and only the
former is worth retrying with different parameters.
"""

from __future__ import annotations


class MCPProtocolError(Exception):
    """Base class for errors that map to a JSON-RPC error code."""

    code = -32603


class InvalidParams(MCPProtocolError):
    """Unknown tool, malformed arguments container, or an unusable cursor."""

    code = -32602


class ResourceNotFound(MCPProtocolError):
    """No registered resource or resource template matches the requested URI."""

    code = -32002


__all__ = ["InvalidParams", "MCPProtocolError", "ResourceNotFound"]
