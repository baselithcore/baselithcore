"""Protocol revision 2026-07-28 — stateless, per-request metadata.

2026-07-28 removed the ``initialize`` handshake and protocol-level sessions:
every request carries its own protocol version, client identity and client
capabilities in ``_meta``, and every result carries a ``resultType``, the
server's identity, and caching hints for the operations that are cacheable.

This module holds the era-specific vocabulary and the two transformations the
dispatcher applies — reading a modern request's metadata and finalizing a
modern result — so the handler bodies stay era-agnostic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.mcp.errors import InvalidParams, UnsupportedProtocolVersion

MODERN_PROTOCOL_VERSION = "2026-07-28"

# Reserved `_meta` keys (see spec §"General fields").
_PREFIX = "io.modelcontextprotocol/"
PROTOCOL_VERSION_KEY = f"{_PREFIX}protocolVersion"
CLIENT_INFO_KEY = f"{_PREFIX}clientInfo"
CLIENT_CAPABILITIES_KEY = f"{_PREFIX}clientCapabilities"
LOG_LEVEL_KEY = f"{_PREFIX}logLevel"
SERVER_INFO_KEY = f"{_PREFIX}serverInfo"

# Operations whose results are cacheable; everything else carries no hints.
CACHEABLE_METHODS = frozenset(
    {
        "server/discover",
        "tools/list",
        "prompts/list",
        "resources/list",
        "resources/templates/list",
        "resources/read",
    }
)

# Methods this revision removed. Legacy clients keep them; modern ones get
# "method not found", which is what a conforming 2026-07-28 server returns.
REMOVED_IN_MODERN = frozenset({"ping", "logging/setLevel", "initialize"})


@dataclass(frozen=True)
class RequestMeta:
    """The per-request protocol fields a modern client sends."""

    protocol_version: str
    client_capabilities: dict[str, Any]
    client_info: dict[str, Any] | None = None
    log_level: str | None = None


def is_modern(message: dict[str, Any]) -> bool:
    """Whether *message* is served under 2026-07-28 semantics.

    The protocol version in ``_meta`` is the era marker: it is required on
    every modern request and has no meaning in any earlier revision.
    """
    params = message.get("params")
    if not isinstance(params, dict):
        return False
    meta = params.get("_meta")
    return isinstance(meta, dict) and PROTOCOL_VERSION_KEY in meta


def parse_request_meta(
    message: dict[str, Any], supported_versions: tuple[str, ...]
) -> RequestMeta:
    """Validate and extract the modern per-request metadata.

    Raises:
        UnsupportedProtocolVersion: The requested version is not served.
        InvalidParams: A required per-request field is missing.
    """
    meta = message["params"]["_meta"]
    version = meta[PROTOCOL_VERSION_KEY]
    if version not in supported_versions:
        raise UnsupportedProtocolVersion(
            "Unsupported protocol version",
            data={"supported": list(supported_versions), "requested": version},
        )

    capabilities = meta.get(CLIENT_CAPABILITIES_KEY)
    if not isinstance(capabilities, dict):
        raise InvalidParams(
            f"Missing required per-request field: {CLIENT_CAPABILITIES_KEY}"
        )

    return RequestMeta(
        protocol_version=version,
        client_capabilities=capabilities,
        client_info=meta.get(CLIENT_INFO_KEY),
        log_level=meta.get(LOG_LEVEL_KEY),
    )


def client_request_meta(
    version: str,
    client_info: dict[str, Any] | None = None,
    capabilities: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the ``_meta`` a modern client must attach to every request."""
    meta: dict[str, Any] = {
        PROTOCOL_VERSION_KEY: version,
        CLIENT_CAPABILITIES_KEY: capabilities if capabilities is not None else {},
    }
    if client_info is not None:
        meta[CLIENT_INFO_KEY] = client_info
    return meta


def negotiate_version(
    server_versions: list[str], client_versions: tuple[str, ...]
) -> str | None:
    """Pick the newest version both sides speak, or None when there is none.

    ``client_versions`` is ordered newest-first, so the first match wins.
    """
    offered = set(server_versions)
    return next((v for v in client_versions if v in offered), None)


def finalize_result(
    result: dict[str, Any],
    method: str,
    server_info: dict[str, Any],
    ttl_ms: int,
    cache_scope: str,
) -> dict[str, Any]:
    """Add the fields every modern result must carry.

    ``resultType`` is always ``"complete"`` here: interim
    ``"input_required"`` results belong to the multi round-trip pattern, which
    this server does not use (it never asks the client for input).
    """
    result["resultType"] = "complete"
    result.setdefault("_meta", {})[SERVER_INFO_KEY] = server_info
    if method in CACHEABLE_METHODS:
        result["ttlMs"] = max(0, int(ttl_ms))
        result["cacheScope"] = cache_scope
    return result


__all__ = [
    "CACHEABLE_METHODS",
    "client_request_meta",
    "negotiate_version",
    "CLIENT_CAPABILITIES_KEY",
    "CLIENT_INFO_KEY",
    "LOG_LEVEL_KEY",
    "MODERN_PROTOCOL_VERSION",
    "PROTOCOL_VERSION_KEY",
    "REMOVED_IN_MODERN",
    "SERVER_INFO_KEY",
    "RequestMeta",
    "finalize_result",
    "is_modern",
    "parse_request_meta",
]
