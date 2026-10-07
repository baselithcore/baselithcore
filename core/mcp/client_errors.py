"""Client-side error types for MCP calls.

Kept apart from the client so the operations mixin, the transports and the
OAuth client can all import them without a cycle.

Every type derives from :class:`MCPClientError`, which derives from
``RuntimeError`` — the type the client raised for every failure before these
existed — so an existing ``except RuntimeError`` keeps catching all of them.
Messages never embed a raw server response body: whatever a third-party server
sent is cut through :func:`~core.observability.redaction.safe_snippet` first.
"""

from __future__ import annotations

import re
from typing import Any

from core.observability.redaction import safe_snippet
from core.security.ssrf import SsrfError

#: Upper bound, in characters, of any server-supplied text inside an error.
SNIPPET_LIMIT = 160

_RESOURCE_METADATA = re.compile(r'resource_metadata\s*=\s*"([^"]+)"', re.IGNORECASE)


def error_text(result: dict[str, Any]) -> str:
    """Join the text blocks of an ``isError`` tools/call result."""
    return "\n".join(
        block.get("text", "")
        for block in result.get("content", [])
        if isinstance(block, dict) and block.get("type") == "text"
    ).strip()


def bounded(text: object, limit: int = SNIPPET_LIMIT) -> str:
    """A redacted, single-line, length-bounded excerpt of untrusted text."""
    return safe_snippet(str(text), limit=limit)


class MCPClientError(RuntimeError):
    """Base of every failure the MCP client itself raises."""


class MCPTransportError(MCPClientError):
    """The bytes did not make it: HTTP failure, deadline, size cap, bad framing.

    Attributes:
        status_code: HTTP status when the failure was an HTTP response, else
            ``None``.
    """

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class MCPProtocolError(MCPClientError):
    """The server answered, but not with valid MCP: a JSON-RPC error, a missing
    reply, a repeated pagination cursor.

    Attributes:
        code: JSON-RPC error code when the server returned an error object.
    """

    def __init__(self, message: str, *, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


class MCPBlockedError(MCPTransportError, SsrfError):
    """The egress (SSRF) policy refused the target.

    Also an :class:`~core.security.ssrf.SsrfError`, which is what the
    transport raised before the typed errors existed, so a caller catching
    that keeps working.
    """


class MCPAuthError(MCPTransportError):
    """The server refused our credentials (HTTP 401 or 403).

    Attributes:
        status_code: ``401`` or ``403``.
        www_authenticate: Bounded text of the ``WWW-Authenticate`` challenge,
            if the server sent one.
        resource_metadata: The RFC 9728 ``resource_metadata`` URL from the
            challenge, if present — the entry point for OAuth discovery.
    """

    def __init__(
        self,
        status_code: int,
        *,
        www_authenticate: str | None = None,
        resource_metadata: str | None = None,
    ) -> None:
        super().__init__(
            f"MCP server rejected the credentials (HTTP {status_code})",
            status_code=status_code,
        )
        self.www_authenticate = www_authenticate
        self.resource_metadata = resource_metadata

    @classmethod
    def from_response(cls, status_code: int, challenge: str | None) -> MCPAuthError:
        """Build the error from a 401/403 and its ``WWW-Authenticate`` header."""
        match = _RESOURCE_METADATA.search(challenge) if challenge else None
        return cls(
            status_code,
            www_authenticate=bounded(challenge, 512) if challenge else None,
            resource_metadata=match.group(1) if match else None,
        )


class MCPToolError(MCPClientError):
    """A ``tools/call`` that completed but reported ``isError: true``.

    Distinct from transport/protocol failures: the server executed the tool and
    the tool itself failed, so the message is meant to be surfaced to the model
    for self-correction rather than treated as a broken connection.
    """


__all__ = [
    "SNIPPET_LIMIT",
    "MCPAuthError",
    "MCPBlockedError",
    "MCPClientError",
    "MCPProtocolError",
    "MCPToolError",
    "MCPTransportError",
    "bounded",
    "error_text",
]
