# core/scraper/utils.py
"""Utility functions for the web scraper module.

Includes URL normalization, domain extraction, SSRF protection,
and robots.txt parsing.
"""

from __future__ import annotations

import re
from functools import lru_cache
from urllib.parse import urljoin, urlparse, urlunparse

from core.config.scraper import get_scraper_config
from core.security.ssrf import (
    SsrfError,
    SsrfPolicy,
    _resolve_safe_addresses,
    hostname_is_blocked_literal,
)
from core.security.ssrf import resolve_pinned_target as _core_pin


def normalize_url(url: str, base_url: str | None = None) -> str:
    """Normalize a URL for consistent comparison.

    Args:
        url: The URL to normalize.
        base_url: Optional base URL for resolving relative URLs.

    Returns:
        Normalized absolute URL.
    """
    # Handle relative URLs
    if base_url and not url.startswith(("http://", "https://", "//")):
        url = urljoin(base_url, url)
    elif url.startswith("//"):
        url = "https:" + url

    parsed = urlparse(url)

    # Ensure scheme
    if not parsed.scheme:
        parsed = urlparse("https://" + url)

    # Normalize path
    path = parsed.path or "/"
    if not path.startswith("/"):
        path = "/" + path

    # Remove trailing slash for non-root paths
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")

    # Remove default ports
    netloc = parsed.netloc
    if parsed.port == 80 and parsed.scheme == "http":
        netloc = parsed.hostname or ""
    elif parsed.port == 443 and parsed.scheme == "https":
        netloc = parsed.hostname or ""

    # Lowercase scheme and host
    return urlunparse(
        (
            parsed.scheme.lower(),
            netloc.lower(),
            path,
            "",  # params
            parsed.query,
            "",  # fragment is removed
        )
    )


def extract_domain(url: str) -> str:
    """Extract the domain from a URL.

    Args:
        url: The URL to extract domain from.

    Returns:
        The domain (e.g., 'example.com').
    """
    parsed = urlparse(url)
    return parsed.netloc.lower()


def is_same_domain(url1: str, url2: str) -> bool:
    """Check if two URLs are on the same domain.

    Args:
        url1: First URL.
        url2: Second URL.

    Returns:
        True if same domain.
    """
    return extract_domain(url1) == extract_domain(url2)


def is_valid_url(url: str) -> bool:
    """Check if a string is a valid URL.

    Args:
        url: The string to check.

    Returns:
        True if valid URL.
    """
    try:
        result = urlparse(url)
        return all([result.scheme in ("http", "https"), result.netloc])
    except Exception:
        return False


def _scraper_policy() -> SsrfPolicy:
    """Build the SSRF policy from the current scraper configuration."""
    config = get_scraper_config()
    return SsrfPolicy(allow_internal=not config.block_private_ips)


def is_private_ip(hostname: str) -> bool:
    """Check if a hostname is a known-private/internal literal.

    This is used for SSRF protection.

    Deprecated: this now delegates to the unified core SSRF module
    (:func:`core.security.ssrf.hostname_is_blocked_literal`), kept only for
    backward compatibility with importers of this module's legacy API. It is
    a cheap, offline, literal-only check (no DNS resolution) — pair with
    :func:`resolve_safe_ips`, or prefer :func:`check_ssrf_safe` /
    :func:`get_pinned_url_for_host` directly, for the authoritative,
    DNS-resolving check.

    Args:
        hostname: The hostname to check.

    Returns:
        True if private/internal IP.
    """
    return hostname_is_blocked_literal(hostname)


def resolve_safe_ips(hostname: str) -> list[str]:
    """Resolve a hostname to IP addresses, returning only public ones.

    Returns an empty list if any resolved IP is private/internal, or if
    DNS resolution fails.  Callers must treat an empty result as "blocked".

    Deprecated: this now delegates to the unified core SSRF resolver
    (``core.security.ssrf._resolve_safe_addresses``), kept only for backward
    compatibility with importers of this module's legacy API. The core
    resolver additionally blocks CGNAT (100.64.0.0/10) and the deprecated
    6to4 relay anycast range, which the original implementation did not.

    Args:
        hostname: The hostname to resolve.

    Returns:
        List of resolved public IP address strings, or [] if unsafe/unresolvable.
    """
    try:
        return _resolve_safe_addresses(hostname)
    except SsrfError:
        return []


