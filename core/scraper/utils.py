# core/scraper/utils.py
"""Utility functions for the web scraper module.

Includes URL normalization, domain extraction, SSRF protection,
and robots.txt parsing.
"""

from __future__ import annotations

import ipaddress
import re
from functools import lru_cache
from urllib.parse import urljoin, urlparse, urlunparse

from core.config.scraper import get_scraper_config


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


def is_private_ip(hostname: str) -> bool:
    """Check if a hostname resolves to a private/internal IP.

    This is used for SSRF protection.

    Args:
        hostname: The hostname to check.

    Returns:
        True if private/internal IP.
    """
    # Common private hostnames
    private_hostnames = {
        "localhost",
        "127.0.0.1",
        "0.0.0.0",  # nosec B104
        "::1",
        "[::1]",
    }

    if hostname.lower() in private_hostnames:
        return True

    # Check if it's an IP address
    try:
        ip = ipaddress.ip_address(hostname.strip("[]"))
        return ip.is_private or ip.is_loopback or ip.is_reserved or ip.is_link_local
    except ValueError:
        pass

    # Check for private network patterns
    private_patterns = [
        r"^10\.",
        r"^172\.(1[6-9]|2[0-9]|3[0-1])\.",
        r"^192\.168\.",
        r"^169\.254\.",
        r"\.local$",
        r"\.internal$",
        r"\.localhost$",
    ]

    for pattern in private_patterns:
        if re.search(pattern, hostname, re.IGNORECASE):
            return True

    return False


def check_ssrf_safe(url: str) -> bool:
    """Check if a URL is safe from SSRF attacks.

    Args:
        url: The URL to check.

    Returns:
        True if safe, False if potentially dangerous.
    """
    config = get_scraper_config()
    if not config.block_private_ips:
        return True

    try:
        parsed = urlparse(url)
        hostname = parsed.hostname or ""
        if not hostname:
            return False

        if is_private_ip(hostname):
            return False

        import socket

        try:
            addrinfo = socket.getaddrinfo(hostname, None)
            for result in addrinfo:
                ip_addr = result[4][0]
                # Check resolved IPs to prevent DNS rebinding
                if is_private_ip(str(ip_addr)):
                    return False
        except socket.gaierror:
            return False

        return True
    except Exception:
        return False


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
