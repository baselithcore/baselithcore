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
from urllib.parse import urlparse, urlunparse


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


def resolve_pinned_target(url: str, *, allow_internal: bool = False) -> tuple[str, str]:
    """Validate ``url`` and pin it to a verified public IP (anti-rebinding).

    Resolves the host once, fails closed if *any* resolved address is internal,
    and returns a ``(pinned_url, host)`` pair where ``pinned_url`` has the
    hostname replaced by the first safe resolved IP. The caller must connect to
    ``pinned_url`` while sending ``host`` as both the ``Host`` header and the
    TLS SNI (via httpx ``extensions={"sni_hostname": host}``) so that the address
    validated here is exactly the address connected to — closing the DNS
    rebinding window between validation and delivery.

    Args:
        url: The webhook endpoint URL.
        allow_internal: When ``True`` skip the private/loopback checks and
            connection pinning (still enforces the scheme), returning the URL
            unchanged. For trusted local development only.

    Returns:
        ``(pinned_url, host)``. When ``allow_internal`` is set, ``pinned_url``
        is the original URL.

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
        return url, host

    try:
        addresses = _resolve_addresses(host)
    except socket.gaierror as e:
        raise WebhookSSRFError(f"Could not resolve host {host!r}") from e

    if not addresses:
        raise WebhookSSRFError(f"Host {host!r} resolved to no addresses")

    safe_ip: str | None = None
    for ip in addresses:
        if _ip_is_blocked(ip):
            raise WebhookSSRFError(
                f"Host {host!r} resolves to a blocked address ({ip})"
            )
        if safe_ip is None:
            safe_ip = ip

    assert safe_ip is not None  # guaranteed: addresses non-empty, none blocked
    netloc = f"[{safe_ip}]" if ":" in safe_ip else safe_ip
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"
    pinned_url = urlunparse(parsed._replace(netloc=netloc))
    return pinned_url, host


def validate_webhook_url(url: str, *, allow_internal: bool = False) -> None:
    """Raise :class:`WebhookSSRFError` if ``url`` is not a safe delivery target.

    Registration-time validation. For actual delivery use
    :func:`resolve_pinned_target`, which additionally pins the connection to the
    validated IP to defeat DNS rebinding.

    Args:
        url: The webhook endpoint URL.
        allow_internal: When ``True`` skip the private/loopback checks (still
            enforces the scheme). For trusted local development only.

    Raises:
        WebhookSSRFError: If the scheme is not http(s), the host is missing, the
            host cannot be resolved, or any resolved address is internal.
    """
    resolve_pinned_target(url, allow_internal=allow_internal)
