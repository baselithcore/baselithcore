"""Unified SSRF protection for every outbound URL the framework fetches.

Merges the two prior implementations: the literal normalization from the
browser agent (IPv4-mapped IPv6, fail-closed parsing) and the DNS resolve +
IP pinning + SNI strategy from the webhook dispatcher. Any URL that comes
from config, user input, LLM output, or a remote document is
attacker-influenced and must pass through here before a connection is made.

Fail-closed: parse errors, resolution failures, and unknown inputs are all
treated as unsafe.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlparse, urlunparse

from pydantic import BaseModel, ConfigDict

_BLOCKED_HOSTNAMES = frozenset({"localhost", "broadcasthost"})

# RFC 6598 Carrier-Grade NAT range and other networks not caught by stdlib predicates
_EXTRA_INTERNAL_NETWORKS = (
    ipaddress.ip_network("100.64.0.0/10"),  # RFC 6598 CGNAT
    ipaddress.ip_network("192.88.99.0/24"),  # Deprecated 6to4 relay anycast
)


class SsrfError(ValueError):
    """A URL was rejected as an unsafe outbound target."""


class SsrfPolicy(BaseModel):
    """Per-component egress policy.

    Attributes:
        allow_internal: Skip private/loopback blocking and IP pinning
            (scheme checks still apply). Trusted local development only.
        allowed_schemes: URL schemes permitted for outbound requests.
        allowed_hosts: When set, only these exact hostnames are permitted
            (checked before DNS resolution; the resolved-IP checks still run).
    """

    model_config = ConfigDict(frozen=True)

    allow_internal: bool = False
    allowed_schemes: frozenset[str] = frozenset({"http", "https"})
    allowed_hosts: frozenset[str] | None = None


_DEFAULT_POLICY = SsrfPolicy()


def ip_is_internal(ip: str) -> bool:
    """True for loopback/private/link-local/multicast/reserved/unspecified.

    Also blocks RFC 6598 CGNAT (100.64.0.0/10) and deprecated 6to4 relay
    anycast (192.88.99.0/24), which are not caught by stdlib predicates.

    IPv4-mapped IPv6 (``::ffff:169.254.169.254``) is judged on the embedded
    IPv4 address. Unparseable input is unsafe (fail-closed).
    """
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    if (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    ):
        return True
    # Check extra networks not caught by stdlib predicates
    for network in _EXTRA_INTERNAL_NETWORKS:
        if addr in network:
            return True
    return False


def hostname_is_blocked_literal(hostname: str) -> bool:
    """Cheap offline check: known-internal names and literal internal IPs.

    Does not resolve DNS — pair with :func:`assert_url_safe` or
    :func:`resolve_pinned_target` for the authoritative check.
    """
    if not hostname:
        return True
    lowered = hostname.lower().strip(".").strip("[]")
    if lowered in _BLOCKED_HOSTNAMES or lowered.endswith(".localhost"):
        return True
    try:
        ipaddress.ip_address(lowered)
    except ValueError:
        return False
    return ip_is_internal(lowered)


def _parse_and_screen(url: str, policy: SsrfPolicy) -> tuple[str, str]:
    """Shared scheme/host screening. Returns ``(scheme, host)`` or raises."""
    try:
        parsed = urlparse(url)
    except ValueError as e:
        raise SsrfError(f"Malformed URL: {e}") from e
    scheme = (parsed.scheme or "").lower()
    if scheme not in policy.allowed_schemes:
        raise SsrfError(f"URL scheme {scheme!r} is not allowed")
    host = parsed.hostname
    if not host:
        raise SsrfError("URL has no host")
    try:
        _ = parsed.port  # Validate port is in range 0-65535
    except ValueError as e:
        raise SsrfError(f"Invalid URL port: {e}") from e
    if policy.allowed_hosts is not None and host not in policy.allowed_hosts:
        raise SsrfError(f"Host {host!r} is not in the allowed host list")
    return scheme, host


def _resolve_safe_addresses(host: str) -> list[str]:
    """Resolve ``host`` and fail closed unless every address is external."""
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError) as e:
        raise SsrfError(f"Could not resolve host {host!r}") from e
    addresses = [str(info[4][0]) for info in infos]
    if not addresses:
        raise SsrfError(f"Host {host!r} resolved to no addresses")
    for ip in addresses:
        if ip_is_internal(ip):
            raise SsrfError(f"Host {host!r} resolves to a blocked address ({ip})")
    return addresses


def assert_url_safe(url: str, policy: SsrfPolicy | None = None) -> None:
    """Raise :class:`SsrfError` unless ``url`` is a safe outbound target.

    Runs the literal check and, unless ``policy.allow_internal``, resolves
    DNS and fails closed if any resolved address is internal. Blocking
    (may perform a DNS lookup) — call off the event loop, or use
    :func:`assert_url_safe_async`.
    """
    policy = policy or _DEFAULT_POLICY
    _scheme, host = _parse_and_screen(url, policy)
    if policy.allow_internal:
        return
    if hostname_is_blocked_literal(host):
        raise SsrfError(f"Host {host!r} is a blocked internal target")
    _resolve_safe_addresses(host)


async def assert_url_safe_async(url: str, policy: SsrfPolicy | None = None) -> None:
    """Async wrapper for :func:`assert_url_safe` (DNS off the event loop)."""
    await asyncio.to_thread(assert_url_safe, url, policy)


def resolve_pinned_target(
    url: str, policy: SsrfPolicy | None = None
) -> tuple[str, str]:
    """Validate ``url`` and pin it to a verified external IP (anti-rebinding).

    Returns ``(pinned_url, original_host)``: the caller must connect to
    ``pinned_url`` while sending ``original_host`` as the ``Host`` header and
    TLS SNI (httpx: ``extensions={"sni_hostname": host}``), so the address
    validated here is exactly the address connected to. With
    ``policy.allow_internal`` the URL is returned unchanged.

    Blocking (DNS lookup) — call off the event loop.

    Raises:
        SsrfError: Scheme not allowed, host missing/not allowed, resolution
            failure, or any resolved address internal.
    """
    policy = policy or _DEFAULT_POLICY
    _scheme, host = _parse_and_screen(url, policy)
    try:
        parsed = urlparse(url)
    except ValueError as e:
        raise SsrfError(f"Malformed URL: {e}") from e
    if policy.allow_internal:
        return url, host
    if hostname_is_blocked_literal(host):
        raise SsrfError(f"Host {host!r} is a blocked internal target")
    addresses = _resolve_safe_addresses(host)
    safe_ip = addresses[0]
    netloc = f"[{safe_ip}]" if ":" in safe_ip else safe_ip
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"
    return urlunparse(parsed._replace(netloc=netloc)), host
