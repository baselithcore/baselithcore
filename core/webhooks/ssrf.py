"""
SSRF protection for outbound webhook URLs.

A webhook URL is attacker-influenced data (whoever registers the endpoint), so
before delivering we reject non-``http(s)`` schemes and any host that resolves
to a loopback, private, link-local, reserved, or multicast address — the classic
SSRF targets (cloud metadata endpoints, internal services). Override only for
trusted local development via ``WEBHOOK_ALLOW_INTERNAL``.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


class WebhookSSRFError(ValueError):
    """A webhook URL was rejected as unsafe (SSRF guard)."""


_ALLOWED_SCHEMES = frozenset({"http", "https"})


def _ip_is_blocked(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True  # un-parseable → treat as unsafe
    return (
        addr.is_loopback
        or addr.is_private
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    )


def _resolve_addresses(host: str) -> list[str]:
    """Resolve a hostname to all of its IP addresses."""
    infos = socket.getaddrinfo(host, None)
    return [str(info[4][0]) for info in infos]


def validate_webhook_url(url: str, *, allow_internal: bool = False) -> None:
    """Raise :class:`WebhookSSRFError` if ``url`` is not a safe delivery target.

    Args:
        url: The webhook endpoint URL.
        allow_internal: When ``True`` skip the private/loopback checks (still
            enforces the scheme). For trusted local development only.

    Raises:
        WebhookSSRFError: If the scheme is not http(s), the host is missing, the
            host cannot be resolved, or any resolved address is internal.
    """
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise WebhookSSRFError(
            f"URL scheme {parsed.scheme!r} is not allowed (use http/https)"
        )
    host = parsed.hostname
    if not host:
        raise WebhookSSRFError("URL has no host")

    if allow_internal:
        return

    try:
        addresses = _resolve_addresses(host)
    except socket.gaierror as e:
        raise WebhookSSRFError(f"Could not resolve host {host!r}") from e

    if not addresses:
        raise WebhookSSRFError(f"Host {host!r} resolved to no addresses")

    for ip in addresses:
        if _ip_is_blocked(ip):
            raise WebhookSSRFError(
                f"Host {host!r} resolves to a blocked address ({ip})"
            )
