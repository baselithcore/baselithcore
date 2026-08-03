"""Protocol-era detection for :class:`~core.mcp.client.MCPClient`.

A dual-era client must know which protocol the server on the other end speaks
before it sends anything substantive: modern (2026-07-28, stateless, metadata
on every request) or legacy (an ``initialize`` handshake that establishes a
session). Guessing wrong is not a graceful degradation — a legacy server may
silently mis-serve a modern-shaped request — so the era is established with a
deliberate ``server/discover`` probe, per the spec's stdio fallback rules.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from core.config import get_mcp_config
from core.mcp.modern import SERVER_INFO_KEY, negotiate_version
from core.observability.logging import get_logger

if TYPE_CHECKING:
    from core.mcp.client import MCPServerInfo

logger = get_logger(__name__)


def _modern_client_versions() -> tuple[str, ...]:
    """Modern versions this client can drive, newest first."""
    from core.mcp.client import MODERN_CLIENT_VERSIONS

    return MODERN_CLIENT_VERSIONS


class HandshakeMixin:
    """Era detection and connection setup shared by every transport."""

    # Supplied by MCPClient.
    _protocol_version: str | None
    _client_info: dict[str, Any]
    _server_info: Any
    _connected: bool
    _send_request: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
    _send_notification: Callable[[str, dict[str, Any]], Awaitable[None]]

    @property
    def is_modern(self) -> bool:
        """Whether the connected server speaks a stateless (2026-07-28+) era."""
        return self._protocol_version is not None

    async def handshake(self) -> MCPServerInfo:
        """Establish the era with the connected server and return its identity.

        Probes with ``server/discover`` first: a ``DiscoverResult`` identifies a
        modern server and the client goes stateless from there. Anything else —
        an error, or a reply that is not a discover result — identifies a
        legacy server, and the client falls back to the ``initialize``
        handshake. Guessing wrong is not a graceful degradation, so the probe
        is deliberate rather than optimistic.
        """
        from core.mcp.client import MCPServerInfo

        config = get_mcp_config()
        self._client_info = {
            "name": config.mcp_server_name,
            "version": config.mcp_server_version,
        }

        discovered = await self._probe_discover()
        if discovered is not None:
            self._server_info = MCPServerInfo(
                name=discovered.get("_meta", {})
                .get(SERVER_INFO_KEY, {})
                .get("name", "unknown"),
                version=discovered.get("_meta", {})
                .get(SERVER_INFO_KEY, {})
                .get("version", "unknown"),
                capabilities=discovered.get("capabilities", {}),
            )
            self._connected = True
            logger.info(
                "mcp_client_connected",
                era="modern",
                protocol_version=self._protocol_version,
                server_name=self._server_info.name,
            )
            return self._server_info

        from core.mcp.handlers import LATEST_PROTOCOL_VERSION

        init_response = await self._send_request(
            "initialize",
            {
                "protocolVersion": LATEST_PROTOCOL_VERSION,
                "clientInfo": self._client_info,
                "capabilities": {},
            },
        )

        server_info = init_response.get("serverInfo", {})
        self._server_info = MCPServerInfo(
            name=server_info.get("name", "unknown"),
            version=server_info.get("version", "unknown"),
            capabilities=init_response.get("capabilities", {}),
        )

        # Send initialized notification
        await self._send_notification("notifications/initialized", {})

        self._connected = True
        logger.info(
            "mcp_client_connected",
            era="legacy",
            server_name=self._server_info.name,
            server_version=self._server_info.version,
        )

        return self._server_info

    async def _probe_discover(self) -> dict[str, Any] | None:
        """Return the ``DiscoverResult`` when the server is modern, else None.

        Sets ``self._protocol_version`` to the negotiated version on success.
        """
        try:
            result = await self._send_request("server/discover", {})
        except RuntimeError as exc:
            # "Method not found" and friends: a legacy server, not a failure.
            logger.debug("mcp_discover_probe_declined", error=str(exc))
            return None

        versions = result.get("supportedVersions")
        if not isinstance(versions, list):
            return None
        negotiated = negotiate_version(versions, _modern_client_versions())
        if negotiated is None:
            # The server answered but shares no modern version with us: use the
            # handshake, which is the only thing both sides still speak.
            logger.info("mcp_no_shared_modern_version", server_versions=versions)
            return None
        self._protocol_version = negotiated
        return result
