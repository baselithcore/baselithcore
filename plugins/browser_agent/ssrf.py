"""SSRF guarding for browser navigation.

Two layers cooperate:

- a cheap, offline literal check (:func:`assert_navigation_allowed`,
  :func:`_hostname_is_blocked`) that runs before every ``goto``;
- an authoritative, DNS-resolving check (:func:`_hostname_resolves_to_internal`)
  wired into the Playwright route handler, which also sees server-driven
  redirects and sub-resource requests.

IP/hostname classification delegates to the unified :mod:`core.security.ssrf`
module (shared with the webhook dispatcher and the web_scraper plugin) so the
blocked-range logic — including RFC 6598 CGNAT, the deprecated 6to4 relay
anycast range, and IPv4-mapped IPv6 — lives in exactly one place.

.. warning:: Residual DNS-rebinding exposure

    This guard resolves the hostname **in Python**, and Chromium then resolves
    it **again** for the connection it actually makes. An attacker serving a
    zero-TTL record can answer "public" to the first lookup and "internal" to
    the second, so the address validated here is not provably the address
    connected to. Unlike :mod:`core.security.http` — which pins the verified IP
    and passes the original hostname as Host/SNI — a browser cannot be pinned
    the same way: rewriting the URL to the IP breaks TLS certificate
    validation, and Chromium exposes no "refuse private addresses" switch.

    The cache below bounds the window (verdicts expire, and are dropped on
    every top-level navigation) but cannot close it. Closing it requires a
    control below the browser:

    - route the browser through an egress-filtering proxy that performs the
      resolution and policy check itself — pass ``proxy={"server": ...}`` in
      ``BrowserAgent(context_options=...)``, which reaches Playwright's
      ``new_context``; with a proxy configured Chromium delegates resolution to
      it, so there is no second lookup to poison; or
    - run the browser in a network namespace / container whose egress policy
      denies internal ranges outright.

    Treat the in-process guard as defence in depth, not as the perimeter.
"""

from __future__ import annotations

import os
import socket
import time
from urllib.parse import urlparse

from core.security.ssrf import hostname_is_blocked_literal as _hostname_is_blocked
from core.security.ssrf import ip_is_internal as _ip_is_internal

__all__ = [
    "SsrfVerdictCache",
    "assert_navigation_allowed",
]

_ALLOWED_SCHEMES = frozenset({"http", "https"})

# A cached verdict is a decision taken from a *past* DNS answer, so its
# lifetime is exactly the rebinding window. Short enough that a poisoned record
# cannot ride an open page indefinitely, long enough that a page with many
# same-host assets does not pay a lookup per request.
_DEFAULT_VERDICT_TTL_SECONDS = 30.0
_MAX_CACHED_HOSTS = 1024


def _verdict_ttl_seconds() -> float:
    """TTL for cached SSRF verdicts (``BASELITH_BROWSER_SSRF_CACHE_TTL``).

    ``0`` disables caching entirely — every request re-resolves, which is the
    strictest in-process posture at the cost of a DNS lookup per sub-resource.
    """
    raw = os.environ.get("BASELITH_BROWSER_SSRF_CACHE_TTL", "").strip()
    if not raw:
        return _DEFAULT_VERDICT_TTL_SECONDS
    try:
        return max(0.0, float(raw))
    except ValueError:
        return _DEFAULT_VERDICT_TTL_SECONDS


class SsrfVerdictCache:
    """Per-host SSRF verdicts with a bounded lifetime.

    Clearing on navigation alone is not enough: a single-page app can hold one
    document open indefinitely, and with it a verdict derived from a DNS answer
    that has long since changed. Entries therefore also expire on a TTL.
    """

    def __init__(self, ttl_seconds: float | None = None) -> None:
        self._ttl = (
            _verdict_ttl_seconds() if ttl_seconds is None else max(0.0, ttl_seconds)
        )
        self._entries: dict[str, tuple[bool, float]] = {}

    def get(self, host: str) -> bool | None:
        """Cached verdict for ``host``, or ``None`` when absent or expired."""
        if self._ttl <= 0:
            return None
        entry = self._entries.get(host)
        if entry is None:
            return None
        verdict, stamped_at = entry
        if (time.monotonic() - stamped_at) > self._ttl:
            self._entries.pop(host, None)
            return None
        return verdict

    def set(self, host: str, verdict: bool) -> None:
        """Record ``verdict`` for ``host``, evicting expired entries when full."""
        if self._ttl <= 0:
            return
        if len(self._entries) >= _MAX_CACHED_HOSTS:
            now = time.monotonic()
            self._entries = {
                k: v for k, v in self._entries.items() if (now - v[1]) <= self._ttl
            }
            if len(self._entries) >= _MAX_CACHED_HOSTS:
                self._entries.clear()
        self._entries[host] = (verdict, time.monotonic())

    def clear(self) -> None:
        self._entries.clear()


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
