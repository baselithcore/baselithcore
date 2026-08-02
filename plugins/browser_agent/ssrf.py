"""SSRF guarding for browser navigation.

Two layers cooperate:

- a cheap, offline literal check (:func:`assert_navigation_allowed`,
  :func:`_hostname_is_blocked`) that runs before every ``goto``;
- an authoritative, DNS-resolving check (:func:`_hostname_resolves_to_internal`)
  wired into the Playwright route handler, which also sees server-driven
  redirects.
"""

from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import urlparse

_ALLOWED_SCHEMES = frozenset({"http", "https"})
_BLOCKED_HOSTNAMES = frozenset({"localhost", "broadcasthost"})


def _ip_is_internal(ip: str) -> bool:
    """Return True for an IP string in a loopback/private/reserved range.

    An unparseable value is treated as unsafe (fail-closed).
    """
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        # ::ffff:169.254.169.254 must be judged on its embedded IPv4.
        addr = addr.ipv4_mapped
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    )


def _hostname_is_blocked(hostname: str) -> bool:
    """Cheap, offline-safe literal check — blocks known-internal hostnames and
    literal IPs (any form ``ipaddress`` parses) in internal ranges.

    Does NOT resolve DNS: this is the fast pre-navigation gate. The
    authoritative, DNS-resolving check that defeats rebinding and non-standard
    IP encodings runs at the network layer in :meth:`BrowserAgent._ssrf_route_guard`
    via :func:`_hostname_resolves_to_internal`.
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
    return _ip_is_internal(lowered)


def _hostname_resolves_to_internal(hostname: str) -> bool:
    """Resolve DNS and fail closed: True when resolution fails or ANY resolved
    address is internal.

    Defeats the SSRF bypasses the literal check cannot see: a public-looking
    domain whose A record points at ``169.254.169.254``/``127.0.0.1`` (DNS
    rebinding), and decimal/octal/hex IP encodings (``2130706433``,
    ``0x7f000001``) which ``getaddrinfo`` normalizes to their internal form.
    Blocking on connection (may issue a DNS lookup) — call off the event loop.
    """
    if not hostname:
        return True
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        return True
    if not infos:
        return True
    return any(_ip_is_internal(str(info[4][0])) for info in infos)


def _ssrf_guard_disabled() -> bool:
    """Return True when ``BASELITH_BROWSER_ALLOW_INTERNAL`` is truthy."""
    raw = os.environ.get("BASELITH_BROWSER_ALLOW_INTERNAL", "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _url_is_blocked(url: str, *, resolve_dns: bool = False) -> bool:
    """Return True when ``url`` has a disallowed scheme or an internal host.

    With ``resolve_dns=True`` the host is additionally resolved and failed
    closed if it maps to an internal address (the authoritative check used at
    the network layer). Blocking when ``resolve_dns`` is set — run off-loop.
    """
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme not in _ALLOWED_SCHEMES:
        return True
    host = parsed.hostname or ""
    if _hostname_is_blocked(host):
        return True
    if resolve_dns and _hostname_resolves_to_internal(host):
        return True
    return False


def assert_navigation_allowed(url: str) -> None:
    """Raise ``ValueError`` when ``url`` targets an internal/loopback resource.

    Override with ``BASELITH_BROWSER_ALLOW_INTERNAL=true`` for trusted local use.
    """
    if _ssrf_guard_disabled():
        return
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise ValueError(f"Refusing to navigate: scheme '{scheme}' not allowed")
    hostname = parsed.hostname or ""
    if _hostname_is_blocked(hostname):
        raise ValueError(
            f"Refusing to navigate: '{hostname}' resolves to a blocked range"
        )