def check_ssrf_safe(url: str) -> bool:
    """Check if a URL is safe from SSRF attacks.

    Validates both the literal hostname and all DNS-resolved IPs.  Returns
    False if *any* resolved address is private/internal.

    Delegates to the unified core SSRF module
    (:func:`core.security.ssrf.resolve_pinned_target`), honoring the
    scraper's ``block_private_ips`` setting via :func:`_scraper_policy`.

    Note on DNS rebinding: this check resolves DNS at validation time.
    To prevent rebinding attacks the caller should pin the connection to the
    IP returned by :func:`get_pinned_url_for_host` rather than letting the
    HTTP client re-resolve the hostname independently.

    Args:
        url: The URL to check.

    Returns:
        True if safe, False if potentially dangerous.
    """
    try:
        _core_pin(url, _scraper_policy())
        return True
    except SsrfError:
        return False


def get_pinned_url_for_host(url: str) -> tuple[str, str] | None:
    """Resolve the URL hostname to a pinned IP to prevent DNS rebinding.

    Returns a (pinned_url, original_host) tuple where *pinned_url* has the
    hostname replaced by the first safe resolved IP, and *original_host* is
    the original hostname to use as the HTTP ``Host`` header.

    Returns None if the URL is not SSRF-safe.

    Delegates to the unified core SSRF module
    (:func:`core.security.ssrf.resolve_pinned_target`), honoring the
    scraper's ``block_private_ips`` setting via :func:`_scraper_policy`.

    Usage with httpx::

        result = get_pinned_url_for_host(url)
        if result is None:
            raise ValueError("SSRF check failed")
        pinned_url, host = result
        async with httpx.AsyncClient() as client:
            resp = await client.get(pinned_url, headers={"Host": host})

    Args:
        url: The original URL.

    Returns:
        (pinned_url, original_host) or None if unsafe.
    """
    try:
        return _core_pin(url, _scraper_policy())
    except SsrfError:
        return None


def is_blocked_extension(url: str) -> bool:
    """Check if URL points to a blocked file type.

    Args:
        url: The URL to check.

    Returns:
        True if blocked extension.
    """
    config = get_scraper_config()
    parsed = urlparse(url)
    path = parsed.path.lower()

    for ext in config.blocked_extensions:
        if path.endswith(ext):
            return True
    return False


def clean_text(text: str) -> str:
    """Clean and normalize extracted text.

    Args:
        text: Raw text to clean.

    Returns:
        Cleaned text.
    """
    if not text:
        return ""

    # Normalize whitespace
    text = re.sub(r"\s+", " ", text)

    # Remove leading/trailing whitespace
    text = text.strip()

    return text


@lru_cache(maxsize=100)
def parse_robots_txt(
    robots_content: str, user_agent: str = "*"
) -> dict[str, list[str]]:
    """Parse robots.txt content.

    Args:
        robots_content: The robots.txt file content.
        user_agent: The user agent to match rules for.

    Returns:
        Dict with 'allow' and 'disallow' lists.
    """
    result: dict[str, list[str]] = {"allow": [], "disallow": []}

    current_agent = None
    ua_lower = user_agent.lower()

    for line in robots_content.split("\n"):
        line = line.strip()

        # Skip comments and empty lines
        if not line or line.startswith("#"):
            continue

        # Parse directive
        if ":" not in line:
            continue

        directive, value = line.split(":", 1)
        directive = directive.strip().lower()
        value = value.strip()

        if directive == "user-agent":
            current_agent = value.lower()
        elif current_agent in ("*", ua_lower):
            if directive == "disallow" and value:
                result["disallow"].append(value)
            elif directive == "allow" and value:
                result["allow"].append(value)

    return result


def is_url_allowed_by_robots(url: str, robots_rules: dict[str, list[str]]) -> bool:
    """Check if a URL is allowed by robots.txt rules.

    Args:
        url: The URL to check.
        robots_rules: Parsed robots.txt rules.

    Returns:
        True if allowed.
    """
    parsed = urlparse(url)
    path = parsed.path or "/"

    # Check allow rules first (they take precedence)
    for pattern in robots_rules.get("allow", []):
        if path.startswith(pattern):
            return True

    # Check disallow rules
    for pattern in robots_rules.get("disallow", []):
        if path.startswith(pattern):
            return False

    return True
