"""Resource bounds the MCP client applies to anything a server sends.

Resolved from :class:`~core.config.mcp.MCPConfig` on every use, with a
``getattr`` fallback to the documented default so a test that swaps in a
partial config object keeps working.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.config import get_mcp_config

DEFAULT_MAX_RESPONSE_BYTES = 4_194_304
DEFAULT_MAX_LINE_BYTES = 4_194_304
DEFAULT_TOTAL_DEADLINE = 60.0
DEFAULT_MAX_PAGES = 20
DEFAULT_MAX_ITEMS = 1000


@dataclass(frozen=True)
class ClientLimits:
    """Snapshot of the client-side bounds (see ``MCP_CLIENT_*`` settings)."""

    max_response_bytes: int
    max_line_bytes: int
    total_deadline: float
    max_pages: int
    max_items: int


def client_limits() -> ClientLimits:
    """Read the current bounds from the MCP configuration."""
    config = get_mcp_config()
    return ClientLimits(
        max_response_bytes=getattr(
            config, "mcp_client_max_response_bytes", DEFAULT_MAX_RESPONSE_BYTES
        ),
        max_line_bytes=getattr(
            config, "mcp_client_max_line_bytes", DEFAULT_MAX_LINE_BYTES
        ),
        total_deadline=getattr(
            config, "mcp_client_total_deadline_seconds", DEFAULT_TOTAL_DEADLINE
        ),
        max_pages=getattr(config, "mcp_client_max_pages", DEFAULT_MAX_PAGES),
        max_items=getattr(config, "mcp_client_max_items", DEFAULT_MAX_ITEMS),
    )


__all__ = ["ClientLimits", "client_limits"]
